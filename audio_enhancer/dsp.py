"""Cadena DSP de Audio Enhancer FxStyle (puro numpy/scipy, sin UI).

``Enhancer`` procesa bloques de audio en el hilo de captura de PortAudio.
Thread-safety: el hilo de audio (callbacks) lee los *targets mientras la UI
escribe en volume/bass/treble/eq_gains/blend. Para evitar zipper noise y
estados corruptos, process() desliza los valores "actuales" (_c*) hacia los
objetivos con una rampa exponencial por bloque; los filtros se rediseñan cada
bloque con el valor suavizado. Nunca se escribe el estado del DSP desde el
callback.
"""

import dataclasses
import math
import threading
from functools import lru_cache

import numpy as np

from .constants import SAMPLE_RATE

# Import perezoso de scipy.signal: su importación cuesta ~2,3 s y solo se
# usa sosfilt() cuando hay secciones de filtro activas. La precarga en
# segundo plano (main.py) evita pagar el coste dentro del callback de audio.
_signal = None
_signal_lock = threading.Lock()


def _scipy_signal():
    global _signal
    if _signal is None:
        with _signal_lock:
            if _signal is None:
                from scipy import signal as _s

                _signal = _s
    return _signal


_upfirdn_cache: dict[int, tuple] = {}


def _true_peak_oversample(y: np.ndarray) -> np.ndarray:
    """Sobremuestreo x4 por el MISMO algoritmo que resample_poly, pero con el
    filtro FIR cacheado.

    ``scipy.signal.resample_poly`` rediseña el FIR (ventana Kaiser) en CADA
    llamada: perfilado del peor caso (full_hot) mostró que ese diseño, no el
    filtrado, es lo que dispara los picos de 20-30 ms/bloque → microcortes.
    Replicamos su diseño exacto (n=10·max_rate, kaiser 5.0) una sola vez por
    tasa y aplicamos con ``upfirdn``. Bit a bit idéntico a resample_poly
    (verificado) y ~2x más rápido."""
    sig = _scipy_signal()
    up, down = 4, 1
    entry = _upfirdn_cache.get(id(sig))  # la tasa no interviene: up/down fijos
    if entry is None:
        max_rate = max(up, down)
        half_len = 10 * max_rate
        filt = sig.firwin(2 * half_len + 1, 1.0 / max_rate, window=("kaiser", 5.0))
        pad = (len(filt) - 1) // 2
        entry = (filt * up, up, down, pad)
        _upfirdn_cache[id(sig)] = entry
    filt, up, down, pad = entry
    out = sig.upfirdn(filt, y, up, down, axis=0)
    # Mismo recorte que resample_poly (descartar los flancos del filtro).
    return out[pad : out.shape[0] - pad if pad else out.shape[0]]


_ndimage = None


def _scipy_ndimage():
    """scipy.ndimage perezoso (mismo motivo que scipy.signal: import caro).

    Se usa en el limitador true-peak: los filtros de máximo/mínimo 1-D son
    O(n) (van Herk) frente al O(n·ventana) de la ventana deslizante de numpy
    (ventana de 2·look-ahead+1 = 1153 muestras sobre 4x la señal)."""
    global _ndimage
    if _ndimage is None:
        with _signal_lock:
            if _ndimage is None:
                from scipy import ndimage as _nd

                _ndimage = _nd
    return _ndimage


# Histéresis Schmitt de activación de secciones de filtro (en dB): una
# sección ENTRA cuando |ganancia| >= SECTION_ENTER_DB y SALE cuando
# |ganancia| < SECTION_EXIT_DB. La banda muerta intermedia evita el parpadeo
# on/off (con sus chasquidos de estado de filtro) al rozar el umbral con un
# slider.
SECTION_ENTER_DB = 0.15
SECTION_EXIT_DB = 0.05

EQ_BANDS = [60, 150, 250, 500, 1000, 2000, 4000, 8000, 12000]


@lru_cache(maxsize=8)
def _smooth_kernel(k: int) -> np.ndarray:
    """Kernel Hann normalizado para el suavizado de ganancia del limitador.

    Cacheado (D1): k solo depende de la tasa, pero antes se alocaba y
    normalizaba en CADA bloque. El array devuelto no se muta (np.convolve no
    modifica sus entradas)."""
    kernel = np.hanning(k)
    kernel /= kernel.sum()
    return kernel


@lru_cache(maxsize=16)
def _ramp_falling(n: int, sample_rate: int, tau: float) -> np.ndarray:
    """Rampa exponencial descendente cacheada (solo lectura, nunca mutar).

    Misma recurrencia que lfilter, expresada directamente: ramp[k] = pole**k.
    La cache es un LRU acotado (16 entradas) en lugar de un dict sin límite:
    antes cada (n, sample_rate) nueva alocaba para siempre, y con dispositivos
    de tasas exóticas o bloques irregulares crecía sin control."""
    pole = float(np.exp(-1.0 / (sample_rate * tau)))
    steps = np.arange(1, n + 1, dtype=np.float32)
    return np.power(pole, steps).astype(np.float32, copy=False)


@dataclasses.dataclass(frozen=True)
class EnhancerParams:
    """Instantánea INMUTABLE de los parámetros controlados por la UI.

    La UI construye la instantánea (una sola asignación de tuplas/listas
    nuevas) y la entrega con ``Enhancer.apply_params``: el hilo de audio ve
    un conjunto coherente de valores, nunca una mezcla a medias de dos
    escrituras (el problema de fondo de la carrera H5/H6). Los valores son
    tipos inmutables o copias: la instantánea no puede mutarse después."""

    volume: float = 1.0
    bass: float = 0.0
    treble: float = 0.0
    eq_gains: tuple[float, ...] = ()
    limiter: bool = True
    compressor: bool = True
    true_peak: bool = True
    blend: float = 1.0
    crossfeed: bool = False
    crossfeed_preset: str = "Natural"


