"""Diagnóstico de latencia y de tiempos de procesado del camino de audio.

Módulo puro de la stdlib (sin numpy, sin Qt y sin PyAudio) para poder
importarlo desde la interfaz, desde el proceso hijo del DSP y desde los tests
sin coste de arranque. Expone dos utilidades independientes:

* :class:`RollingStats` — percentiles (p50/p95/p99/max/media) de una serie de
  duraciones, con ventana deslizante de tamaño fijo.
* :class:`LatencyBreakdown` — desglose en milisegundos del camino de audio:
  bloque de captura, colchón del ring, cola cruda del proceso hijo y bloque de
  salida.

Convenio de percentiles: se usa **interpolación lineal entre rangos contiguos**
(método R-7, el mismo que ``numpy.percentile`` por defecto). Es determinista
para un conjunto de datos dado, no depende del orden de llegada y no necesita
dependencias externas. Se documenta aquí porque el resto del proyecto no tiene
una convención previa y el resultado tiene que ser reproducible en los tests.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping

# Tasa de respaldo y suelo de seguridad: una tasa inválida (0, negativa o NaN)
# no puede provocar ZeroDivisionError ni valores infinitos, así que se acota.
_TASA_POR_DEFECTO = 48000.0
_TASA_MINIMA = 1.0

# Claves del snapshot, en el orden en que se publican.
_CLAVES_SNAPSHOT = ("p50_ms", "p95_ms", "p99_ms", "max_ms", "mean_ms")


def _as_float(value: object, default: float = 0.0) -> float:
    """Convierte a float de forma defensiva: ``default`` si no es finito o no es número."""
    if not isinstance(value, float | int):
        return default
    try:
        numero = float(value)
    except OverflowError:  # enteros gigantes (contadores corruptos) no deben romper nada
        return default
    return numero if math.isfinite(numero) else default


def _as_non_negative(value: object) -> float:
    """Igual que :func:`_as_float` pero acotando los negativos a 0."""
    numero = _as_float(value)
    return numero if numero > 0.0 else 0.0


def _percentile_ms(values: list[float], percent: float) -> float:
    """Percentil (0-100) de una lista YA ordenada de segundos, devuelto en ms.

    Interpolación lineal entre los dos rangos contiguos que rodean la posición
    ``(n - 1) * percent / 100`` (método R-7). Con una sola muestra devuelve esa
    muestra, que es el comportamiento esperado de un p99 con un único bloque.
    """
    posicion = (len(values) - 1) * (percent / 100.0)
    if posicion <= 0.0:
        return values[0] * 1000.0
    if posicion >= len(values) - 1:
        return values[-1] * 1000.0
    inferior = int(math.floor(posicion))
    fraccion = posicion - inferior
    return (values[inferior] + (values[inferior + 1] - values[inferior]) * fraccion) * 1000.0


class RollingStats:
    """Estadística de una serie de duraciones con ventana deslizante acotada.

    La ventana guarda como máximo ``window`` muestras (memoria constante) y
    ``add`` es O(1): el ordenamiento se paga solo en :meth:`snapshot`, y sobre
    una copia, nunca sobre la estructura viva.

    Concurrencia: la clase no usa locks a propósito, porque el productor de
    muestras corre en el callback de audio y no puede bloquearse. ``snapshot``
    copia la ventana y ordena esa copia, así que el peor caso es observar un
    fotograma ligeramente desfasado, nunca corromper la serie. El consumidor
    debe llamar a ``snapshot`` con una cadencia razonable (telemetría de ~1 Hz):
    el coste es O(window · log window).
    """

    def __init__(self, window: int = 512) -> None:
        # Una ventana <= 0 convertiría el deque en un objeto inservible: se acota a 1.
        self._window = max(1, int(_as_non_negative(window)))
        self._samples: deque[float] = deque(maxlen=self._window)
        self._count = 0

    def add(self, seconds: float) -> None:
        """Registra una duración en segundos.

        Las duraciones negativas (relojes que retroceden) se acotan a 0 y los
        valores no finitos (NaN/inf) se descartan para no envenenar los
        percentiles; en ambos casos la serie sigue siendo utilizable.
        """
        if not isinstance(seconds, float | int):
            return
        try:
            valor = float(seconds)
        except OverflowError:  # reloj corrupto: mejor descartar la muestra que romper la serie
            return
        if not math.isfinite(valor):
            return
        self._samples.append(valor if valor > 0.0 else 0.0)
        self._count += 1

    def add_ms(self, milliseconds: float) -> None:
        """Conveniencia: igual que :meth:`add` pero recibiendo milisegundos."""
        self.add(milliseconds / 1000.0)

    def clear(self) -> None:
        """Vacía la ventana y reinicia el histórico."""
        self._samples.clear()
        self._count = 0

    @property
    def count(self) -> int:
        """Muestras registradas desde el último :meth:`clear` (histórico, no la ventana)."""
        return self._count

    def snapshot(self) -> dict[str, float]:
        """Percentiles de la ventana actual en MILISEGUNDOS.

        Claves: ``p50_ms``, ``p95_ms``, ``p99_ms``, ``max_ms`` y ``mean_ms``.
        Sin muestras, todas valen 0.0.
        """
        datos = sorted(self._copiar_muestras())
        if not datos:
            return dict.fromkeys(_CLAVES_SNAPSHOT, 0.0)
        return {
            "p50_ms": _percentile_ms(datos, 50.0),
            "p95_ms": _percentile_ms(datos, 95.0),
            "p99_ms": _percentile_ms(datos, 99.0),
            "max_ms": datos[-1] * 1000.0,
            "mean_ms": math.fsum(datos) * 1000.0 / len(datos),
        }

    def _copiar_muestras(self) -> list[float]:
        """Copia la ventana tolerando una mutación concurrente del deque.

        ``list(deque)`` lanza ``RuntimeError`` si otro hilo hace ``append``
        durante la iteración; se reintenta un par de veces y, como último
        recurso, se indexa (el deque solo crece por la derecha hasta su tamaño
        máximo, así que los índices calculados siguen siendo válidos).
        """
        for _ in range(3):
            try:
                return list(self._samples)
            except RuntimeError:  # deque mutado durante la iteración
                continue
        return [self._samples[indice] for indice in range(len(self._samples))]


class LatencyBreakdown:
    """Desglose de la latencia del pipeline en milisegundos.

    Etapas, en el orden real del camino de audio:

    * ``capture_block``: bloque que entrega el callback de captura
      (``block_frames / sample_rate``).
    * ``ring``: colchón acumulado en el ring buffer
      (``ring_fill_frames / sample_rate``).
    * ``child_backlog``: cola cruda que aún no ha consumido el proceso hijo
      (``backlog_blocks * block_frames / rate``).
    * ``output_block``: bloque que se escribe en la salida
      (``block_frames / sample_rate``).
    * ``total``: la suma de las cuatro etapas anteriores.

    Todos los cálculos sanean la entrada: los negativos se acotan a 0 y una
    tasa o un tamaño de bloque absurdos no provocan excepciones.
    """

    def __init__(self, sample_rate: int = 48000, block_frames: int = 1024) -> None:
        self._sample_rate = self._tasa_segura(sample_rate)
        self._block_frames = _as_non_negative(block_frames)

    @staticmethod
    def _tasa_segura(sample_rate: object) -> float:
        """Acota la tasa a un mínimo positivo para no dividir por cero."""
        tasa = _as_float(sample_rate, _TASA_POR_DEFECTO)
        return tasa if tasa >= _TASA_MINIMA else _TASA_MINIMA

    def compute(self, *, ring_fill_frames: int = 0, backlog_blocks: float = 0.0) -> dict[str, float]:
        """Devuelve el desglose en ms; nunca lanza y nunca devuelve valores absurdos.

        Claves: ``capture_block_ms``, ``ring_ms``, ``child_backlog_ms``,
        ``output_block_ms`` y ``total_ms``.
        """
        tasa = self._sample_rate
        bloque_frames = self._block_frames
        relleno_frames = _as_non_negative(ring_fill_frames)
        cola_bloques = _as_non_negative(backlog_blocks)

        bloque_ms = bloque_frames / tasa * 1000.0
        relleno_ms = relleno_frames / tasa * 1000.0
        cola_ms = cola_bloques * bloque_frames / tasa * 1000.0
        return {
            "capture_block_ms": bloque_ms,
            "ring_ms": relleno_ms,
            "child_backlog_ms": cola_ms,
            "output_block_ms": bloque_ms,
            "total_ms": bloque_ms + relleno_ms + cola_ms + bloque_ms,
        }

    @staticmethod
    def format(breakdown: dict[str, float]) -> str:
        """Línea de una sola fila con el desglose; tolera claves ausentes."""
        bloque = _field_ms(breakdown, "capture_block_ms")
        relleno = _field_ms(breakdown, "ring_ms")
        cola = _field_ms(breakdown, "child_backlog_ms")
        salida = _field_ms(breakdown, "output_block_ms")
        total = _field_ms(breakdown, "total_ms")
        linea = (
            f"bloque {bloque:.1f} | ring {relleno:.1f} | cola hijo {cola:.1f} | "
            f"salida {salida:.1f} | total {total:.1f} ms"
        )
        return linea


def _field_ms(values: Mapping[str, float], key: str) -> float:
    """Lee una etapa del desglose en ms; 0.0 si falta la clave o no es numérica."""
    return _as_float(values.get(key, 0.0))


def latency_breakdown(
    sample_rate: int = 48000,
    block_frames: int = 1024,
    ring_fill_frames: int = 0,
    backlog_blocks: float = 0.0,
) -> dict[str, float]:
    """Atajo funcional de :meth:`LatencyBreakdown.compute` para usar en una línea."""
    breakdown = LatencyBreakdown(sample_rate=sample_rate, block_frames=block_frames)
    return breakdown.compute(ring_fill_frames=ring_fill_frames, backlog_blocks=backlog_blocks)


def format_stats(stats: dict[str, float]) -> str:
    """Resumen de una fila de un snapshot de :class:`RollingStats`.

    Formato: ``'p50 1.2 ms | p95 2.0 | p99 2.4 | max 3.1'``. Tolera claves
    ausentes (se muestran como 0.0) para que la telemetría nunca rompa la UI.
    """
    p50 = _field_ms(stats, "p50_ms")
    p95 = _field_ms(stats, "p95_ms")
    p99 = _field_ms(stats, "p99_ms")
    maximo = _field_ms(stats, "max_ms")
    linea = f"p50 {p50:.1f} ms | p95 {p95:.1f} | p99 {p99:.1f} | max {maximo:.1f}"
    return linea
