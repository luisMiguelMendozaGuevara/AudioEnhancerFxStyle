"""Medición de sonoridad conforme a ITU-R BS.1770 y EBU R 128 ('EBU Mode').

Motivo: el "Leveling" del proyecto normaliza por RMS, y el RMS no es sonoridad
percibida. Dos pistas con el mismo RMS pueden diferir 6 LU o más según su
contenido espectral: el oído pesa distinto las frecuencias (por eso BS.1770
aplica el filtro K antes de medir) y la energía de los graves molesta mucho
menos que la de los medios.

Qué implementa (y contra qué está validado en tests/test_loudness.py):
- Filtro K de dos etapas (shelf + paso alto RLB) según BS.1770.
- Sonoridad MOMENTÁNEA (M): ventana rectangular deslizante de 400 ms, sin gate.
- Sonoridad de CORTO PLAZO (S): ventana de 3 s, sin gate.
- Sonoridad INTEGRADA (I): bloques de 400 ms con 75 % de solape y doble gate
  (−70 LUFS absoluto, −10 LU relativo), tal como EBU Tech 3341 §2.3.
- Rango de sonoridad (LRA): percentiles 10/95 de la sonoridad de corto plazo
  con gate relativo de −20 LU (EBU Tech 3342).

Los valores esperados de los tests NO son inventados: son los de la Tabla 1 de
EBU Tech 3341-2023 (casos 1 a 5), que es el material de conformidad oficial
para medidores 'EBU Mode'.

Todo el módulo es streaming y de memoria acotada: no hay que guardar el audio,
solo las energías por bloque. El coste por bloque son dos biquads por canal
(scipy.signal.sosfilt con estado) más acumulación de cuadrados.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any

import numpy as np

from .constants import SAMPLE_RATE

# scipy se importa la primera vez que se usa (no al importar el módulo): el
# arranque de la app y el CI de Linux no deben pagar scipy por importar esto.
_SIGNAL: Any = None


def _scipy_signal() -> Any:
    """Módulo scipy.signal, importado y cacheado en la primera llamada."""
    global _SIGNAL
    if _SIGNAL is None:
        from scipy import signal as _sig

        _SIGNAL = _sig
    return _SIGNAL

# Constante de la ecuación 2 de BS.1770: el nivel de referencia se define de
# forma que un seno de 1 kHz con pico a -23 dBFS en L y R (en fase) mida
# exactamente -23.0 LUFS (EBU Tech 3341, caso 1).
LOUDNESS_OFFSET_DB = -0.691

# Umbrales del gate (EBU Tech 3341 §2.3 y BS.1770).
GATE_ABSOLUTE_LUFS = -70.0
GATE_RELATIVE_LU = -10.0
# Gate relativo del rango de sonoridad (EBU Tech 3342).
LRA_GATE_RELATIVE_LU = -20.0

# Ventanas de 'EBU Mode' (segundos).
MOMENTARY_WINDOW_S = 0.4
SHORT_TERM_WINDOW_S = 3.0
# Paso de actualización: 100 ms => 75 % de solape en bloques de 400 ms.
HOP_S = 0.1

# Pesos por canal de BS.1770 (G_i). El LFE se excluye (no ponderado): EBU
# Tech 3341 §2.10 lo prohíbe explícitamente en un medidor 'EBU Mode'.
_CHANNEL_WEIGHTS = (1.0, 1.0, 1.0, 1.41, 1.41)


def k_weighting_sos(sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Sección SOS (2 etapas) del filtro K de BS.1770 a la tasa indicada.

    Etapa 1: shelf de altura +4 dB centrado en 1681.97 Hz (modela la cabeza).
    Etapa 2: paso alto RLB a 38.14 Hz con Q 0.5003 (ruido de sala).
    Los coeficientes se recalculan desde las frecuencias y Q del estándar, así
    que el módulo vale para 44,1 / 48 / 96 kHz sin tablas cableadas. A 48 kHz
    coinciden dígito a dígito con la tabla del anexo 1 de BS.1770-4 (hay un test
    que lo comprueba), incluida la NO normalización del numerador de la etapa 2.
    """
    fs = float(sample_rate)
    if not math.isfinite(fs) or fs <= 0:
        fs = float(SAMPLE_RATE)

    # --- Etapa 1: high shelf ---
    f0 = 1681.974450955533
    ganancia_db = 3.999843853973347
    q1 = 0.7071752369554196
    k = math.tan(math.pi * f0 / fs)
    vh = 10.0 ** (ganancia_db / 20.0)
    vb = vh**0.4996667741545416
    a0 = 1.0 + k / q1 + k * k
    shelf = (
        (vh + vb * k / q1 + k * k) / a0,
        2.0 * (k * k - vh) / a0,
        (vh - vb * k / q1 + k * k) / a0,
        2.0 * (k * k - 1.0) / a0,
        (1.0 - k / q1 + k * k) / a0,
    )

    # --- Etapa 2: paso alto (RLB) ---
    # OJO: la tabla oficial de BS.1770-4 (anexo 1) NO normaliza el numerador:
    # usa b = [1, -2, 1] con los ``a`` ya divididos por a0. Normalizar también
    # el numerador (la convención DSP habitual) resta 20*log10(a0) = 0.0435 dB
    # y desvía la medida exactamente 0.04 LU (verificado contra EBU 3341).
    f0_hp = 38.13547087602444
    q2 = 0.5003270373238773
    k2 = math.tan(math.pi * f0_hp / fs)
    a0_hp = 1.0 + k2 / q2 + k2 * k2
    highpass = (
        1.0,
        -2.0,
        1.0,
        2.0 * (k2 * k2 - 1.0) / a0_hp,
        (1.0 - k2 / q2 + k2 * k2) / a0_hp,
    )

    def _fila(coef: tuple[float, float, float, float, float]) -> list[float]:
        b0, b1, b2, a1, a2 = coef
        return [b0, b1, b2, 1.0, a1, a2]

    return np.asarray([_fila(shelf), _fila(highpass)], dtype=np.float64)