class Enhancer:
    """Cadena DSP: biquads RBJ (Low/High Shelf + Peaking) con sosfilt+zi,
    compresor RMS por muestra, limitador brickwall con look-ahead y suavizado
    de parámetros."""

    def __init__(self) -> None:
        self.sample_rate: int = SAMPLE_RATE
        self.eq_bands: list[float] = list(EQ_BANDS)
        self._eq_gains: list[float] = [0.0] * len(self.eq_bands)
        self.bass: float = 0.0
        self.treble: float = 0.0
        self.volume: float = 1.0
        self.bass_freq: float = 150.0  # corte del Low Shelf
        self.treble_freq: float = 6000.0  # corte del High Shelf
        # Q de las bandas peaking (Fase 3: EQ paramétrico extendido).
        # eq_q es la propiedad escalar de compatibilidad (leer/se-ear propaga
        # a todas las bandas); eq_q_values permite Q individual por banda.
        self._eq_q_default: float = 1.4
        self.eq_q_values: list[float] = [1.4] * len(self.eq_bands)
        # Limitador brickwall con look-ahead (seguridad a nivel final)
        self.limiter: bool = True
        self.limiter_threshold: float = 0.95  # techo (pico de salida)
        # Techo de seguridad para cuando el limitador musical está APAGADO:
        # sin él, el único techo de la cadena era np.clip (recorte duro:
        # muestras clavadas a +-1.0 = flat-top audible, ~62% del bloque con
        # volumen 2.0 sobre un seno a 0.9). 0.99 (~-0.09 dBFS) es transparente
        # y deja el np.clip final como guardia digital que en la práctica ya
        # no llega a actuar. Ajustable por quien quiera más margen.
        self.safety_ceiling: float = 0.99
        # Interruptor del techo de seguridad (página Efectos). Apagado, el
        # único tope es el recorte duro a ±1.0 al final de process().
        self.safety_ceiling_enabled: bool = True
        # Recorte duro final a ±1.0 (página Efectos). Apagado, la señal sale
        # SIN recortar (puede pasar de 1.0): el recorte queda en el driver.
        self.final_clip_enabled: bool = True
        # True-peak: envolvente medida sobre la señal 4x sobremuestreada
        # (detecta picos inter-muestra invisibles al pico por muestra; típico
        # con contenido cerca de Nyquist).
        self.true_peak: bool = True
        # Compresor RMS dinámico (loudness)
        self.compressor: bool = True
        self.comp_threshold: float = 0.85  # ~-1.4 dBFS
        self.comp_ratio: float = 4.0  # compresión 4:1
        self.comp_attack: float = 0.005  # segundos
        self.comp_release: float = 0.2  # segundos
        self.comp_makeup: float = 1.0
        # Umbral del compresor en dB: solo depende de comp_threshold, que la UI
        # cambia rara vez; se recalcula al detectar el cambio (evita un log10
        # escalar por bloque).
        self._thr_src: float = self.comp_threshold
        self._thr_db: float = 20.0 * math.log10(max(self.comp_threshold, 1e-9))
        # Estados del compresor por muestra (dual-envelope RMS): se crean
        # perezosamente en la primera llamada a _compress y persisten entre
        # bloques; reset_state() los libera.
        self._comp_zi_fast = None
        self._comp_zi_slow = None
        # Histéresis de secciones: clave -> bool (True = sección apilada).
        self._section_on: dict[str, bool] = {}
        # A/B crossfade: blend=1 efectos, blend=0 directo
        self.blend: float = 1.0
        # Crossfeed BS2B (para auriculares; OFF por defecto). Hace que el
        # estéreo se perciba más "fuera de la cabeza" mezclando cada canal en el
        # opuesto con un LPF + shelf (algoritmo de libbs2b: ver _crossfeed_coeffs).
        # Es una etapa aislada: no toca EQ/compresor/limitador.
        self.crossfeed: bool = False
        self.crossfeed_preset: str = "Natural"
        # Coeficientes y estados (zi) del crossfeed, recalculados al cambiar
        # preset/tasa; reset_state() los limpia.
        self._cf_coeffs: tuple | None = None
        self._cf_src: tuple | None = None  # (sample_rate, preset) de _cf_coeffs
        self._cf_state: np.ndarray | None = None  # zi de lfilter
        # Medidor. Por canal (L/R) y agregado: level_rms/level_peak son el
        # MAXIMO de ambos canales (compatibilidad con quien los lee como nivel
        # global); los _l/_r alimentan los medidores estéreo de la UI.
        self.level_rms: float = 0.0
        self.level_peak: float = 0.0
        self.level_rms_l: float = 0.0
        self.level_rms_r: float = 0.0
        self.level_peak_l: float = 0.0
        self.level_peak_r: float = 0.0
        # Reducción de ganancia del limitador (dB, <=0): feedback para la UI.
        self.level_gr: float = 0.0
        # Valores suavizados actuales (rampa anti-cremallera)
        self._c_vol: float = 1.0
        self._c_bass: float = 0.0
        self._c_treble: float = 0.0
        self._c_eq = np.zeros(len(self.eq_bands), dtype=np.float32)
        # (R3-B5) Objetivo numpy de las ganancias EQ, reconstruido SOLO en el
        # setter (hilo de UI): el callback ya no copia la lista ni aloc un
        # array por bloque. _eq_scratch es el buffer de trabajo de la rampa
        # in-place (tampoco aloc por bloque).
        self._eq_target = np.zeros(len(self.eq_bands), dtype=np.float32)
        self._eq_scratch = np.zeros(len(self.eq_bands), dtype=np.float32)
        self._c_blend: float = 1.0
        # Estados de filtros y analizador
        self._states: dict[str, np.ndarray] = {}
        self._sos_cache: dict[str, tuple] = {}
        self._channels: int | None = None
        self.spectrum: np.ndarray | None = None  # 64 barras (dB) para el canvas
        self.spectrum_enabled: bool = True
        self._snapshot: np.ndarray | None = None  # copia mono del último bloque
        self._win: np.ndarray | None = None
        self._win_n: int = 0
        self._spec_meta: tuple | None = None  # cache (n, sample_rate, idx)

    def reset_state(self) -> None:
        """Reinicia todo el estado dependiente de la señal.

        Imprescindible al cambiar de dispositivo o de tasa de muestreo: los
        estados zi de los biquads y del compresor corresponden a la señal
        previa y seguirían mezclando historial de otra tasa (artefactos al
        arrancar). También pone a cero rampas y medidores."""
        self._states = {}
        self._sos_cache = {}
        self._channels = None
        self._c_vol = 1.0
        self._c_bass = 0.0
        self._c_treble = 0.0
        self._c_eq = np.zeros(len(self.eq_bands), dtype=np.float32)
        self._c_blend = 1.0

        self._comp_zi_fast = None
        self._comp_zi_slow = None
        self._section_on = {}
        self.level_rms = 0.0
        self.level_peak = 0.0
        self.level_rms_l = 0.0
        self.level_rms_r = 0.0
        self.level_peak_l = 0.0
        self.level_peak_r = 0.0
        self.level_gr = 0.0
        self._cf_state = None
        self.spectrum = None
        self._snapshot = None
        self._spec_meta = None

    # ---------- diseño de filtros RBJ (Audio EQ Cookbook) ----------

    @property
    def eq_gains(self) -> list[float]:
        """Copia defensiva de las ganancias del EQ.

        La UI (hilo Qt) y process() (callback de audio) comparten este dato:
        devolver la lista VIVA permitía que un widget mutara `[i]` mientras el
        callback la leía (carrera y valores a medias). Ahora toda escritura
        pasa por el setter (reemplazo atómico de la lista) y toda lectura
        recibe una copia inmutable de facto."""
        return list(self._eq_gains)

    @eq_gains.setter
    def eq_gains(self, value) -> None:
        self._eq_gains = [float(v) for v in value]
        # (R3-B5) El objetivo numpy se recalcula aquí, fuera del callback: la
        # escritura llega por UI/preset, nunca por el hilo de audio. Si la
        # longitud cambiara (no ocurre hoy: la UI fija 9 bandas), la rampa se
        # reinicia a cero del nuevo tamaño para evitar un broadcasting roto.
        target = np.asarray(self._eq_gains, dtype=np.float32)
        if target.shape != self._c_eq.shape:
            self._c_eq = np.zeros_like(target)
            self._eq_scratch = np.zeros_like(target)
        self._eq_target = target

    @property
    def eq_q(self) -> float:
        """Q escalar de compatibilidad: lee el Q por defecto; escribirlo
        propaga el valor a TODAS las bandas (la UI simple usa esto)."""
        return self._eq_q_default

    @eq_q.setter
    def eq_q(self, value: float) -> None:
        v = float(value)
        self._eq_q_default = v
        self.eq_q_values = [v] * len(self.eq_bands)

    def apply_params(self, params: "EnhancerParams") -> None:
        """Aplica una instantánea de parámetros de forma atómica.

        Escrituras individuales de la UI siguen siendo válidas (los sliders
        escriben atributo a atributo); este método es para cambios EN BLOQUE
        (presets, carga de configuración) donde cada atributo por separado
        dejaba una ventana con estados mezclados."""
        self.volume = float(params.volume)
        self.bass = float(params.bass)
        self.treble = float(params.treble)
        self.eq_gains = [float(g) for g in params.eq_gains]
        self.limiter = bool(params.limiter)
        self.compressor = bool(params.compressor)
        self.true_peak = bool(params.true_peak)
        self.blend = float(params.blend)
        self.crossfeed = bool(params.crossfeed)
        self.crossfeed_preset = str(params.crossfeed_preset)

    def snapshot_params(self) -> "EnhancerParams":
        """Lee el estado actual como instantánea inmutable."""
        return EnhancerParams(
            volume=float(self.volume),
            bass=float(self.bass),
            treble=float(self.treble),
            eq_gains=tuple(self._eq_gains),
            limiter=bool(self.limiter),
            compressor=bool(self.compressor),
            true_peak=bool(self.true_peak),
            blend=float(self.blend),
            crossfeed=bool(self.crossfeed),
            crossfeed_preset=str(self.crossfeed_preset),
        )

    @staticmethod
    def _shelf(freq, gain_db, fs, s=1.0, low=True):
        A = 10.0 ** (gain_db / 40.0)
        w0 = 2.0 * np.pi * freq / fs
        cw = np.cos(w0)
        sw = np.sin(w0)
        alpha = sw / 2.0 * np.sqrt((A + 1.0 / A) * (1.0 / s - 1.0) + 2.0)
        sa = 2.0 * np.sqrt(A) * alpha
        if low:
            b0 = A * ((A + 1) - (A - 1) * cw + sa)
            b1 = 2 * A * ((A - 1) - (A + 1) * cw)
            b2 = A * ((A + 1) - (A - 1) * cw - sa)
            a0 = (A + 1) + (A - 1) * cw + sa
            a1 = -2 * ((A - 1) + (A + 1) * cw)
            a2 = (A + 1) + (A - 1) * cw - sa
        else:
            b0 = A * ((A + 1) + (A - 1) * cw + sa)
            b1 = -2 * A * ((A - 1) + (A + 1) * cw)
            b2 = A * ((A + 1) + (A - 1) * cw - sa)
            a0 = (A + 1) - (A - 1) * cw + sa
            a1 = 2 * ((A - 1) - (A + 1) * cw)
            a2 = (A + 1) - (A - 1) * cw - sa
        return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0

    @staticmethod
    def _peaking(freq, gain_db, fs, q):
        A = 10.0 ** (gain_db / 40.0)
        w0 = 2.0 * np.pi * freq / fs
        cw = np.cos(w0)
        sw = np.sin(w0)
        alpha = sw / (2.0 * q)
        b0 = 1.0 + alpha * A
        b1 = -2.0 * cw
        b2 = 1.0 - alpha * A
        a0 = 1.0 + alpha / A
        a1 = -2.0 * cw
        a2 = 1.0 - alpha / A
        return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0

    def _section(self, key, b0, b1, b2, a1, a2):
        """Devuelve (key, sos, zi) para una sección biquad con estado persistente.

        No filtra: el ensamblado y la única llamada a sosfilt ocurren en
        _process_dsp, manteniendo el estado por banda en _states."""
        channels = self._channels
        zi = self._states.get(key)
        if zi is None or zi.shape != (2, channels):
            zi = np.zeros((2, channels), dtype=np.float32)
        coeffs = (b0, b1, b2, a1, a2)
        cached = self._sos_cache.get(key)
        if cached is None or cached[0] != coeffs:
            sos = np.array([[b0, b1, b2, 1.0, a1, a2]], dtype=np.float32)
            self._sos_cache[key] = (coeffs, sos)
        else:
            sos = cached[1]
        return key, sos, zi

    # ---------- crossfeed BS2B (auriculares) ----------

    # Perfiles de libbs2b: (frecuencia de corte Hz, nivel de cruce en dB).
    # Natural = default de BS2B; Moderate = Chu Moy (CMoy); Strong = Jan Meier.
    CROSSFEED_PRESETS: dict[str, tuple[int, float]] = {
        "Natural": (700, 4.5),
        "Moderate": (700, 6.0),
        "Strong": (650, 9.5),
    }

    def _crossfeed_coeffs(self):
        """Coeficientes de libbs2b para (tasa, preset), cacheados.

        Reproduce EXACTAMENTE el algoritmo de referencia (bs2b.c / bs2b):
          gb_lo = -5/6·dB - 3 ; gb_hi = dB/6 - 3
          g_lo  = 10^(gb_lo/20) ; g_hi = 1 - 10^(gb_hi/20)
          fc_hi = fc_lo · 2^((gb_lo - 20·log10(g_hi))/12)
        La señal propia pasa por un shelf (hi); la CRUZADA por un LPF 1er orden
        (lo). out_L = (hi_L + lo_R)·gain, out_R = (hi_R + lo_L)·gain."""
        preset = self.crossfeed_preset if self.crossfeed_preset in self.CROSSFEED_PRESETS else "Natural"
        src = (self.sample_rate, preset)
        if self._cf_coeffs is not None and self._cf_src == src:
            return self._cf_coeffs
        fc_lo, level_db = self.CROSSFEED_PRESETS[preset]
        fs = float(self.sample_rate)
        gb_lo = level_db * -5.0 / 6.0 - 3.0
        gb_hi = level_db / 6.0 - 3.0
        g_lo = 10.0 ** (gb_lo / 20.0)
        g_hi = 1.0 - 10.0 ** (gb_hi / 20.0)
        fc_hi = fc_lo * 2.0 ** ((gb_lo - 20.0 * math.log10(g_hi)) / 12.0)
        x_lo = math.exp(-2.0 * math.pi * fc_lo / fs)
        x_hi = math.exp(-2.0 * math.pi * fc_hi / fs)
        a0_lo, b1_lo = g_lo * (1.0 - x_lo), x_lo
        a0_hi, a1_hi, b1_hi = 1.0 - g_hi * (1.0 - x_hi), -x_hi, x_hi
        gain = 1.0 / (1.0 - g_hi + g_lo)
        self._cf_coeffs = (a0_lo, b1_lo, a0_hi, a1_hi, b1_hi, gain)
        self._cf_src = src
        return self._cf_coeffs

    def _apply_crossfeed(self, y: np.ndarray) -> np.ndarray:
        """Etapa BS2B por bloque (vectorizada con lfilter; O(n), sin allocs).

        Estado zi persistente entre bloques (continuidad); el bloque estéreo se
        procesa por canal y luego se cruza. No altera ganancia global (gain la
        normaliza). Entra al INICIO de la cadena (Input -> Crossfeed -> EQ...),
        igual que en libbs2b."""
        if y.shape[1] < 2:
            return y  # mono: no hay cruce posible
        a0_lo, b1_lo, a0_hi, a1_hi, b1_hi, gain = self._crossfeed_coeffs()
        sig = _scipy_signal()
        zi = self._cf_state
        # zi por canal: orden 1 -> estado de 1 tap por canal (2, 1, 2)=(filtro, 1, canales).
        if zi is None or zi.shape != (2, 1, 2):
            zi = np.zeros((2, 1, 2), dtype=np.float64)
        x = y.astype(np.float64, copy=False)
        # lo: LPF 1er orden del canal propio (luego se cruza).
        lo, zi_lo = sig.lfilter([a0_lo], [1.0, -b1_lo], x, axis=0, zi=zi[0])
        # hi: shelf del canal propio (realimenta el estado previo con a1_hi).
        hi, zi_hi = sig.lfilter([a0_hi, a1_hi], [1.0, -b1_hi], x, axis=0, zi=zi[1])
        self._cf_state = np.stack([zi_lo, zi_hi])
        out = np.empty_like(hi)
        out[:, 0] = (hi[:, 0] + lo[:, 1]) * gain  # L propio + cross de R
        out[:, 1] = (hi[:, 1] + lo[:, 0]) * gain
        return out.astype(np.float32, copy=False)

    # ---------- DSP ----------

    @staticmethod
    def _ramp(cur, target, alpha):
        return cur + (target - cur) * alpha

    def process(self, data):
        data = np.asarray(data, dtype=np.float32)
        mono = data.ndim == 1
        if mono:
            data = data[:, None]
        n = data.shape[0]
        if self.spectrum_enabled:
            # El analizador visual no necesita la tasa del audio: el callback
            # solo copia el bloque mono y la UI calcula la FFT en _update_meter.
            self._snapshot = data[:, 0].copy()
        block_sec = n / self.sample_rate
        # crossfade A/B: blend se desliza hacia el objetivo en ~50 ms
        alpha_blend = 1.0 - np.exp(-block_sec / 0.02)
        self._c_blend = self._ramp(self._c_blend, self.blend, alpha_blend)
        # si está en directo (B) estabilizado, omitir todo el DSP
        if self._c_blend < 1e-4 and self.blend <= 0.0:
            y = data.copy()
            self._measure_levels(y)
            return y[:, 0] if mono else y
        wet = self._process_dsp(data, block_sec)
        # caso común (A estabilizado): sin copias de dry ni crossfade
        if self._c_blend > 1.0 - 1e-4 and self.blend >= 1.0:
            self._measure_levels(wet)
            return wet[:, 0] if mono else wet
        dry = data.copy()
        y = dry * (1.0 - self._c_blend) + wet * self._c_blend
        if self.final_clip_enabled:
            y = np.clip(y, -1.0, 1.0)
        y = y.astype(np.float32)
        self._measure_levels(y)
        return y[:, 0] if mono else y

    def _process_dsp(self, data, block_sec):
        y = data.copy()
        nyquist = self.sample_rate / 2
        channels = data.shape[1]
        # Crossfeed BS2B al INICIO de la cadena (Input -> Crossfeed -> EQ ->
        # Comp -> Limit), como en libbs2b. Es una etapa aislada y O(n).
        if self.crossfeed:
            y = self._apply_crossfeed(y)
        if self._channels != channels:
            self._states = {}
            self._sos_cache = {}
            self._channels = channels
        # rampa anti-cremallera de los objetivos de la UI (el volumen se
        # suaviza aparte, por muestra, en _apply_volume). La de EQ es
        # IN-PLACE sobre _c_eq con buffer propio (R3-B5): cero allocations
        # por bloque; antes copiaba la lista de ganancias y aloc un array
        # nuevo en cada pasada del callback.
        alpha_eq = 1.0 - np.exp(-block_sec / 0.03)
        self._c_bass = self._ramp(self._c_bass, self.bass, alpha_eq)
        self._c_treble = self._ramp(self._c_treble, self.treble, alpha_eq)
        np.subtract(self._eq_target, self._c_eq, out=self._eq_scratch)
        self._eq_scratch *= alpha_eq
        self._c_eq += self._eq_scratch
        bass = float(self._c_bass)
        treb = float(self._c_treble)
        # Histéresis Schmitt por sección: ENTRA con |g| >= SECTION_ENTER_DB y
        # SALE con |g| < SECTION_EXIT_DB. En la banda muerta intermedia se
        # conserva el estado previo, así un slider que roza el umbral no
        # enciende/apaga el filtro bloque a bloque (cada conmutación reinyecta
        # el transitorio del estado zi: chasquido audible).
        nyq_ok_bass = self.bass_freq < nyquist
        bass_on = self._section_on.get("bass", False)
        if not bass_on and abs(bass) >= SECTION_ENTER_DB and nyq_ok_bass:
            bass_on = True
        elif bass_on and (abs(bass) < SECTION_EXIT_DB or not nyq_ok_bass):
            bass_on = False
        self._section_on["bass"] = bass_on
        nyq_ok_treb = self.treble_freq < nyquist
        treb_on = self._section_on.get("treble", False)
        if not treb_on and abs(treb) >= SECTION_ENTER_DB and nyq_ok_treb:
            treb_on = True
        elif treb_on and (abs(treb) < SECTION_EXIT_DB or not nyq_ok_treb):
            treb_on = False
        self._section_on["treble"] = treb_on
        # Apila las secciones activas en una sola matriz SOS: sosfilt aplica la
        # cascada en el orden dado (bass -> treble -> eq0..8), idéntico a llamar
        # por sección, con una única ida y vuelta a scipy.
        sections = []
        if bass_on:
            b0, b1, b2, a1, a2 = self._shelf(self.bass_freq, bass, self.sample_rate, 1.0, low=True)
            sections.append(self._section("bass", b0, b1, b2, a1, a2))
        if treb_on:
            b0, b1, b2, a1, a2 = self._shelf(self.treble_freq, treb, self.sample_rate, 1.0, low=False)
            sections.append(self._section("treble", b0, b1, b2, a1, a2))
        for i, (freq, g) in enumerate(zip(self.eq_bands, self._c_eq, strict=False)):
            key = "eq_%d" % i
            g_db = float(g)
            nyq_ok = freq < nyquist
            g_on = self._section_on.get(key, False)
            if not g_on and abs(g_db) >= SECTION_ENTER_DB and nyq_ok:
                g_on = True
            elif g_on and (abs(g_db) < SECTION_EXIT_DB or not nyq_ok):
                g_on = False
            self._section_on[key] = g_on
            if g_on:
                b0, b1, b2, a1, a2 = self._peaking(freq, g_db, self.sample_rate, self.eq_q_values[i])
                sections.append(self._section(key, b0, b1, b2, a1, a2))
        if sections:
            sos = np.concatenate([s[1] for s in sections], axis=0)
            zi = np.stack([s[2] for s in sections])
            out, zi_out = _scipy_signal().sosfilt(sos, y, axis=0, zi=zi)
            for s, z in zip(sections, zi_out, strict=False):
                self._states[s[0]] = np.asarray(z, dtype=np.float32)
            y = out
        # Cadena en orden de mastering: filtros -> compresor -> volumen ->
        # limitador (o techo de seguridad si está apagado). Va AL FINAL porque
        # es el único que puede garantizar el techo sobre la señal que
        # realmente sale: antes el volumen se aplicaba antes del compresor y
        # el limitador "suave" dejaba pasar ~70% del exceso por encima de su
        # umbral.
        if self.compressor:
            y = self._compress(y, block_sec)
        y = self._apply_volume(y, block_sec)
        if self.limiter:
            y = self._limit(y)
        elif y.size:
            # Techo de seguridad SIEMPRE activo: con el limitador apagado el
            # único techo era np.clip (recorte duro). El MISMO limitador
            # look-ahead cubre el hueco con techo transparente: una sola
            # implementación de ganancia suavizada que escala la señal
            # linealmente (la curva del EQ se preserva intacta). Con el
            # limitador encendido es no-op: 0.95 < 0.99 y _limit ya garantiza
            # su techo. Guarda SIN alocar (max/min son reducciones puras, sin
            # array temporal; el caso común bajo el techo cuesta dos pasadas).
            peak = max(float(y.max()), -float(y.min()))
            if self.safety_ceiling_enabled and peak > self.safety_ceiling:
                y = self._limit(y, thr=self.safety_ceiling)
        # Recorte duro final (±1.0), desactivable: apagado, la señal sale tal
        # cual y el recorte (si lo hay) ocurre en el driver de audio.
        if self.final_clip_enabled:
            y = np.clip(y, -1.0, 1.0)
        return y.astype(np.float32)

    def _apply_volume(self, data, block_sec):
        """Aplica el volumen con una rampa exponencial POR MUESTRA (tau ~100 ms).

        Antes se acercaba al objetivo en un solo bloque (~21 ms): al arrastrar
        el slider rápido el salto de ganancia se oía como un clic. Aquí la
        ganancia se desliza muestra a muestra, sin discontinuidades."""
        target = float(self.volume)
        start = self._c_vol
        n = data.shape[0]
        tau = 0.10  # segundos
        # Rampa cacheada en un LRU global acotado (solo lectura): misma
        # recurrencia que lfilter. Antes era un dict sin límite que alocaba
        # para siempre cada (n, sample_rate) nueva.
        ramp = _ramp_falling(n, self.sample_rate, tau)
        gain = target + (start - target) * ramp
        self._c_vol = float(gain[-1])
        return data * gain[:, None]

    def _compress(self, y, block_sec):
        """Compresor RMS POR MUESTRA (dual-envelope) con make-up gain.

        Reemplaza al compresor por BLOQUE: antes se medía un solo RMS para
        ~1024 muestras (~21 ms) y se aplicaba UNA ganancia constante al bloque
        entero; al cambiar el nivel dentro del bloque o entre bloques, la
        ganancia saltaba y se oía zipper noise. Ahora la envolvente de
        potencia se sigue muestra a muestra con dos polos (ataque ~5 ms y
        release ~200 ms) y la reducción se calcula por muestra en dB.

        Truco vectorial (sin bucle Python): se filtra la potencia con DOS
        lfilter de coeficientes constantes (uno rápido y otro lento) y se toma
        el máximo punto a punto. En un ataque el polo rápido va por encima (se
        usa: ataque rápido); en la caída el polo lento se queda arriba (se
        usa: release lento). Equivale al conmutado clásico ataque/release sin
        lfilter de coeficientes variables.

        La reducción es la curva estándar: over > 0 dB sobre el umbral =>
        reducción en dB negativos proporcional a (1 - 1/ratio). El monolito
        original aplicaba el signo contrario y AMPLIFICABA.
        """
        if y.shape[0] == 0:
            return y
        sig = _scipy_signal()
        a_att = 1.0 - math.exp(-1.0 / (self.sample_rate * max(self.comp_attack, 1e-4)))
        a_rel = 1.0 - math.exp(-1.0 / (self.sample_rate * max(self.comp_release, 1e-4)))
        # (R3-B4) Salida temprana SIN entrar en scipy. La envolvente de
        # potencia del bloque está acotada por max(pico actual, estados
        # previos): el one-pole con coeficientes positivos nunca supera
        # max(estado, entrada). Si ese tope ya está bajo umbral^2 (y makeup
        # =1, sin amplificación), la reducción sería 0.0 dB en todo el
        # bloque y el camino completo devolvería y intacto — pero gastando
        # un bloque en float64, dos lfilter, sqrt y log10. Aquí devolvemos
        # y directamente y DECAYEMOS los estados como los decaería la señal
        # real (recurrencia con entrada nula), para que el siguiente bloque
        # activo comprima exactamente igual que con el camino largo.
        zi_f = self._comp_zi_fast
        zi_s = self._comp_zi_slow
        amp_max = float(np.abs(y).max())
        bound = amp_max * amp_max
        if zi_f is not None:
            bound = max(bound, float(zi_f[0]), float(zi_s[0]))
        if self.comp_makeup == 1.0 and bound < self.comp_threshold**2:
            if zi_f is not None:
                n = y.shape[0]
                self._comp_zi_fast = zi_f * ((1.0 - a_att) ** n)
                self._comp_zi_slow = zi_s * ((1.0 - a_rel) ** n)
            return y
        # Potencia instantánea vinculada (stereo-link): media de canales.
        p = np.mean(np.asarray(y, dtype=np.float64) ** 2, axis=1)
        # one-pole: z[k] = z[k-1] + a*(x[k] - z[k-1]) <-> lfilter([a], [1, a-1]).
        # zi persiste entre bloques: la envolvente no se reinicia cada bloque.
        # (lfilter solo devuelve la tupla (y, zf) si zi no es None.)
        zi_f = np.zeros(1) if zi_f is None else zi_f
        zi_s = np.zeros(1) if zi_s is None else zi_s
        fast, self._comp_zi_fast = sig.lfilter([a_att], [1.0, a_att - 1.0], p, zi=zi_f)
        slow, self._comp_zi_slow = sig.lfilter([a_rel], [1.0, a_rel - 1.0], p, zi=zi_s)
        env_rms = np.sqrt(np.maximum(np.maximum(fast, slow), 0.0))
        if self._thr_src != self.comp_threshold:
            self._thr_src = self.comp_threshold
            self._thr_db = 20.0 * math.log10(max(self.comp_threshold, 1e-9))
        db = 20.0 * np.log10(np.maximum(env_rms, 1e-9))
        over = db - self._thr_db
        if self.comp_makeup == 1.0 and float(over.max()) <= 0.0:
            return y  # caso común: nada por encima del umbral, sin alocar
        red_db = np.where(over > 0.0, -over * (1.0 - 1.0 / self.comp_ratio), 0.0)
        gain = (10.0 ** (red_db / 20.0)) * self.comp_makeup
        return (y * gain[:, None].astype(np.float32)).astype(np.float32, copy=False)

    def _limit(self, y, thr=None):
        """Limitador brickwall con look-ahead de 3 ms (techo garantizado).

        Reemplaza al antiguo "soft limiter" por muestra: esa curva dependía de
        `strength` y dejaba pasar hasta ~70% del exceso (un pico de 1.4 salía
        ~1.14) porque atenuaba poco y SIN anticipación: cuando la ganancia
        reaccionaba, el transitorio ya había cruzado.

        ``thr`` permite reutilizar ESTA MISMA maquinaria (look-ahead + Hann +
        garantía de techo) como techo de seguridad cuando el limitador
        musical está apagado: un solo código de suavizado de ganancia que
        preserva la forma espectral (escala lineal), sin duplicados.

        Diseño estándar de mastering:
        1. Envolvente de pico vinculada (máximo entre canales, mono-link).
        2. Look-ahead: la ganancia exigida en cada muestra i se calcula sobre
           el MÁXIMO de la envolvente en [i, i+3 ms]; la atenuación empieza
           ANTES del transitorio, no después.
        3. Suavizado de ganancia con ventana corta (~2 ms, simétrico: es
           válido porque el look-ahead ya desplazó la ganancia al pasado) para
           eliminar zipper noise.
        4. Techo exacto: tras suavizar, un ajuste escalar (raro, fracciones de
           dB) garantiza pico <= techo sin recorte duro.

        Con ``true_peak`` activo (Fase 3), la envolvente se mide sobre la
        señal 4x sobremuestreada (resample_poly): los picos INTER-MUESTRA
        (contenido cerca de Nyquist, p.ej. un seno a fs/4 desfasado muestrea a
        0.707×A con pico real A) quedan cubiertos. La ganancia sobremuestreada
        se colapsa por min-pooling de 4 en 4 (peor caso por muestra base)."""
        if thr is None:
            thr = float(self.limiter_threshold)
        if y.size == 0:
            return y
        linked = np.abs(y).max(axis=1)  # envolvente de pico vinculada
        sample_peak = float(linked.max())
        if self.true_peak:
            # Atajo barato: el sobrepico inter-muestra teórico máximo es ~3 dB
            # (factor ~1.414) respecto al pico por muestra. Si incluso
            # asumiendo ese sobrepico no se alcanza el techo, no hay trabajo.
            if sample_peak <= thr / 1.414:
                return y
        elif sample_peak <= thr:
            return y  # caso común: bloque bajo el techo, intocado
        la = max(1, int(self.sample_rate * 0.003))  # look-ahead 3 ms
        if self.true_peak:
            # Sobremuestrear la senal CON SIGNO por canal y tomar abs despues:
            # aplicar abs antes equivaldría a rectificar (para un seno a fs/4
            # con fase π/4 el absoluto es DC y el pico real desaparecería).
            up = np.abs(_true_peak_oversample(y)).max(axis=1)
            la_up = la * 4
            # Envolvente CENTRADA (±3 ms) sobre la senal sobremuestreada.
            # maximum_filter1d(size impar, mode="nearest") replica los bordes
            # igual que el padding manual, pero en O(n) (van Herk) en vez de
            # O(n·ventana): la ventana mide 2·la_up+1 = 1153.
            nd = _scipy_ndimage()
            env_up = nd.maximum_filter1d(up, size=2 * la_up + 1, mode="nearest")  # (4n,)
            g_up = np.where(env_up > thr, thr / np.maximum(env_up, 1e-9), 1.0)
            # Colapso 4->1 por MINIMO sobre la MISMA ventana: la ganancia por
            # muestra base queda LENTA (constante por regiones). Min-pool de
            # solo 4 submuestras NO vale para contenido cerca de Nyquist: el
            # requisito oscila a la tasa del propio audio, modula la senal y
            # genera splatter con picos nuevos. Minimo sobre ±3 ms = ganancia
            # sostenida que cubre el intervalo completo de los picos.
            g_slow = nd.minimum_filter1d(g_up, size=2 * la_up + 1, mode="nearest")
            g = g_slow[::4][: y.shape[0]]
            if g.shape[0] < y.shape[0]:
                g = np.concatenate([g, np.full(y.shape[0] - g.shape[0], g[-1])])
        else:
            # Máximo móvil sobre [i, i+la]: padding replicando el final. La
            # ventana deslizante cuesta O(n*la) memoria transient (~0.15 MB
            # por bloque de 1024 muestras): despreciable.
            padded = np.concatenate([linked, np.full(la, linked[-1], dtype=linked.dtype)])
            win = np.lib.stride_tricks.sliding_window_view(padded, la + 1)
            env = win.max(axis=1)
            g = np.where(env > thr, thr / np.maximum(env, 1e-9), 1.0)
        # Suavizado simétrico ~2 ms (Hann normalizado): con look-ahead la
        # atenuación ya se anticipa 3 ms, así que centrar la ventana no
        # retrasa la protección.
        k = max(3, int(self.sample_rate * 0.002) | 1)
        kernel = _smooth_kernel(k)  # cacheado: k solo cambia con la tasa (D1)
        half = k // 2
        g_pad = np.concatenate([np.full(half, g[0]), g, np.full(half, g[-1])])
        g_s = np.minimum(np.convolve(g_pad, kernel, mode="valid")[: y.shape[0]], 1.0)
        out = y * g_s[:, None].astype(np.float32)
        # Garantía de techo: el suavizado puede subestimar la reducción en
        # transitorios muy cortos. En modo true-peak hay que MEDIR el true-peak
        # de la salida; pero solo vale la pena cuando el sample-peak está cerca
        # del techo (si no, ni asumiendo el sobrepico máximo ×1.414 lo cruza).
        # Eso evita una 2ª pasada de sobremuestreo en el caso común.
        sample_peak_out = float(np.abs(out).max())
        if self.true_peak:
            if sample_peak_out > thr / 1.414:
                peak = float(np.abs(_true_peak_oversample(out)).max())
                if peak > thr:
                    out *= thr / peak
        elif sample_peak_out > thr:
            out *= thr / sample_peak_out
        # GR (gain reduction) del bloque en dB: 0 = sin limitar, negativo =
        # atenuando. La UI lo muestra en un medidor para ajustar sin ir a
        # ciegas. Se toma el mínimo de la ganancia suavizada (el peor caso).
        min_gain = float(g_s.min()) if g_s.size else 1.0
        self.level_gr = max(-24.0, 20.0 * math.log10(max(min_gain, 1e-6)))
        return out

    # ---------- analizador de espectro ----------

    def compute_spectrum(self) -> None:
        """Calcula el espectro (64 barras en dB) desde el snapshot del último bloque.

        Se invoca desde el hilo de la UI (_update_meter), no desde el callback
        de audio: mantiene la FFT fuera del camino en tiempo real. El binning
        está vectorizado (np.add.reduceat) y los bordes se cachean por
        (n, sample_rate).
        """
        x = self._snapshot
        if x is None or x.size < 64:
            return
        n = x.size
        if self._win_n != n:
            self._win = np.hanning(n).astype(np.float32)
            self._win_n = n
            self._spec_meta = None
        mono = x * self._win
        spec = np.abs(np.fft.rfft(mono)) / n
        meta = self._spec_meta
        if meta is None or meta[0] != n or meta[1] != self.sample_rate:
            freqs = np.fft.rfftfreq(n, 1.0 / self.sample_rate)
            hi = min(self.sample_rate / 2.0, 20000.0)
            edges = np.geomspace(40.0, max(hi, 200.0), 65)
            idx = np.searchsorted(freqs, edges, side="right")
            self._spec_meta = (n, self.sample_rate, idx)
        else:
            idx = meta[2]
        out = np.full(64, -80.0, dtype=np.float32)
        counts = np.diff(idx)
        valid = counts > 0
        # reduceat suma el último segmento hasta el final del array: hay que
        # añadir idx[-1] como tope de cierre para que el bin 63 no absorba los
        # bins de frecuencia que quedan por encima del techo (20 kHz).
        sums = np.add.reduceat(spec, np.append(idx[:-1], idx[-1]))[:64]
        means = sums / np.where(valid, counts, 1)
        pos = valid & (means > 1e-9)
        out[pos] = 20.0 * np.log10(means[pos])
        self.spectrum = out

    def _measure_levels(self, y) -> None:
        """Actualiza el medidor de nivel POR CANAL (L/R) y el agregado.

        Antes mezclaba canales (max/mean sobre todo el array): con estéreo
        real el medidor no distinguía un canal del otro. Ahora se mide cada
        canal y ``level_peak``/``level_rms`` quedan como el máximo de ambos
        (mismo valor que daba el cálculo global, por compatibilidad)."""
        if y.size == 0:
            peak_l = peak_r = rms_l = rms_r = 0.0
        elif y.ndim > 1 and y.shape[1] >= 2:
            peak_l = float(np.max(np.abs(y[:, 0])))
            peak_r = float(np.max(np.abs(y[:, 1])))
            rms_l = float(np.sqrt(np.mean(y[:, 0] ** 2)))
            rms_r = float(np.sqrt(np.mean(y[:, 1] ** 2)))
        else:  # mono: ambos canales idénticos
            peak_l = peak_r = float(np.max(np.abs(y)))
            rms_l = rms_r = float(np.sqrt(np.mean(y**2)))
        self.level_peak_l = self.level_peak_l * 0.7 + peak_l * 0.3
        self.level_peak_r = self.level_peak_r * 0.7 + peak_r * 0.3
        self.level_rms_l = self.level_rms_l * 0.85 + rms_l * 0.15
        self.level_rms_r = self.level_rms_r * 0.85 + rms_r * 0.15
        self.level_peak = max(self.level_peak_l, self.level_peak_r)
        self.level_rms = max(self.level_rms_l, self.level_rms_r)
