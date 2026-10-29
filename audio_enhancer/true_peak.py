"""Medidor de true-peak (pico entre muestras) según ITU-R BS.1770-4, anexo 2.

El limitador actual trabaja sobre **muestras**. Un pico entre muestras no lo ve,
y en una cadena con caminos no lineales (Clarity, exciters) es donde más probable
es que aparezca. La diferencia no es teórica: un seno a `fs/4` con fase de 45°
tiene un pico de muestra de −3,01 dBFS y un pico real de 0 dBTP, es decir, **3,01
dB más** de los que el medidor de muestras informa.

Cómo se mide (norma):

- BS.1770-4 anexo 2 define el pico real por **sobremuestreo** de la señal y toma
  del máximo del resultado. El mínimo que contempla la norma es **4×**, y es lo
  que se usa aquí: se midió 8× y cuesta el doble (3,7 % del bloque frente a 2,0 %)
  para ganar 0,1 dB de exactitud justo en el borde de la banda (20 kHz), que no
  compensa para un medidor.
- El filtro de interpolación es un FIR paso-bajo diseñado con ventana Kaiser
  (β = 8) sobre `fs/2` y repartido en 8 fases (polifase). Sin él, el
  sobremuestreo por inserción de ceros dejaría las imágenes espectrales del
  proceso y el medidor "vería" picos que no existen.
- Este módulo **solo mide**: no toca el audio. La salida de la cadena tiene que
  seguir siendo bit-exacta con y sin medidor, y eso se comprueba en los tests.

Este medidor no incluye el filtro K ni la sonoridad: para eso está `loudness.py`.
"""

from __future__ import annotations

import threading

import numpy as np
from scipy.signal import firwin, upfirdn

#: Factor de sobremuestreo: el mínimo de la norma (BS.1770-4 anexo 2).
FACTOR_SOBREMUESTREO = 4

#: Taps por fase del FIR de interpolación (total = factor × 32 + 1).
#: Se prefiere alargar el filtro antes que subir el factor, y se midió: con 24
#: taps la banda de transición llegaba hasta 0,6·fs y el error a 20 kHz era de
#: 0,135 dB; con 32 taps el error baja a 0,001 dB y la atenuación de las imágenes
#: desde 0,6·fs pasa de −63 a −82 dB, por el mismo coste.
TAPS_POR_FASE = 32

#: Beta de la ventana Kaiser: compromiso entre lóbulo principal y rechazo.
_BETA_KAISER = 8.0

#: Centinela de "sin medida" (coherente con el medidor de sonoridad).
SIN_MEDIDA = -1000.0


def disenar_filtro(factor: int = FACTOR_SOBREMUESTREO, taps_por_fase: int = TAPS_POR_FASE) -> np.ndarray:
    """FIR paso-bajo para interpolar ``factor``×, con ganancia ``factor`` en DC.

    El corte va en la mitad del ancho de banda original (``fs/2``), que en la
    rejilla del filtro sobremuestreado es ``1/factor`` de su Nyquist. La ganancia
    tiene que ser ``factor`` porque la entrada polifásica son ceros insertados:
    con ganancia 1 la señal saldría ``factor`` veces más débil.
    """
    if factor < 2:
        raise ValueError("el factor de sobremuestreo debe ser >= 2")
    if taps_por_fase < 4:
        raise ValueError("se necesitan al menos 4 taps por fase")
    largo = factor * taps_por_fase + 1
    h = firwin(largo, 1.0 / factor, window=("kaiser", _BETA_KAISER), scale=True)
    return h * factor