def energy_to_lufs(energy: float) -> float:
    """Sonoridad (LUFS) de una energía ponderada sumada de canales.

    ``energy`` es la suma de ``G_i * z_i`` de BS.1770 (z = media cuadrática de
    la señal ya filtrada con K). Un valor <= 0 es silencio: -inf LUFS.
    """
    if not math.isfinite(energy) or energy <= 0.0:
        return -math.inf
    return LOUDNESS_OFFSET_DB + 10.0 * math.log10(energy)


def loudness_gain_db(measured_lufs: float, target_lufs: float, max_db: float = 9.0) -> float:
    """Corrección de ganancia para llevar ``measured`` a ``target``, acotada.

    Devuelve 0.0 si no hay medida válida (silencio o NaN): un AGC que
    reaccionara a un -inf subiría la ganancia al máximo en cada pausa.
    """
    if not math.isfinite(measured_lufs) or not math.isfinite(target_lufs):
        return 0.0
    limite = abs(float(max_db))
    return max(-limite, min(limite, target_lufs - measured_lufs))


class LoudnessMeter:
    """Medidor de sonoridad en streaming ('EBU Mode': M, S, I y LRA).

    Uso:
        medidor = LoudnessMeter(sample_rate=48000, channels=2)
        for bloque in bloques:          # (N, 2) float32/float64
            medidor.process(bloque)
        medidor.snapshot()              # {'momentary_lufs', 'short_term_lufs',
                                        #  'integrated_lufs', 'lra_lu', ...}

    Notas de implementación:
    - Internamente acumula 100 ms de energía y de ahí construye ventanas
      deslizantes: M = últimos 4 tramos, S = últimos 30. No guarda audio.
    - La sonoridad integrada se recalcula con el doble gate cada vez que se
      cierra un tramo (EBU Tech 3341 §2.3: en un medidor en vivo se recalcula
      desde los valores almacenados).
    - Memoria acotada: los bloques del integrado se guardan en un array que
      crece hasta ``max_blocks`` (por defecto ~3 h a 10 bloques/s) y luego se
      descarta el más antiguo.
    """

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        channels: int = 2,
        hop_s: float = HOP_S,
        max_blocks: int = 108_000,
    ) -> None:
        self.sample_rate = int(sample_rate) if sample_rate else SAMPLE_RATE
        self.channels = max(1, min(int(channels), len(_CHANNEL_WEIGHTS)))
        self.hop_frames = max(1, int(round(hop_s * self.sample_rate)))
        self._sos = k_weighting_sos(self.sample_rate)
        weights = np.asarray(_CHANNEL_WEIGHTS[: self.channels], dtype=np.float64)
        self._weights = weights
        self._momentary_hops = max(1, int(round(MOMENTARY_WINDOW_S / hop_s)))
        self._short_term_hops = max(1, int(round(SHORT_TERM_WINDOW_S / hop_s)))
        self._max_blocks = max(self._momentary_hops, int(max_blocks))
        # Energías por tramo: z_i por canal, sin ponderar por G_i (los pesos se
        # aplican al agregar). Así el mismo tramo sirve para M, S e I.
        self._hops: deque[np.ndarray] = deque(maxlen=self._short_term_hops)
        # Bloques de 400 ms (energía ponderada): array que crece y luego se
        # desplaza por mitades. Un deque de Python obligaría a convertir a array
        # en cada snapshot (~5 ms tras una hora de sesión); así el snapshot es
        # O(1) y solo se recalcula cuando entra un bloque nuevo.
        self._block_energy = np.empty(min(self._max_blocks, 4096), dtype=np.float64)
        self._block_n = 0
        # Sonoridad de corto plazo por posición (la usa el LRA), también en
        # array acotado y actualizada de forma incremental.
        self._st_capacity = max(1, min(self._max_blocks // max(1, self._momentary_hops), 36_000))
        self._st_energy = np.empty(self._st_capacity, dtype=np.float64)
        self._st_n = 0
        self._cache_key: tuple[int, int] = (-1, -1)
        self._cache: dict[str, float] = {}
        self._zi: np.ndarray | None = None
        self._acc = np.zeros(self.channels, dtype=np.float64)
        self._acc_n = 0
        self.reset()

    # ---------- ciclo de vida ----------

    def reset(self) -> None:
        """Reinicia medidas (equivale al 'reset' de un medidor EBU)."""
        self._hops.clear()
        self._reiniciar_acumuladores()
        self._acc = np.zeros(self.channels, dtype=np.float64)
        self._acc_n = 0
        # Estado del filtro K: ceros => el primer bloque no arrastra transitorio
        # de una medición anterior (p. ej. tras cambiar de dispositivo).
        self._zi = np.zeros((2, self.channels, 2), dtype=np.float64)

    def reset_integrated(self) -> None:
        """Reinicia solo el integrado y el LRA (M y S siguen midiendo)."""
        self._reiniciar_acumuladores()

    def _reiniciar_acumuladores(self) -> None:
        """Vacía bloques integrados y serie de corto plazo, e invalida la caché."""
        self._block_n = 0
        self._st_n = 0
        self._cache_key = (-1, -1)
        self._cache = {}

    # ---------- medida ----------

    def process(self, block: np.ndarray) -> None:
        """Procesa un bloque de audio ``(N, canales)`` y actualiza las medidas."""
        if block is None or block.size == 0:
            return
        datos = np.asarray(block, dtype=np.float64)
        if datos.ndim == 1:
            datos = datos[:, None]
        if datos.shape[1] != self.channels:
            # Monofónico -> duplicado; más canales -> se recortan a los medidos.
            datos = (
                np.repeat(datos, self.channels, axis=1) if datos.shape[1] == 1 else datos[:, : self.channels]
            )
        # Filtro K en una sola llamada C (dos secciones, estado por canal).
        filtrado, self._zi = _scipy_signal().sosfilt(self._sos, datos, axis=0, zi=self._zi)
        cuadrado = filtrado * filtrado
        pos = 0
        total = cuadrado.shape[0]
        while pos < total:
            falta = self.hop_frames - self._acc_n
            trozo = cuadrado[pos : pos + falta]
            self._acc += trozo.sum(axis=0)
            self._acc_n += trozo.shape[0]
            pos += trozo.shape[0]
            if self._acc_n >= self.hop_frames:
                self._cerrar_tramo()

    def _cerrar_tramo(self) -> None:
        """Cierra un tramo de 100 ms y actualiza M, S, I y LRA."""
        energia = self._acc / float(self._acc_n)  # z_i medio del tramo
        self._acc = np.zeros(self.channels, dtype=np.float64)
        self._acc_n = 0
        self._hops.append(energia)
        # Bloque de 400 ms (solo cuando hay ventana completa).
        if len(self._hops) >= self._momentary_hops:
            reciente = list(self._hops)[-self._momentary_hops :]
            bloque = float(np.mean(np.asarray(reciente), axis=0) @ self._weights)
            self._empujar_bloque(bloque)
        # Serie de corto plazo: una entrada por posición con ventana de 3 s.
        if len(self._hops) >= self._short_term_hops:
            tramos = list(self._hops)
            self._empujar_st(float(np.mean(np.asarray(tramos), axis=0) @ self._weights))

    def _empujar_bloque(self, energia: float) -> None:
        """Añade un bloque de 400 ms al integrado (memoria acotada)."""
        if self._block_n == self._block_energy.size:
            if self._block_energy.size < self._max_blocks:
                nuevo = np.empty(min(self._max_blocks, self._block_energy.size * 2), dtype=np.float64)
                nuevo[: self._block_n] = self._block_energy[: self._block_n]
                self._block_energy = nuevo
            else:
                # Desplazar la mitad: amortizado (una vez cada miles de bloques).
                mitad = self._block_n // 2
                self._block_energy[:mitad] = self._block_energy[mitad : self._block_n]
                self._block_n = mitad
        self._block_energy[self._block_n] = energia
        self._block_n += 1

    def _empujar_st(self, energia: float) -> None:
        """Añade una posición de sonoridad de corto plazo (memoria acotada)."""
        if self._st_n == self._st_energy.size:
            mitad = self._st_n // 2
            self._st_energy[:mitad] = self._st_energy[mitad : self._st_n]
            self._st_n = mitad
        self._st_energy[self._st_n] = energia
        self._st_n += 1

    # ---------- lecturas ----------

    def _ventana_energia(self, tramos: int) -> float:
        if len(self._hops) < tramos:
            return -1.0  # sin ventana completa: medida no válida
        reciente = list(self._hops)[-tramos:]
        return float(np.mean(np.asarray(reciente), axis=0) @ self._weights)

    @property
    def momentary_lufs(self) -> float:
        """Sonoridad momentánea (400 ms), o -inf si aún no hay ventana."""
        return energy_to_lufs(self._ventana_energia(self._momentary_hops))

    @property
    def short_term_lufs(self) -> float:
        """Sonoridad de corto plazo (3 s), o -inf si aún no hay ventana."""
        return energy_to_lufs(self._ventana_energia(self._short_term_hops))

    @property
    def integrated_lufs(self) -> float:
        """Sonoridad integrada con doble gate (-70 LUFS absoluto, -10 LU relativo)."""
        self._actualizar_cache()
        return self._cache["integrated_lufs"]

    def _integradas(self) -> tuple[float, float]:
        """Calcula (integrada, LRA) sobre las series actuales."""
        integrada = -math.inf
        energias = self._block_energy[: self._block_n]
        if energias.size:
            absolutos = energias[energias > 0.0]
            if absolutos.size:
                niveles = LOUDNESS_OFFSET_DB + 10.0 * np.log10(np.maximum(absolutos, 1e-30))
                # Gate absoluto (-70 LUFS) y después relativo (-10 LU).
                pasa_abs = niveles > GATE_ABSOLUTE_LUFS
                if pasa_abs.any():
                    energia_abs = float(np.mean(absolutos[pasa_abs]))
                    umbral_rel = energy_to_lufs(energia_abs) + GATE_RELATIVE_LU
                    pasa_rel = niveles > umbral_rel
                    if pasa_rel.any():
                        integrada = energy_to_lufs(float(np.mean(absolutos[pasa_rel])))
        return integrada, self._lra()

    def _lra(self) -> float:
        """LRA: percentiles 10/95 de la sonoridad de corto plazo (gate -20 LU)."""
        energia = self._st_energy[: self._st_n]
        if energia.size < 2:
            return 0.0
        niveles = LOUDNESS_OFFSET_DB + 10.0 * np.log10(np.maximum(energia, 1e-30))
        pasa_abs = niveles > GATE_ABSOLUTE_LUFS
        if not pasa_abs.any():
            return 0.0
        umbral_rel = energy_to_lufs(float(np.mean(energia[pasa_abs]))) + LRA_GATE_RELATIVE_LU
        niveles = niveles[niveles > umbral_rel]
        if niveles.size < 2:
            return 0.0
        bajo, alto = np.percentile(niveles, [10.0, 95.0])
        return float(max(0.0, alto - bajo))

    def _actualizar_cache(self) -> None:
        """Recalcula integrada/LRA solo si han entrado tramos nuevos.

        La UI llama a snapshot() a 10 Hz; sin esta caché los percentiles del LRA
        se recalcularían dos veces por segundo sobre toda la sesión.
        """
        clave = (self._block_n, self._st_n)
        if clave == self._cache_key:
            return
        integrada, lra = self._integradas()
        self._cache = {"integrated_lufs": integrada, "lra_lu": lra}
        self._cache_key = clave

    @property
    def loudness_range_lu(self) -> float:
        """Rango de sonoridad (LRA) en LU, con gate relativo de -20 LU."""
        self._actualizar_cache()
        return self._cache["lra_lu"]

    def snapshot(self) -> dict[str, float]:
        """Medidas actuales como dict (lista para telemetría/UI)."""
        self._actualizar_cache()
        return {
            "momentary_lufs": self.momentary_lufs,
            "short_term_lufs": self.short_term_lufs,
            "integrated_lufs": self._cache["integrated_lufs"],
            "lra_lu": self._cache["lra_lu"],
        }

    def __len__(self) -> int:
        """Número de bloques de 400 ms medidos (los que usa el integrado)."""
        return self._block_n

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuración
        datos: Any = self.snapshot()
        return "LoudnessMeter(%d Hz, %d ch, %r)" % (self.sample_rate, self.channels, datos)