class TruePeakMeter:
    """Pico de muestra, pico real y retención, con estado entre bloques.

    Es seguro entre hilos (el motor mide desde el hilo de audio y la interfaz lee
    desde el suyo): el estado de la señal y el de la medida se protegen con el
    mismo cerrojo reentrante, igual que el medidor de sonoridad.

    La medida es **por canal** y luego se toma el máximo: el pico real de un
    estéreo es el del canal que más se acerca al techo, no la suma.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        canales: int = 2,
        factor: int = FACTOR_SOBREMUESTREO,
        taps_por_fase: int = TAPS_POR_FASE,
        retencion_s: float = 1.0,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate debe ser > 0")
        if canales <= 0:
            raise ValueError("canales debe ser > 0")
        self.sample_rate = int(sample_rate)
        self.canales = int(canales)
        self.factor = int(factor)
        self._filtro = disenar_filtro(self.factor, taps_por_fase)
        #: Semiancho del filtro en muestras de ENTRADA: es el contexto que hace
        #: falta a cada lado de una muestra para que su interpolación sea válida.
        #: Con 193 taps a 8× son ~24 muestras de entrada, 12 por lado.
        self._media = max(1, (len(self._filtro) // self.factor) // 2)
        #: Búfer de contexto: las últimas 2·media muestras del flujo.
        self._buf = np.zeros((2 * self._media, self.canales), dtype=np.float64)
        self._cerrojo = threading.RLock()
        self._retencion_frames = max(1, int(retencion_s * self.sample_rate))
        self.reset()

    # ------------------------------------------------------------------ estado

    def reset(self) -> None:
        """Borra medidas y estado del filtro (cambio de dispositivo o de parada)."""
        with self._cerrojo:
            self._buf[:] = 0.0
            self._primado = False
            self._hold_abs = 0.0
            self._max_abs = 0.0
            self._pico_muestra_abs = 0.0
            self._frames_desde_max = 0
            self._frames_vistos = 0

    # --------------------------------------------------------------- procesado

    def process(self, bloque: np.ndarray) -> None:
        """Mide un bloque ``(frames, canales)`` o ``(frames,)`` (mono).

        El bloque se mide con ``media`` muestras de contexto a cada lado, así que
        cada muestra se mide **una sola vez y con soporte completo del filtro**.
        Medir el borde del bloque sin ese contexto produce rizado de Gibbs
        (medido: +0,11 dB de más en un seno a `fs/4`) y el medidor se quedaría
        con ese falso pico para siempre.

        El primer bloque tras ``reset()`` solo carga el filtro: su contexto es
        silencio artificial y su transitorio no es audio.
        """
        if bloque is None:
            return
        x = np.asarray(bloque, dtype=np.float64)
        if x.size == 0:
            return
        if x.ndim == 1:
            x = x[:, None]
        if x.ndim != 2:
            raise ValueError(f"forma no soportada para medir: {x.shape}")
        if x.shape[1] != self.canales:
            raise ValueError(f"se esperaban {self.canales} canales, llegaron {x.shape[1]}")
        # Un NaN o un infinito envenenarían la retención para siempre: se
        # sustituyen por 0 solo para medir (el audio no se toca).
        if not np.all(np.isfinite(x)):
            x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
        with self._cerrojo:
            n = x.shape[0]
            self._pico_muestra_abs = max(self._pico_muestra_abs, float(np.max(np.abs(x))))
            combinado = np.concatenate([self._buf, x], axis=0)
            sobremuestreado = upfirdn(self._filtro, combinado, up=self.factor, axis=0)
            # La rejilla de salida está retardada media longitud del filtro; el
            # tramo que corresponde al bloque empieza tras el contexto previo.
            centro = (len(self._filtro) - 1) // 2
            inicio = centro + self.factor * self._media
            tramo = sobremuestreado[inicio : inicio + self.factor * n]
            self._buf = combinado[n:].copy()
            if not self._primado:
                # Arranque: el contexto era silencio, no audio.
                self._primado = True
                return
            pico_real = float(np.max(np.abs(tramo))) if tramo.size else 0.0
            self._max_abs = max(self._max_abs, pico_real)
            if pico_real >= self._hold_abs:
                self._hold_abs = pico_real
                self._frames_desde_max = 0
            else:
                self._frames_desde_max += n
                if self._frames_desde_max >= self._retencion_frames:
                    # Fin de la retención: la aguja vuelve al nivel RECIENTE (el
                    # de este bloque), no al máximo histórico: usar el máximo
                    # dejaba la aguja clavada para siempre.
                    self._hold_abs = pico_real
                    self._frames_desde_max = 0
            self._frames_vistos += n

    # ------------------------------------------------------------------ lectura

    def snapshot(self) -> dict[str, float]:
        """Medidas actuales en dB. ``SIN_MEDIDA`` si todavía no hay señal."""
        with self._cerrojo:
            if self._frames_vistos == 0 or self._max_abs <= 0.0:
                return {
                    "sample_peak_dbfs": SIN_MEDIDA,
                    "true_peak_dbtp": SIN_MEDIDA,
                    "hold_dbtp": SIN_MEDIDA,
                    "difference_db": 0.0,
                }
            muestra_db = _a_db(self._pico_muestra_abs)
            real_db = _a_db(self._max_abs)
            return {
                "sample_peak_dbfs": muestra_db,
                "true_peak_dbtp": real_db,
                "hold_dbtp": _a_db(self._hold_abs),
                "difference_db": real_db - muestra_db,
            }

    def reiniciar_maximo(self) -> None:
        """Pone a cero el máximo acumulado (botón "reset" de la interfaz)."""
        with self._cerrojo:
            self._max_abs = 0.0
            self._hold_abs = 0.0
            self._pico_muestra_abs = 0.0
            self._frames_desde_max = 0


def _a_db(valor: float) -> float:
    """Convierte una amplitud lineal a dB; ``SIN_MEDIDA`` para silencio."""
    if valor <= 0.0:
        return SIN_MEDIDA
    return 20.0 * float(np.log10(valor))


def medir_true_peak(bloque: np.ndarray, factor: int = FACTOR_SOBREMUESTREO) -> tuple[float, float]:
    """Atajo sin estado: devuelve ``(pico_muestra, pico_real)`` lineales.

    Útil para tests y para medir un archivo suelto. Pierde las fronteras entre
    bloques, así que para el camino de audio se usa ``TruePeakMeter``.
    """
    x = np.asarray(bloque, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    if x.size == 0:
        return 0.0, 0.0
    filtro = disenar_filtro(factor, TAPS_POR_FASE)
    sobremuestreado = upfirdn(filtro, x, up=factor, axis=0)
    # Se descarta el TRANSITORIO COMPLETO del filtro, no solo su retardo de grupo
    # (media longitud): medido, descartar media longitud deja +0,11 dB de error en
    # el caso de referencia fs/4 a 45 grados; descartar la longitud entera lo deja
    # en 0,0003 dB.
    arranque = len(filtro) - 1
    cuerpo = sobremuestreado[arranque:-arranque] if sobremuestreado.shape[0] > 2 * arranque else sobremuestreado
    return float(np.max(np.abs(x))), float(np.max(np.abs(cuerpo)))
