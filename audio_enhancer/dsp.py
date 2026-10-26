"""Cadena DSP de Audio Enhancer FxStyle (puro numpy/scipy, sin UI).

``Enhancer`` procesa bloques de audio en el hilo DSP dedicado del motor
(``AudioEngine._dsp_loop``; antes corría dentro del callback de captura).
Thread-safety: el hilo de audio (DSP) lee los *targets mientras la UI
escribe en volume/bass/treble/eq_gains/blend. Para evitar zipper noise y
estados corruptos, process() desliza los valores "actuales" (_c*) hacia los
objetivos con una rampa exponencial por bloque; los filtros se rediseñan cada
bloque con el valor suavizado. Nunca se escribe el estado del DSP desde el
callback.
"""

import dataclasses
import logging
import math
import threading
from functools import lru_cache
from typing import Any

import numpy as np

from .constants import SAMPLE_RATE
from .loudness import GATE_ABSOLUTE_LUFS, LOUDNESS_OFFSET_DB, k_weighting_sos, loudness_gain_db

logger = logging.getLogger("audio_enhancer.dsp")

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

# Sets de frecuencias centrales por número de bandas (Fase 5: EQ avanzado).
# El de 9 es el histórico (compatibilidad); el resto son estándar (31 = ISO 1/3
# de octava). El usuario puede mover cada frecuencia dentro de [20, 20000].
EQ_BAND_SETS: dict[int, list[float]] = {
    5: [60, 250, 1000, 4000, 12000],
    9: list(EQ_BANDS),
    10: [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000],
    15: [20, 31.5, 50, 80, 125, 200, 315, 500, 800, 1250, 2000, 3150, 5000, 8000, 12500],
    31: [
        20,
        25,
        31.5,
        40,
        50,
        63,
        80,
        100,
        125,
        160,
        200,
        250,
        315,
        400,
        500,
        630,
        800,
        1000,
        1250,
        1600,
        2000,
        2500,
        3150,
        4000,
        5000,
        6300,
        8000,
        10000,
        12500,
        16000,
        20000,
    ],
}
EQ_FREQ_MIN, EQ_FREQ_MAX = 20.0, 20000.0
# Rango de Q con el que el biquad peaking es numéricamente seguro (q=0 anula
# el denominador y envenena el estado). El config/UI usan 0.2..10.
EQ_Q_MIN, EQ_Q_MAX = 0.05, 60.0
EQ_Q_DEFAULT = 1.4

# Capa "enhancer" (paridad FxSound): 5 efectos con escala 0-10 en la UI.
# Los nombres siguen a FxSound para que la UI sea familiar.
EFFECT_KEYS = ("fidelity", "ambience", "surround", "dynamic_boost", "bass")
# Escala de la UI de los efectos y rango interno de los parámetros nuevos.
EFFECT_MIN, EFFECT_MAX = 0.0, 10.0
BALANCE_DB_MIN, BALANCE_DB_MAX = -20.0, 20.0
MASTER_DB_MIN, MASTER_DB_MAX = -20.0, 20.0
# DC blocker al inicio de la cadena: paso-alto de 1 polo a ~10 Hz.
DC_BLOCK_HZ = 10.0


def default_effects() -> dict[str, float]:
    """Efectos nuevos a 0 (apagados): la cadena suena igual que antes."""
    return {k: 0.0 for k in EFFECT_KEYS}


# Dynamic Boost (compresor ascendente): objetivo de loudness (dBFS RMS) y tope
# del boost. Sube los pasajes bajos sin tocar los picos.
DYNAMIC_BOOST_TARGET_DB = -18.0
DYNAMIC_BOOST_MAX_DB = 12.0
# Leveling (AGC por SONORIDAD, no por RMS): objetivo en LUFS segun ITU-R
# BS.1770 (el oido pesa distinto las frecuencias y eso el RMS no lo sabe) y
# tope de la corrección. Los tiempos son LENTOS a propósito: es un AGC de
# material (entre pistas), no un compresor; con ataque/release rápidos se oiría
# "bombear" y se aplanarían los contrastes musicales.
LEVELING_TARGET_LUFS = -18.0
LEVELING_MAX_DB = 9.0
LEVELING_ATTACK_S = 0.5
LEVELING_RELEASE_S = 3.0
# Cebado: durante el primer segundo de medida VÁLIDA no se actúa. Sin esto, al
# encender el control (o al volver el audio tras una pausa) la envolvente parte
# de cero y el AGC pediría el máximo (+9 dB) durante un instante: un salto de
# nivel audible en la primera nota.
LEVELING_PRIME_S = 1.0
# Suavizado de la corrección entre bloques (segundos): lento al SUBIR (un AGC no
# debe hinchar de golpe) y rápido al BAJAR (seguridad: si entra material fuerte,
# la ganancia sobrante desaparece en ~80 ms en vez de quedarse medio segundo
# empujando el limitador).
LEVELING_SLEW_S = 0.5
LEVELING_SLEW_DOWN_S = 0.08
# Detector RÁPIDO solo para la puerta (segundos): la envolvente del AGC (3 s de
# release) tarda ~13 s en cruzar -70 LUFS tras un silencio, así que por sí sola
# no distingue una pausa de material bajito. El retardo (hold) evita que un
# silencio corto ENTRE notas se tome por una pausa y reinicie el AGC.
LEVELING_GATE_S = 0.025
LEVELING_GATE_HOLD_S = 0.15
# Alias histórico (v1.6 usaba un objetivo en dBFS RMS): se mantiene por
# compatibilidad de imports, pero el AGC ya no mide RMS.
LEVELING_TARGET_DB = LEVELING_TARGET_LUFS
# Tiempos de la envolvente (segundos): ataque rápido y release lento.
ENV_ATTACK_S = 0.005
ENV_RELEASE_S = 0.35
# Clarity (Fidelity) y Bass exciter: generación de 2º armónico.
#   banda = filtro (HP de presencia / LP de graves); h = banda*|banda| (2f, sin
#   DC); se mezcla una fracción. Restaura detalle/punch sin subir esa banda de
#   volumen (es contenido NUEVO, no un realce del EQ).
CLARITY_HP_HZ = 3000.0
CLARITY_LP_HZ = 16000.0
CLARITY_MIX = 0.5
# Bass exciter: banda de entrada y mezcla de armónicos. La banda llega a 120 Hz
# (antes 100) para cubrir los graves típicos de la música, y se generan 2º Y 3º
# armónico (ver _bass_exciter): el 2º solo cae a 80 Hz con una fundamental de
# 40 Hz, donde un altavoz pequeño no reproduce nada; el 3º (120 Hz) sí se oye.
BASS_EXCITER_LP_HZ = 120.0
BASS_EXCITER_MIX_H2 = 0.45
BASS_EXCITER_MIX_H3 = 0.35
# El 3er armónico se genera con un cúbico NORMALIZADO (x^3 / media(x^2)): para un
# seno eso deja el 3er armónico a -12 dB del fundamental, mientras que x*|x| lo
# dejaba a -37 dB (medido, es decir: inaudible). El paso-alto quita la
# fundamental que el cúbico también contiene, para no realzar el grave (eso ya lo
# hace el EQ). La escala 0.5 deja el 3F a ~-24 dB: se oye sin tapar al 2º.
BASS_EXCITER_HP3_HZ = 120.0
BASS_EXCITER_H3_SCALE = 0.5
# Ganancia de compensación de la atenuación que introduce el ADAA (promedia dos
# muestras). Medido por frecuencia del ARMÓNICO: 3 kHz 0.981, 5 kHz 0.972,
# 6 kHz 0.924, 8 kHz 0.866, 11 kHz 0.872 y 22 kHz 0.516. La banda útil de Clarity
# es 6-16 kHz, así que se compensa ~1.2 dB (1.15); calibrar en el peor punto
# (22 kHz, 0.516 -> x2) dejaba el efecto hasta +6 dB más caliente justo donde se
# oye, que es el error que se corrigió aquí.
ADAA_GAIN = 1.15
EXCITER_Q = 0.707
# Ambience: reflexiones tempranas (retardo en ms, ganancia) por canal; los
# retardos son primos y distintos por canal (decorrelación L/R).
AMBIENCE_TAPS_L = ((11.0, 0.35), (23.0, 0.20), (37.0, 0.12))
AMBIENCE_TAPS_R = ((13.0, 0.35), (27.0, 0.20), (41.0, 0.12))
AMBIENCE_MIX = 0.3
# Surround: ensanchado M/S con mono-bass (los graves quedan al centro) y guard
# de correlación (si la señal ya es ancha, se reduce el ensanchado).
SURROUND_WIDTH = 0.8
SURROUND_MONO_BASS_HZ = 150.0
SURROUND_CORR_MIN = 0.2


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


@lru_cache(maxsize=64)
def _onepole_powers(pole: float, n: int) -> np.ndarray:
    """p^1 .. p^n cacheados (para el filtro de 1 polo vectorizado)."""
    return pole ** np.arange(1, n + 1, dtype=np.float64)


def pchip_interp(x, y, xq):
    """Interpolacion cubica monotona (Fritsch-Carlson) de ``y(x)`` en ``xq``.

    Sin sobreoscilacion (no inventa picos entre puntos) y con pendiente
    continua: la usan la curva dibujada y el remapeo al cambiar de bandas,
    para que la forma se conserve. Fuera del rango de ``x`` el valor queda
    plano en el extremo (extension horizontal)."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    xq = np.asarray(xq, dtype=np.float64)
    n = x.size
    if n < 2:
        return np.full(xq.shape, float(y[0]) if n else 0.0, dtype=np.float64)
    h = np.diff(x)
    if np.any(h <= 0.0):
        return np.interp(xq, x, y)  # x no creciente: no deberia ocurrir
    delta = np.diff(y) / h
    m = np.zeros_like(y)
    m[0] = delta[0]
    m[-1] = delta[-1]
    if n > 2:
        d0, d1 = delta[:-1], delta[1:]
        w1 = 2.0 * h[1:] + h[:-1]
        w2 = h[1:] + 2.0 * h[:-1]
        same = d0 * d1 > 0.0
        safe0 = np.where(same, d0, 1.0)
        safe1 = np.where(same, d1, 1.0)
        m[1:-1] = np.where(same, (w1 + w2) / (w1 / safe0 + w2 / safe1), 0.0)
    xc = np.clip(xq, x[0], x[-1])
    idx = np.clip(np.searchsorted(x, xc) - 1, 0, n - 2)
    t = (xc - x[idx]) / h[idx]
    t2 = t * t
    t3 = t2 * t
    return (
        (2.0 * t3 - 3.0 * t2 + 1.0) * y[idx]
        + (t3 - 2.0 * t2 + t) * h[idx] * m[idx]
        + (-2.0 * t3 + 3.0 * t2) * y[idx + 1]
        + (t3 - t2) * h[idx] * m[idx + 1]
    )


def _onepole_lp(x: np.ndarray, pole: float, y_last, b0=None):
    """Filtro de 1 polo ``y[n] = b0*x[n] + pole*y[n-1]`` SIN scipy.

    Solución cerrada del filtro: ``y[n] = p^(n+1)*y_last +
    b0*sum_{k<=n} p^(n-k) x[k]``, calculable con cumsum (sin bucle Python).
    Evita la llamada a ``scipy.signal.lfilter`` (~150 us de overhead por
    bloque). Válido para polos cercanos a 1 (nuestros taus van de 5 ms a
    0.35 s), donde ``p^-k`` no desborda en float64. ``b0`` por defecto es
    ``1-pole`` (ganancia unitaria en continua, como un low-pass).

    Devuelve ``(y, y_last_nuevo)``."""
    n = x.shape[0]
    if n == 0:
        return x, y_last
    if b0 is None:
        b0 = 1.0 - pole
    powers = _onepole_powers(pole, n)
    if x.ndim > 1:
        powers = powers[:, None]
    cs = np.cumsum(np.asarray(x, dtype=np.float64) / powers, axis=0)
    y = powers * (b0 * cs + y_last)
    return y, (y[-1] if y.ndim == 1 else y[-1].copy())


def _onepole_hp(x: np.ndarray, pole: float, y_last, x_last):
    """Paso-alto de 1 polo ``y[n] = x[n]-x[n-1] + pole*y[n-1]`` SIN scipy.

    Diferencia la señal y aplica el filtro de 1 polo con b0=1 (así el
    numerador es 1 - z^-1: el paso-alto clásico). Devuelve
    ``(y, y_last_nuevo, x_last_nuevo)``."""
    n = x.shape[0]
    if n == 0:
        return x, y_last, x_last
    x64 = np.asarray(x, dtype=np.float64)
    d = np.empty_like(x64)
    d[0] = x64[0] - x_last
    d[1:] = x64[1:] - x64[:-1]
    y, y_last = _onepole_lp(d, pole, y_last, b0=1.0)
    return y, y_last, (x64[-1] if x64.ndim == 1 else x64[-1].copy())


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
    # Fase 5 (EQ avanzado): frecuencias centrales por banda y Q global.
    eq_bands: tuple[float, ...] = ()
    eq_q: float = 1.4
    limiter: bool = True
    compressor: bool = True
    true_peak: bool = True
    blend: float = 1.0
    crossfeed: bool = False
    crossfeed_preset: str = "Natural"
    crossfeed_cut_hz: int = 700
    crossfeed_feed_db: float = 4.5
    # Capa enhancer (Fase 0): efectos 0-10, balance/master en dB y leveling 0-10.
    # El dict se copia en apply/snapshot (la instantánea no comparte referencia).
    effects: dict[str, float] = dataclasses.field(default_factory=default_effects)
    balance_db: float = 0.0
    master_gain_db: float = 0.0
    leveling: float = 0.0


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
        # Modo Avanzado (preset="Custom"): frecuencia de corte y nivel de cruce.
        self.crossfeed_cut_hz: int = 700
        self.crossfeed_feed_db: float = 4.5
        # Coeficientes y estados (zi) del crossfeed, recalculados al cambiar
        # preset/tasa; reset_state() los limpia.
        self._cf_coeffs: tuple | None = None
        self._cf_src: tuple | None = None  # (sample_rate, preset) de _cf_coeffs
        self._cf_state: np.ndarray | None = None  # zi de lfilter
        # ---- Capa enhancer (Fase 0) ----
        # 5 efectos (0-10), balance/master (dB) y leveling (0-10). Apagados por
        # defecto: con todo a 0 la cadena suena igual que antes.
        self.effects: dict[str, float] = default_effects()
        self.balance_db: float = 0.0
        self.master_gain_db: float = 0.0
        self.leveling: float = 0.0
        # Valores suavizados (rampas anti-cremallera) de los nuevos controles.
        self._c_master: float = 1.0
        self._c_balance_db: float = 0.0
        self._c_effects: dict[str, float] = default_effects()
        self._c_leveling: float = 0.0
        # DC blocker SIEMPRE activo al inicio de la cadena: quita la componente
        # continua (<~10 Hz) que roba headroom. Estado zi y coeficiente R.
        # `dc_block_enabled` es un punto de aislamiento para los tests (en
        # producción va siempre True): permite verificar que el RESTO de la
        # cadena es transparente sin el paso-alto.
        self.dc_block_enabled: bool = True
        self._dc_state: tuple | None = None
        self._dc_ch: int = 0
        self._dc_R: float = 0.0
        self._dc_src_rate: int = 0
        # Estados (zi) de las envolventes de las etapas enhancer (Dynamic
        # Boost, Leveling): dict slot -> zi, persistente entre bloques.
        self._env_zi: dict[str, np.ndarray] = {}
        # Estados de los filtros de banda de los exciters (Clarity/Bass):
        # arrays zi de scipy para los biquads y tuplas (y_last, x_last) para
        # los pasos-altos de 1 polo (implementación numpy).
        self._exc_zi: dict[str, Any] = {}
        # Caché de los coeficientes del filtro K (Leveling por LUFS): depende de
        # la tasa, así que se recalcula cuando cambia.
        self._lv_sos: Any = None
        self._lv_rate: int = 0
        # Estado del AGC por sonoridad (corrección aplicada, cebado y puerta).
        self._lv_corr_db: float = 0.0
        self._lv_valid_s: float = 0.0
        self._lv_gate_db: float = -120.0
        self._lv_gate_below_s: float = 0.0
        # Ambience (Fase 3): historial para las reflexiones tempranas.
        self._amb_hist: np.ndarray | None = None
        # Surround (Fase 3): factor del guard de correlación (suavizado).
        self._sr_boost: float = 0.0
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
        # Última ganancia aplicada por el limitador al final del bloque previo
        # (continuidad en la costura entre bloques sin escalón).
        self._lim_last_g: float = 1.0
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
        # Coeficientes biquad por banda, cacheados: en reposo la ganancia
        # suavizada no cambia, así que _shelf/_peaking no se recalculan.
        self._coeff_cache: dict[str, tuple] = {}
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
        arrancar). Las rampas arrancan en los OBJETIVOS actuales y los
        medidores a cero."""
        self._states = {}
        self._sos_cache = {}
        self._coeff_cache = {}
        self._channels = None
        # (F1) Arrancar desde los objetivos, no desde constantes: antes un
        # reset (cambio de tasa/dispositivo o saneo de una entrada no finita)
        # devolvia _c_vol a 1.0 y un usuario con volumen 0.45 (Noche) oia un
        # salto de ~+7 dB durante ~100 ms; en bypass (blend=0) tambien se
        # colaba audio procesado al volver _c_blend a 1.0.
        self._c_vol = float(self.volume)
        self._c_bass = float(self.bass)
        self._c_treble = float(self.treble)
        self._c_eq = np.array(self._eq_target, dtype=np.float32, copy=True)
        self._c_blend = float(self.blend)

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
        self._lim_last_g = 1.0
        self._cf_state = None
        self._c_master = 1.0
        self._c_balance_db = 0.0
        self._c_effects = default_effects()
        self._c_leveling = 0.0
        self._dc_state = None
        self._dc_ch = 0
        self._env_zi = {}
        self._exc_zi = {}
        # Caché del filtro K (BS.1770) del Leveling: depende de la tasa.
        self._lv_sos = None
        self._lv_rate = 0
        # Estado del AGC por sonoridad: corrección aplicada (dB), segundos de
        # medida válida acumulados (cebado) y nivel del detector rápido (puerta).
        self._lv_corr_db = 0.0
        self._lv_valid_s = 0.0
        self._lv_gate_db = -120.0
        self._lv_gate_below_s = 0.0
        self._amb_hist = None
        self._sr_boost = 0.0
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

    def set_eq_bands(self, freqs) -> None:
        """Cambia las frecuencias centrales del EQ (redimensiona ganancias/Q).

        Valida: frecuencias dentro de [20, 20000] y ordenadas; si llegan menos
        de 2, se usa el set por defecto. Las ganancias se REMAPEAN a las nuevas
        frecuencias con una interpolacion cubica monotona (PCHIP) en escala
        logaritmica: la forma se conserva al cambiar de bandas, sin quiebros ni
        picos inventados (la misma curva que se dibuja). Los Q existentes se
        conservan por indice (los nuevos usan el Q por defecto). Limpia los
        estados de filtro: las secciones han cambiado."""
        f = sorted(min(EQ_FREQ_MAX, max(EQ_FREQ_MIN, float(x))) for x in freqs)
        if len(f) < 2:
            f = list(EQ_BANDS)
        n = len(f)
        old_bands = [float(x) for x in self.eq_bands]
        old_gains = [float(g) for g in self._eq_gains]
        if len(old_bands) == n and all(abs(a - b) < 1e-9 for a, b in zip(old_bands, f, strict=True)):
            gains = old_gains  # mismo set: no tocar la curva
        elif not any(abs(g) > 1e-6 for g in old_gains):
            gains = [0.0] * n  # curva plana: nada que remapear
        else:
            # Curva suave (PCHIP) ganancia vs log(frecuencia): fuera del rango
            # antiguo se extiende plana con el valor del extremo.
            log_old = np.log10(np.maximum(np.asarray(old_bands, dtype=np.float64), 1e-9))
            log_new = np.log10(np.maximum(np.asarray(f, dtype=np.float64), 1e-9))
            gains = [float(g) for g in pchip_interp(log_old, old_gains, log_new)]
        qs = (list(self.eq_q_values) + [self._eq_q_default] * n)[:n]
        self.eq_bands = f
        self.eq_q_values = qs
        self.eq_gains = gains  # el setter reconstruye _eq_target y redimensiona rampas
        self._section_on = {}
        self._states = {}
        self._sos_cache = {}
        self._coeff_cache = {}

    def set_eq_band_count(self, count: int) -> None:
        """Fija el número de bandas usando el set por defecto de ese número."""
        self.set_eq_bands(EQ_BAND_SETS.get(int(count), EQ_BANDS))

    def set_eq_band_freq(self, index: int, freq: float) -> None:
        """Mueve la frecuencia central de una banda, acotada entre sus vecinas.

        Se mantiene el orden (cada banda queda entre la anterior y la
        siguiente) para que la curva siga siendo monótona."""
        n = len(self.eq_bands)
        if not (0 <= index < n):
            return
        lo = EQ_FREQ_MIN if index == 0 else self.eq_bands[index - 1] * 1.02
        hi = EQ_FREQ_MAX if index == n - 1 else self.eq_bands[index + 1] * 0.98
        bands = list(self.eq_bands)
        bands[index] = min(max(float(freq), lo), hi)
        self.eq_bands = bands
        # La sección cambió de frecuencia: invalidar su estado y sus cachés.
        key = "eq_%d" % index
        self._section_on.pop(key, None)
        self._states.pop(key, None)
        self._sos_cache.pop(key, None)
        self._coeff_cache.pop(key, None)

    def apply_params(self, params: "EnhancerParams") -> None:
        """Aplica una instantánea de parámetros de forma atómica.

        Escrituras individuales de la UI siguen siendo válidas (los sliders
        escriben atributo a atributo); este método es para cambios EN BLOQUE
        (presets, carga de configuración) donde cada atributo por separado
        dejaba una ventana con estados mezclados."""
        self.volume = float(params.volume)
        self.bass = float(params.bass)
        self.treble = float(params.treble)
        # Fase 5: las bandas primero (redimensiona), luego las ganancias y el Q.
        if params.eq_bands:
            self.set_eq_bands(params.eq_bands)
        self.eq_gains = [float(g) for g in params.eq_gains]
        if params.eq_q:
            self.eq_q = float(params.eq_q)
        self.limiter = bool(params.limiter)
        self.compressor = bool(params.compressor)
        self.true_peak = bool(params.true_peak)
        self.blend = float(params.blend)
        self.crossfeed = bool(params.crossfeed)
        self.crossfeed_preset = str(params.crossfeed_preset)
        self.crossfeed_cut_hz = int(params.crossfeed_cut_hz)
        self.crossfeed_feed_db = float(params.crossfeed_feed_db)
        self.effects = {str(k): float(v) for k, v in dict(params.effects).items()}
        self.balance_db = float(params.balance_db)
        self.master_gain_db = float(params.master_gain_db)
        self.leveling = float(params.leveling)

    def snapshot_params(self) -> "EnhancerParams":
        """Lee el estado actual como instantánea inmutable."""
        return EnhancerParams(
            volume=float(self.volume),
            bass=float(self.bass),
            treble=float(self.treble),
            eq_gains=tuple(self._eq_gains),
            eq_bands=tuple(self.eq_bands),
            eq_q=float(self._eq_q_default),
            limiter=bool(self.limiter),
            compressor=bool(self.compressor),
            true_peak=bool(self.true_peak),
            blend=float(self.blend),
            crossfeed=bool(self.crossfeed),
            crossfeed_preset=str(self.crossfeed_preset),
            crossfeed_cut_hz=int(self.crossfeed_cut_hz),
            crossfeed_feed_db=float(self.crossfeed_feed_db),
            effects=dict(self.effects),
            balance_db=float(self.balance_db),
            master_gain_db=float(self.master_gain_db),
            leveling=float(self.leveling),
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
        # Q con red de seguridad: es el único parámetro del biquad que puede
        # anular el denominador (alpha = sw/(2q)). Con q=0 los coeficientes
        # salían inf/NaN y el estado zi quedaba envenenado (salida NaN para
        # siempre). Se acota a un rango audible; el default es 1.4.
        q = float(q)
        if not math.isfinite(q):
            q = EQ_Q_DEFAULT
        q = min(EQ_Q_MAX, max(EQ_Q_MIN, q))
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

    @staticmethod
    def _highpass(freq, fs, q=EXCITER_Q):
        """Biquad RBJ paso-alto (banda de presencia de los exciters)."""
        w0 = 2.0 * np.pi * freq / fs
        cw = math.cos(w0)
        sw = math.sin(w0)
        alpha = sw / (2.0 * q)
        b0 = (1.0 + cw) / 2.0
        b1 = -(1.0 + cw)
        b2 = (1.0 + cw) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cw
        a2 = 1.0 - alpha
        return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0

    @staticmethod
    def _lowpass(freq, fs, q=EXCITER_Q):
        """Biquad RBJ paso-bajo (banda de graves de los exciters)."""
        w0 = 2.0 * np.pi * freq / fs
        cw = math.cos(w0)
        sw = math.sin(w0)
        alpha = sw / (2.0 * q)
        b0 = (1.0 - cw) / 2.0
        b1 = 1.0 - cw
        b2 = (1.0 - cw) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cw
        a2 = 1.0 - alpha
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

    def _coeff_cached(self, key, sig, compute):
        """Coeficientes biquad cacheados por banda.

        ``sig`` resume los parámetros que afectan al filtro (freq, ganancia
        redondeada, q). En reposo la ganancia suavizada no cambia -> no se
        recalcula el filtro; solo al mover un slider (transitorio)."""
        cached = self._coeff_cache.get(key)
        if cached is not None and cached[0] == sig:
            return cached[1]
        coeffs = compute()
        self._coeff_cache[key] = (sig, coeffs)
        return coeffs

    # ---------- crossfeed BS2B (auriculares) ----------

    # Perfiles de libbs2b (valores oficiales): (frecuencia de corte Hz, nivel
    # de cruce en dB).
    #   DEFAULT  = subwoofer virtual a ±30°, ~3 m: cruce moderado.
    #   CMOY     = circuito analógico de Chu Moy (el más popular).
    #   JMEIER   = CORDA de Jan Meier: el más sutil (menor cambio).
    #   Custom   = perfil de usuario (frecuencia/feed del modo Avanzado).
    CROSSFEED_PRESETS: dict[str, tuple[int, float]] = {
        "Natural": (700, 4.5),  # libbs2b DEFAULT
        "Moderate": (700, 6.0),  # libbs2b CMOY
        "Strong": (650, 9.5),  # libbs2b JMEIER
        "Custom": (700, 4.5),  # valores iniciales; los fija el usuario
    }
    # Rangos válidos de libbs2b (para el modo Custom/Avanzado).
    CF_CUT_MIN, CF_CUT_MAX = 300, 2000  # Hz
    CF_FEED_MIN, CF_FEED_MAX = 1.0, 15.0  # dB

    def _crossfeed_coeffs(self):
        """Coeficientes de libbs2b para (tasa, preset/custom), cacheados.

        Reproduce EXACTAMENTE el algoritmo de referencia (bs2b.c / bs2b):
          gb_lo = -5/6·dB - 3 ; gb_hi = dB/6 - 3
          g_lo  = 10^(gb_lo/20) ; g_hi = 1 - 10^(gb_hi/20)
          fc_hi = fc_lo · 2^((gb_lo - 20·log10(g_hi))/12)
        La señal propia pasa por un shelf (hi); la CRUZADA por un LPF 1er orden
        (lo). out_L = (hi_L + lo_R)·gain, out_R = (hi_R + lo_L)·gain."""
        preset = self.crossfeed_preset if self.crossfeed_preset in self.CROSSFEED_PRESETS else "Natural"
        if preset == "Custom":
            # Modo Avanzado: usa (y acota a los rangos de libbs2b) los valores
            # del usuario en vez de un perfil fijo.
            fc_lo = min(self.CF_CUT_MAX, max(self.CF_CUT_MIN, int(self.crossfeed_cut_hz)))
            level_db = min(self.CF_FEED_MAX, max(self.CF_FEED_MIN, float(self.crossfeed_feed_db)))
        else:
            fc_lo, level_db = self.CROSSFEED_PRESETS[preset]
        src = (self.sample_rate, preset, fc_lo, round(level_db, 3))
        if self._cf_coeffs is not None and self._cf_src == src:
            return self._cf_coeffs
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
        # Estado zi por filtro/canal en float64 (1 tap, orden 1). Se reusa el
        # mismo array y se actualiza IN-PLACE (evita el np.stack por bloque).
        zi = self._cf_state
        if zi is None or zi.shape != (2, 1, 2):
            zi = np.zeros((2, 1, 2), dtype=np.float64)
            self._cf_state = zi
        # lo: LPF 1er orden del canal propio (luego se cruza).
        lo, z_lo = sig.lfilter([a0_lo], [1.0, -b1_lo], y, axis=0, zi=zi[0])
        zi[0] = z_lo
        # hi: shelf del canal propio (realimenta el estado previo con a1_hi).
        hi, z_hi = sig.lfilter([a0_hi, a1_hi], [1.0, -b1_hi], y, axis=0, zi=zi[1])
        zi[1] = z_hi
        # Mezcla cruzada in-place sobre el buffer `hi` (evita un `out` nuevo).
        # lfilter conserva el dtype de la entrada `y`; se normaliza a float32
        # al final para el resto de la cadena (DSP en float32).
        hi[:, 0] = (hi[:, 0] + lo[:, 1]) * gain  # L propio + cross de R
        hi[:, 1] = (hi[:, 1] + lo[:, 0]) * gain
        return hi if hi.dtype == np.float32 else hi.astype(np.float32, copy=False)

    # ---------- DSP ----------

    @staticmethod
    def _ramp(cur, target, alpha):
        return cur + (target - cur) * alpha

    @staticmethod
    def _onepole(block_sec: float, tau: float) -> float:
        """Coeficiente de suavizado de 1 polo para un tau dado (segundos).

        Aplicar ``_ramp(cur, target, alpha)`` con este alpha cada bloque
        reproduce un filtro de 1 polo de constante de tiempo ``tau``."""
        return 1.0 - math.exp(-block_sec / max(tau, 1e-6))

    def _apply_dc_blocker(self, y: np.ndarray) -> np.ndarray:
        """Paso-alto de 1 polo (~10 Hz) al INICIO de la cadena (siempre activo).

        ``y[n] = x[n] - x[n-1] + R*y[n-1]``: quita la componente continua
        (offset de algunos drivers/mixes) que roba headroom y hace trabajar de
        más al compresor y a los exciters. Estado zi persistente entre bloques."""
        rate = int(self.sample_rate)
        if self._dc_src_rate != rate:
            self._dc_R = math.exp(-2.0 * math.pi * DC_BLOCK_HZ / max(rate, 1))
            self._dc_src_rate = rate
            self._dc_state = None
        ch = y.shape[1] if y.ndim > 1 else 1
        if self._dc_state is None or self._dc_ch != ch:
            z = np.zeros(ch, dtype=np.float64) if y.ndim > 1 else 0.0
            self._dc_state = (z, z)
            self._dc_ch = ch
        y_last, x_last = self._dc_state
        out, y_last, x_last = _onepole_hp(y, self._dc_R, y_last, x_last)
        self._dc_state = (y_last, x_last)
        return out

    def process(self, data):
        data = np.asarray(data, dtype=np.float32)
        mono = data.ndim == 1
        if mono:
            data = data[:, None]
        # Robusteza ante muestras no finitas (driver/glitch): un solo NaN/inf
        # envenenaria los biquads y la envolvente del limitador para SIEMPRE
        # (salida NaN). Se sanea la entrada y se reinicia el estado dependiente
        # de la senal: la cadena se recupera ya en este bloque (transitorio
        # corto de rampas, igual que al cambiar de dispositivo).
        if not np.isfinite(data).all():
            data = np.nan_to_num(data, nan=0.0, posinf=1.0, neginf=-1.0)
            self.reset_state()
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
        # DC blocker (Fase 0): quita la continua antes de filtros y dinámica
        # (más headroom, menos trabajo inútil). Siempre activo en producción.
        if self.dc_block_enabled:
            y = self._apply_dc_blocker(y)
        nyquist = self.sample_rate / 2
        channels = data.shape[1]
        # Crossfeed BS2B al INICIO de la cadena (Input -> Crossfeed -> EQ ->
        # Comp -> Limit), como en libbs2b. Es una etapa aislada y O(n).
        if self.crossfeed:
            y = self._apply_crossfeed(y)
        if self._channels != channels:
            self._states = {}
            self._sos_cache = {}
            self._coeff_cache = {}
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
            coeffs = self._coeff_cached(
                "bass",
                (self.sample_rate, self.bass_freq, round(bass, 3)),
                lambda: self._shelf(self.bass_freq, bass, self.sample_rate, 1.0, low=True),
            )
            sections.append(self._section("bass", *coeffs))
        if treb_on:
            coeffs = self._coeff_cached(
                "treble",
                (self.sample_rate, self.treble_freq, round(treb, 3)),
                lambda: self._shelf(self.treble_freq, treb, self.sample_rate, 1.0, low=False),
            )
            sections.append(self._section("treble", *coeffs))
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
                # Q por banda con red de seguridad: si alguien asigna eq_bands
                # directamente (sin set_eq_bands) la lista de Q puede ser mas
                # corta; antes eso reventaba con IndexError en el hilo de audio.
                q = self.eq_q_values[i] if i < len(self.eq_q_values) else self._eq_q_default
                coeffs = self._coeff_cached(
                    key,
                    (self.sample_rate, freq, round(g_db, 3), q),
                    lambda freq=freq, g_db=g_db, q=q: self._peaking(freq, g_db, self.sample_rate, q),
                )
                sections.append(self._section(key, *coeffs))
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
        # Exciters (Fase 2): armónicos de graves y de presencia tras el EQ.
        y = self._bass_exciter(y, block_sec)
        y = self._clarity(y, block_sec)
        # Capa enhancer: Dynamic Boost (compresor ascendente) tras el EQ.
        y = self._dynamic_boost(y, block_sec)
        if self.compressor:
            y = self._compress(y, block_sec)
        # Leveling (AGC lento) antes del volumen del usuario (normaliza el
        # material, no el ajuste manual del usuario).
        y = self._leveling(y, block_sec)
        # Ambience/Surround (Fase 3): espacio y anchura antes del volumen.
        y = self._ambience(y, block_sec)
        y = self._surround(y, block_sec)
        y = self._apply_volume(y, block_sec)
        # Balance y Master (Fase 4): control estéreo y ganancia final, antes
        # del limitador (para que el techo siga siendo el último eslabón).
        y = self._apply_balance(y, block_sec)
        y = self._apply_master(y, block_sec)
        # Auto-curación de la cadena: si algo produjo NaN/inf (un parámetro
        # absurdo, un coeficiente degenerado, un estado zi ya envenenado) se
        # reinicia el estado y se emite UN bloque en silencio. Sin esto el NaN
        # se queda para siempre: los biquads con zi=NaN lo propagan bloque tras
        # bloque y ni el saneo de entrada (la entrada SÍ era finita) lo cura.
        # El pico se calcula aquí de todos modos para el techo de seguridad.
        peak = max(float(y.max()), -float(y.min())) if y.size else 0.0
        if not math.isfinite(peak):
            logger.warning("Cadena DSP no finita (pico=%r): se reinicia el estado", peak)
            self.reset_state()
            return np.zeros_like(y)
        if self.limiter:
            y = self._limit(y, prev_g=self._lim_last_g)
        elif y.size:
            # Techo de seguridad SIEMPRE activo: con el limitador apagado el
            # único techo era np.clip (recorte duro). El MISMO limitador
            # look-ahead cubre el hueco con techo transparente: una sola
            # implementación de ganancia suavizada que escala la señal
            # linealmente (la curva del EQ se preserva intacta). Con el
            # limitador encendido es no-op: 0.95 < 0.99 y _limit ya garantiza
            # su techo. Guarda SIN alocar (max/min son reducciones puras, sin
            # array temporal; el caso común bajo el techo cuesta dos pasadas).
            if self.safety_ceiling_enabled and peak > self.safety_ceiling:
                y = self._limit(y, thr=self.safety_ceiling, prev_g=self._lim_last_g)
            else:
                self._lim_last_g = 1.0
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
        if abs(target - start) < 1e-6:
            # Volumen estable: sin rampa ni array de ganancia por bloque.
            self._c_vol = target
            return data if target == 1.0 else data * target
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

    # ---------- capa enhancer: Dynamic Boost y Leveling (Fase 1) ----------

    def _rms_envelope(self, y: np.ndarray, slot: str) -> np.ndarray:
        """Envolvente RMS por muestra (link estéreo), ataque rápido/release lento.

        Reutiliza el truco dual de ``_compress``: dos one-pole sobre la potencia
        (rápido para subidas, lento para caídas) y se toma el máximo. El estado
        persiste por ``slot`` entre bloques (no se reinicia cada bloque)."""
        pole_att = math.exp(-1.0 / (self.sample_rate * ENV_ATTACK_S))
        pole_rel = math.exp(-1.0 / (self.sample_rate * ENV_RELEASE_S))
        p = np.mean(np.asarray(y, dtype=np.float64) ** 2, axis=1)
        # Dos one-pole vectorizados (sin scipy: cumsum) y se toma el máximo:
        # rápido para subidas, lento para caídas. El estado (y_last) persiste.
        fast, self._env_zi[slot + "_att"] = _onepole_lp(p, pole_att, self._env_zi.get(slot + "_att", 0.0))
        slow, self._env_zi[slot + "_rel"] = _onepole_lp(p, pole_rel, self._env_zi.get(slot + "_rel", 0.0))
        return np.sqrt(np.maximum(np.maximum(fast, slow), 0.0))

    @staticmethod
    def _env_db(env: np.ndarray) -> np.ndarray:
        return 20.0 * np.log10(np.maximum(env, 1e-9))

    def _smooth_effect(self, key: str, target: float, block_sec: float) -> float:
        """Suaviza la intensidad (0-10) de un efecto enhancer (tau ~30 ms).

        En reposo en 0 devuelve 0.0 sin coste; al apagar un efecto activo,
        desliza el valor hacia 0 antes de liberar el estado del filtro (sin clic)."""
        cur = self._c_leveling if key == "leveling" else self._c_effects.get(key, 0.0)
        if target <= 0.0 and cur < 1e-3:
            if cur != 0.0:
                if key == "leveling":
                    self._c_leveling = 0.0
                else:
                    self._c_effects[key] = 0.0
            return 0.0
        if abs(target - cur) < 1e-4:
            nxt = target
        else:
            nxt = self._ramp(cur, target, self._onepole(block_sec, 0.03))
            if target <= 0.0 and nxt < 1e-3:
                nxt = 0.0
        if key == "leveling":
            self._c_leveling = nxt
        else:
            self._c_effects[key] = nxt
        return nxt

    def _dynamic_boost(self, y: np.ndarray, block_sec: float) -> np.ndarray:
        """Compresor ASCENDENTE: sube los pasajes bajos sin tocar los picos.

        Envolvente RMS (ataque 5 ms / release 350 ms) -> boost = k*(objetivo-env)
        acotado a [0, +12] dB. ``amount`` 0-10 (0 = apagado)."""
        amount = self._smooth_effect("dynamic_boost", float(self.effects.get("dynamic_boost", 0.0)), block_sec)
        if amount <= 0.0 or y.shape[0] == 0:
            if self._env_zi:
                self._env_zi.pop("db_att", None)
                self._env_zi.pop("db_rel", None)
            return y
        env_db = self._env_db(self._rms_envelope(y, "db"))
        k = (amount / EFFECT_MAX) * 1.5
        boost_db = np.clip(k * (DYNAMIC_BOOST_TARGET_DB - env_db), 0.0, DYNAMIC_BOOST_MAX_DB)
        if float(boost_db.max()) <= 1e-3:
            return y  # material ya alto: nada que subir, sin alocar
        gain = (10.0 ** (boost_db / 20.0)).astype(np.float32)
        return (y * gain[:, None]).astype(np.float32, copy=False)

    def _loudness_env_db(self, y: np.ndarray) -> np.ndarray:
        """Sonoridad instantánea (LUFS) por muestra, con el filtro K de BS.1770.

        Es la misma ponderación que el medidor LUFS (misma tabla de coeficientes),
        pero aquí SIN gating y con envolvente lenta (ataque 0,5 s / release 3 s):
        lo que el AGC necesita es "cuánto suena esto", no el integrado del
        programa. El estado del filtro y de las envolventes persiste entre
        bloques por ``slot``, así que no hay saltos en las fronteras.
        """
        fs = int(self.sample_rate) or 48000
        sos = self._lv_sos
        if sos is None or self._lv_rate != fs:
            sos = k_weighting_sos(fs)
            self._lv_sos = sos
            self._lv_rate = fs
        # Dos secciones del filtro K con estado propio (slots distintos de los
        # que usan Clarity/Bass exciter).
        x = self._apply_biquad(y, "lvk1", (sos[0][0], sos[0][1], sos[0][2], sos[0][4], sos[0][5]))
        x = self._apply_biquad(x, "lvk2", (sos[1][0], sos[1][1], sos[1][2], sos[1][4], sos[1][5]))
        # Suma de canales SIN normalizar: es lo que manda BS.1770 (G_i = 1 en L/R).
        potencia = np.sum(x * x, axis=1) if x.ndim > 1 else x * x
        pole_att = math.exp(-1.0 / (fs * LEVELING_ATTACK_S))
        pole_rel = math.exp(-1.0 / (fs * LEVELING_RELEASE_S))
        rapido, self._env_zi["lvl_att"] = _onepole_lp(potencia, pole_att, self._env_zi.get("lvl_att", 0.0))
        lento, self._env_zi["lvl_rel"] = _onepole_lp(potencia, pole_rel, self._env_zi.get("lvl_rel", 0.0))
        # Detector rápido SOLO para la puerta: distingue "pausa" de "material
        # bajito" en ~100 ms (la envolvente lenta tarda segundos).
        pole_puerta = math.exp(-1.0 / (fs * LEVELING_GATE_S))
        puerta, self._env_zi["lvl_gate"] = _onepole_lp(potencia, pole_puerta, self._env_zi.get("lvl_gate", 0.0))
        self._lv_gate_db = LOUDNESS_OFFSET_DB + 10.0 * math.log10(max(float(np.median(puerta)), 1e-12))
        envolvente = np.maximum(rapido, lento)
        return LOUDNESS_OFFSET_DB + 10.0 * np.log10(np.maximum(envolvente, 1e-12))

    def _leveling(self, y: np.ndarray, block_sec: float) -> np.ndarray:
        """AGC lento por SONORIDAD (LUFS): iguala el volumen hacia el objetivo.

        Antes medía RMS, y el RMS no es sonoridad: dos pistas con el mismo RMS
        pueden diferir 6 LU o más según su contenido espectral (un bajo a 60 Hz
        "mide" mucho más en RMS de lo que suena). Aquí se mide con el filtro K
        de BS.1770, así que el AGC sube lo que el oído percibe bajo, no lo que
        más energía tiene.

        Salvaguardas (todas necesarias para que no se oiga):
        - **Gate + decaimiento**: por debajo de -70 LUFS (silencio, pausas, colas
          de fade) NO se pide corrección y la aplicada decae hacia la unidad. Un
          AGC que reaccionara a un -inf subiría al máximo en cada pausa; uno que
          congelara la corrección la aplicaría de golpe al volver el audio.
        - **Cebado**: durante el primer segundo de medida válida no actúa, para
          que el arranque de la envolvente no pida +9 dB.
        - **Suavizado entre bloques** (0.5 s): la corrección se desliza, nunca
          salta. La ganancia es un escalar por bloque (un AGC es lento por
          definición; modularla muestra a muestra la convertiría en compresor).

        Corrección acotada a ±``LEVELING_MAX_DB`` y escalada por ``amount`` 0-10
        (0 = apagado). Va ANTES del volumen del usuario: normaliza el material,
        no su ajuste manual."""
        amount = self._smooth_effect("leveling", float(self.leveling), block_sec)
        if amount <= 0.0 or y.shape[0] == 0:
            for clave in ("lvl_att", "lvl_rel", "lvl_gate", "lvk1", "lvk2"):
                self._env_zi.pop(clave, None)
                self._exc_zi.pop(clave, None)
            self._lv_corr_db = 0.0
            self._lv_valid_s = 0.0
            self._lv_gate_db = -120.0
            self._lv_gate_below_s = 0.0
            return y
        lufs = self._loudness_env_db(y)
        # Mediana del bloque: robusta frente a un pico aislado dentro de él.
        medido = float(np.median(lufs)) if lufs.size else -math.inf
        escalado = amount / EFFECT_MAX
        # La puerta usa el detector RÁPIDO: sin él, tras un silencio la
        # envolvente lenta sigue por encima de -70 durante ~13 s y el AGC
        # aplicaría el máximo al material que vuelve.
        puerta_abierta = math.isfinite(self._lv_gate_db) and self._lv_gate_db > GATE_ABSOLUTE_LUFS
        # Hold de la puerta: solo se declara pausa si el detector rápido lleva
        # LEVELING_GATE_HOLD_S por debajo del umbral (un hueco corto entre notas
        # no debe reiniciar el AGC).
        self._lv_gate_below_s = 0.0 if puerta_abierta else self._lv_gate_below_s + block_sec
        pausa = self._lv_gate_below_s >= LEVELING_GATE_HOLD_S
        if math.isfinite(medido) and medido > GATE_ABSOLUTE_LUFS and not pausa:
            self._lv_valid_s += block_sec
            objetivo = loudness_gain_db(medido, LEVELING_TARGET_LUFS, LEVELING_MAX_DB) * escalado
            # Cebado: aún sin medición asentada, no se actúa.
            destino = objetivo if self._lv_valid_s >= LEVELING_PRIME_S else 0.0
        else:
            # Silencio o pausa: la corrección DECAE hacia la unidad (no se
            # congela). Congelarla parecía más seguro, pero medido era peor: tras
            # 6 s de pausa seguía en +9 dB y lo aplicaba al material que volviera
            # (si volvía fuerte, 9 dB de más durante medio segundo). Al decaer, el
            # AGC vuelve a medir y, con el cebado, se re-engancha sin salto.
            self._lv_valid_s = 0.0
            destino = 0.0
        # Suavizado exponencial ASIMÉTRICO: bajar rápido (seguridad), subir lento.
        tau = LEVELING_SLEW_S if destino > self._lv_corr_db else LEVELING_SLEW_DOWN_S
        alfa = 1.0 - math.exp(-block_sec / tau)
        self._lv_corr_db += (destino - self._lv_corr_db) * alfa
        if abs(self._lv_corr_db) <= 1e-3:
            return y
        gain = np.float32(10.0 ** (self._lv_corr_db / 20.0))
        return (y * gain).astype(np.float32, copy=False)

    # ---------- capa enhancer: exciters Clarity y Bass (Fase 2) ----------

    def _apply_biquad(self, y: np.ndarray, slot: str, coeffs: tuple) -> np.ndarray:
        """Aplica un biquad con estado zi persistente por ``slot`` (float64)."""
        b0, b1, b2, a1, a2 = coeffs
        ch = y.shape[1] if y.ndim > 1 else 1
        zi = self._exc_zi.get(slot)
        if zi is None or zi.shape[1] != ch:
            zi = np.zeros((2, ch), dtype=np.float64)
        out, zf = _scipy_signal().lfilter([b0, b1, b2], [1.0, a1, a2], np.asarray(y, dtype=np.float64), axis=0, zi=zi)
        self._exc_zi[slot] = np.asarray(zf, dtype=np.float64)
        return out

    def _harmonic_2nd(self, band: np.ndarray, slot: str, hz: float = 20.0, adaa: bool = False) -> np.ndarray:
        """Genera el 2º armónico de ``band`` SIN continua.

        Rectificar (``|band|``) produce un 2º armónico fuerte, pero también una
        componente continua (2·a/π); se elimina con un paso-alto de 1 polo
        (~20 Hz) con estado por ``slot``. Un simple ``band·|band|`` NO serviría:
        es impar y genera 1º/3º, no 2º.

        ``adaa=True`` aplica **anti-aliasing por antiderivada** (ADAA de 1er
        orden): en vez de evaluar |x| muestra a muestra, usa la antiderivada
        F(x) = x·|x|/2 y la diferencia dividida por el paso de la señal. Los
        armónicos pares que quedarían POR ENCIMA de Nyquist ya no se pliegan de
        vuelta al espectro audible. Es O(1) por muestra (sin sobremuestreo) y el
        estado es una muestra por canal."""
        x = np.asarray(band, dtype=np.float64)
        monofonico = x.ndim == 1
        if monofonico:
            x = x[:, None]
        if adaa:
            ch = x.shape[1]
            estado = self._exc_zi.get(slot + "_adaa")
            if not isinstance(estado, tuple) or np.shape(estado[0])[0] != ch:
                estado = (np.zeros(ch, dtype=np.float64), np.zeros(ch, dtype=np.float64))
            x_prev, f_prev = estado
            # F(x) = 0.5 * x * |x| es la antiderivada de |x|.
            f = 0.5 * x * np.abs(x)
            x_ant = np.empty_like(x)
            f_ant = np.empty_like(x)
            x_ant[0] = x_prev
            f_ant[0] = f_prev
            if x.shape[0] > 1:
                x_ant[1:] = x[:-1]
                f_ant[1:] = f[:-1]
            dx = x - x_ant
            df = f - f_ant
            pequeno = np.abs(dx) <= 1e-9
            seguro = np.where(pequeno, 1.0, dx)
            # La ganancia se aplica SOLO al cociente: el fallback ya vale |x̄|.
            rect = np.where(pequeno, np.abs(0.5 * (x + x_ant)), ADAA_GAIN * df / seguro)
            self._exc_zi[slot + "_adaa"] = (x[-1].copy(), f[-1].copy())
        else:
            rect = np.abs(x)
        R = math.exp(-2.0 * math.pi * hz / max(int(self.sample_rate), 1))
        ch = rect.shape[1]
        state = self._exc_zi.get(slot)
        if not isinstance(state, tuple):
            z = np.zeros(ch, dtype=np.float64)
            state = (z, z)
        out, y_last, x_last = _onepole_hp(rect, R, state[0], state[1])
        self._exc_zi[slot] = (y_last, x_last)
        return out[:, 0] if monofonico else out

    def _harmonic_3rd(self, band: np.ndarray, slot: str, hz: float = BASS_EXCITER_HP3_HZ) -> np.ndarray:
        """Genera el 3er armónico de ``band`` (impar, sin la fundamental).

        Generador CÚBICO normalizado por la potencia de la banda
        (``x³ / media(x²)``): así el nivel del armónico no depende de cuánto
        suba o baje el material. Para un seno de amplitud A, ``x³/A²`` produce un
        3er armónico a -12 dB de la fundamental; ``x·|x|`` lo dejaba a -37 dB
        (medido), es decir inaudible. El cúbico también contiene la fundamental,
        así que se quita con un paso-alto por encima de la banda de entrada: en
        graves eso deja pasar el 3F (120 Hz para una fundamental de 40 Hz), que
        es la zona donde un altavoz pequeño SÍ reproduce. Ese es el hueco que
        dejaba el 2º armónico solo (80 Hz)."""
        x = np.asarray(band, dtype=np.float64)
        monofonico = x.ndim == 1
        if monofonico:
            x = x[:, None]
        # Potencia POR CANAL y suavizada (~150 ms). Con la media del bloque:
        # (a) un bloque medio lleno de señal daba +6 dB de 3F de golpe -> modulación
        # audible a la tasa de bloque (47 Hz) con material articulado;
        # (b) un seno de 40 Hz (0,85 ciclos por bloque) rizaba ±1,3 dB;
        # (c) un bajo paneado a un solo canal recibía +6 dB respecto al centrado.
        potencia_inst = np.mean(x * x, axis=0)
        previa = self._exc_zi.get(slot + "_pow")
        if not isinstance(previa, np.ndarray) or previa.shape != potencia_inst.shape:
            potencia = potencia_inst
        else:
            potencia = 0.85 * previa + 0.15 * potencia_inst
        self._exc_zi[slot + "_pow"] = potencia
        cubico = (x * x * x) / (potencia + 1e-12) * BASS_EXCITER_H3_SCALE
        # Paso-alto de 4º orden (dos biquads en cascada): con uno solo, la
        # fundamental de 60 Hz fugaba a -14,8 dB y quedaba a 2 dB del propio 3F.
        # Con dos, la fuga baja a ~-30 dB y el 3F domina con claridad.
        filtrado = self._apply_biquad(cubico, slot, self._highpass(hz, self.sample_rate))
        filtrado = self._apply_biquad(filtrado, slot + "b", self._highpass(hz, self.sample_rate))
        out = np.asarray(filtrado, dtype=np.float64)
        return out[:, 0] if monofonico else out

    def _clarity(self, y: np.ndarray, block_sec: float) -> np.ndarray:
        """Exciter de presencia (Fidelity/Clarity): 2º armónico de 3 kHz+.

        Filtra la banda de presencia y añade su 2º armónico (rectificado, sin
        continua, suavizado con paso-bajo anti-aliasing a ~16 kHz): restaura
        detalle sin subir esa banda de volumen (contenido NUEVO, no EQ). 0-10."""
        amount = self._smooth_effect("fidelity", float(self.effects.get("fidelity", 0.0)), block_sec)
        if amount <= 0.0 or y.shape[0] == 0:
            if self._exc_zi:
                self._exc_zi.pop("cl_hp", None)
                self._exc_zi.pop("cl_h2", None)
                self._exc_zi.pop("cl_h2_adaa", None)
                self._exc_zi.pop("cl_lp", None)
            return y
        hp = self._apply_biquad(y, "cl_hp", self._highpass(CLARITY_HP_HZ, self.sample_rate))
        # ADAA en la no linealidad: sin esto, los armónicos pares por encima de
        # Nyquist (para la banda de 3 kHz+: 4º=12 kHz, 6º=18 kHz, 8º=24 kHz...) se
        # pliegan de vuelta al espectro audible como tonos que no existen en la
        # señal original. El paso-bajo de después NO los quitaba: el aliasing ya
        # había ocurrido al rectificar.
        h = self._harmonic_2nd(hp, "cl_h2", adaa=True)
        # Paso-bajo anti-aliasing (~16 kHz, biquad 2º orden): atenúa armónicos
        # pares altos (4º, 6º...) cercanos a Nyquist sin tocar el 2º armónico útil.
        lp_hz = min(CLARITY_LP_HZ, 0.42 * max(int(self.sample_rate), 1))
        h = self._apply_biquad(h, "cl_lp", self._lowpass(lp_hz, self.sample_rate))
        g = (amount / EFFECT_MAX) * CLARITY_MIX
        return (y + (g * h).astype(np.float32)).astype(np.float32, copy=False)

    def _bass_exciter(self, y: np.ndarray, block_sec: float) -> np.ndarray:
        """Exciter de graves (Bass): 2º Y 3er armónico de la banda de graves.

        Por qué dos armónicos: el 2º de una fundamental de 40 Hz cae en 80 Hz,
        donde un altavoz pequeño (corte real 150-250 Hz) no reproduce nada; el 3º
        cae en 120 Hz y sí se oye. Antes solo había 2º, así que el efecto era
        inaudible justo en los equipos donde se supone que ayuda, y en
        auriculares podía confundirse con un error de octava (fundamental una
        octava arriba). Generar también el 3º reparte la energía en la serie
        armónica y suena a "más grave", no a "una octava más".

        La banda de entrada llega a 120 Hz y el 3er armónico se filtra por encima
        de esa banda para no realzar el grave (eso ya lo hace el EQ). 0-10."""
        amount = self._smooth_effect("bass", float(self.effects.get("bass", 0.0)), block_sec)
        if amount <= 0.0 or y.shape[0] == 0:
            if self._exc_zi:
                for clave in ("bx_lp", "bx_h2", "bx_h3", "bx_h3b", "bx_h3_pow"):
                    self._exc_zi.pop(clave, None)
            return y
        lp = self._apply_biquad(y, "bx_lp", self._lowpass(BASS_EXCITER_LP_HZ, self.sample_rate))
        h2 = self._harmonic_2nd(lp, "bx_h2")
        h3 = self._harmonic_3rd(lp, "bx_h3")
        escala = amount / EFFECT_MAX
        anadido = escala * (BASS_EXCITER_MIX_H2 * h2 + BASS_EXCITER_MIX_H3 * h3)
        return (y + anadido.astype(np.float32)).astype(np.float32, copy=False)

    # ---------- capa enhancer: Ambience y Surround (Fase 3) ----------

    def _ambience(self, y: np.ndarray, block_sec: float) -> np.ndarray:
        """Reflexiones tempranas decorreladas (3 taps por canal).

        Historial O(n) (sin allocations por tap): se concatenan el historial y
        el bloque y se leen los taps a retardos primos distintos por canal
        (L: 11/23/37 ms, R: 13/27/41 ms) con ganancias decrecientes. Añade
        sensación de espacio (útil sobre todo con auriculares)."""
        amount = self._smooth_effect("ambience", float(self.effects.get("ambience", 0.0)), block_sec)
        if amount <= 0.0 or y.shape[0] == 0 or y.ndim < 2 or y.shape[1] < 2:
            self._amb_hist = None
            return y
        rate = int(self.sample_rate)
        max_delay = max(int(rate * d / 1000.0) for d, _ in AMBIENCE_TAPS_L + AMBIENCE_TAPS_R)
        hist = self._amb_hist
        if hist is None or hist.shape != (max_delay, 2):
            hist = np.zeros((max_delay, 2), dtype=np.float32)
        n = y.shape[0]
        ext = np.concatenate([hist, y], axis=0)
        wet = np.zeros_like(y)
        for taps, ch in ((AMBIENCE_TAPS_L, 0), (AMBIENCE_TAPS_R, 1)):
            for ms, gain in taps:
                d = int(rate * ms / 1000.0)
                start = max_delay - d
                wet[:, ch] += gain * ext[start : start + n, ch]
        self._amb_hist = np.ascontiguousarray(ext[-max_delay:])
        mix = (amount / EFFECT_MAX) * AMBIENCE_MIX
        return (y + mix * wet).astype(np.float32, copy=False)

    def _surround(self, y: np.ndarray, block_sec: float) -> np.ndarray:
        """Ensanchado M/S con mono-bass y guard de correlación.

        Ensancha la componente Side (``S *= 1 + amount/10·0.8``) SOLO por encima
        de 150 Hz: los graves quedan al centro (mono-bass) para conservar la
        compatibilidad mono. Si la señal ya es ancha (correlación L/R < 0.2), el
        guard reduce el ensanchado. El factor del guard se suaviza (~50 ms)."""
        amount = self._smooth_effect("surround", float(self.effects.get("surround", 0.0)), block_sec)
        if amount <= 0.0 or y.shape[0] == 0 or y.ndim < 2 or y.shape[1] < 2:
            if self._exc_zi:
                self._exc_zi.pop("sr_hp", None)
            self._sr_boost = 0.0
            return y
        m = 0.5 * (y[:, 0] + y[:, 1])
        s = 0.5 * (y[:, 0] - y[:, 1])
        s_hp = self._apply_biquad(s[:, None], "sr_hp", self._highpass(SURROUND_MONO_BASS_HZ, self.sample_rate))[:, 0]
        s_low = s - s_hp
        # Guard de correlación (bloque) suavizado con un polo.
        el = float(np.dot(y[:, 0], y[:, 0]))
        er = float(np.dot(y[:, 1], y[:, 1]))
        cross = float(np.dot(y[:, 0], y[:, 1]))
        denom = math.sqrt(max(el * er, 1e-12))
        corr = cross / denom if denom > 0 else 1.0
        target = 1.0 if corr >= SURROUND_CORR_MIN else max(0.0, corr / SURROUND_CORR_MIN)
        self._sr_boost = self._ramp(self._sr_boost, target, self._onepole(block_sec, 0.05))
        boost = 1.0 + (amount / EFFECT_MAX) * SURROUND_WIDTH * self._sr_boost
        s_new = s_low + s_hp * boost
        out = y.copy()
        out[:, 0] = m + s_new
        out[:, 1] = m - s_new
        return out

    # ---------- capa enhancer: Balance y Master (Fase 4) ----------

    def _apply_balance(self, y: np.ndarray, block_sec: float) -> np.ndarray:
        """Balance L/R con ley equal-power (±20). El centro es unidad (sin cambio).

        A ±20 el canal opuesto queda mudo y el otro sube ~+3 dB; en el centro
        ambos quedan a 1.0. El valor se suaviza (~50 ms) para no chasquear."""
        self._c_balance_db = self._ramp(self._c_balance_db, self.balance_db, self._onepole(block_sec, 0.05))
        if abs(self._c_balance_db) < 1e-4 or y.ndim < 2 or y.shape[1] < 2:
            return y
        p = max(-1.0, min(1.0, self._c_balance_db / BALANCE_DB_MAX))
        theta = (p + 1.0) * math.pi / 4.0
        gl = math.cos(theta) * math.sqrt(2.0)
        gr = math.sin(theta) * math.sqrt(2.0)
        out = y.copy()
        out[:, 0] *= gl
        out[:, 1] *= gr
        return out

    def _apply_master(self, y: np.ndarray, block_sec: float) -> np.ndarray:
        """Master gain (dB, ±20) con rampa POR MUESTRA (anti-clic).

        Misma recurrencia que ``_apply_volume``: la ganancia se desliza muestra
        a muestra (tau ~100 ms) para que un salto del slider no se oiga."""
        target = 10.0 ** (self.master_gain_db / 20.0)
        start = self._c_master
        if abs(target - start) < 1e-6:
            self._c_master = target
            if abs(target - 1.0) < 1e-9:
                return y
            return (y * target).astype(np.float32, copy=False)
        n = y.shape[0]
        ramp = _ramp_falling(n, self.sample_rate, 0.10)
        gain = target + (start - target) * ramp
        self._c_master = float(gain[-1])
        return (y * gain[:, None]).astype(np.float32, copy=False)

    def _limit(self, y, thr=None, prev_g: float | None = None):
        """Limitador brickwall con look-ahead de 3 ms (techo garantizado).

        Reemplaza al antiguo "soft limiter" por muestra: esa curva dependía de
        `strength` y dejaba pasar hasta ~70% del exceso (un pico de 1.4 salía
        ~1.14) porque atenuaba poco y SIN anticipación: cuando la ganancia
        reaccionaba, el transitorio ya había cruzado.

        ``thr`` permite reutilizar ESTA MISMA maquinaria (look-ahead + Hann +
        garantía de techo) como techo de seguridad cuando el limitador
        musical está apagado: un solo código de suavizado de ganancia que
        preserva la forma espectral (escala lineal), sin duplicados.
        ``prev_g`` encadena la ganancia final del bloque previo para evitar
        escalones de ganancia en la frontera entre bloques consecutivos.

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
        below_thr = (sample_peak <= thr / 1.414) if self.true_peak else (sample_peak <= thr)
        if below_thr:
            if prev_g is not None:
                self._lim_last_g = 1.0
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
        if prev_g is not None and prev_g < float(g[0]):
            left_pad = np.full(half, prev_g, dtype=g.dtype)
        else:
            left_pad = np.full(half, g[0], dtype=g.dtype)
        g_pad = np.concatenate([left_pad, g, np.full(half, g[-1], dtype=g.dtype)])
        g_s = np.minimum(np.convolve(g_pad, kernel, mode="valid")[: y.shape[0]], 1.0)
        out = y * g_s[:, None].astype(np.float32)
        # Garantía de techo: el suavizado puede subestimar la reducción en
        # transitorios muy cortos. En modo true-peak hay que MEDIR el true-peak
        # de la salida; pero solo vale la pena cuando el sample-peak está cerca
        # del techo (si no, ni asumiendo el sobrepico máximo ×1.414 lo cruza).
        # Eso evita una 2ª pasada de sobremuestreo en el caso común.
        scale = 1.0
        sample_peak_out = float(np.abs(out).max())
        if self.true_peak:
            if sample_peak_out > thr / 1.414:
                peak = float(np.abs(_true_peak_oversample(out)).max())
                if peak > thr:
                    scale = thr / peak
                    out *= scale
        elif sample_peak_out > thr:
            scale = thr / sample_peak_out
            out *= scale
        if prev_g is not None:
            self._lim_last_g = float(g_s[-1]) * scale
        # GR (gain reduction) del bloque en dB: 0 = sin limitar, negativo =
        # atenuando. La UI lo muestra en un medidor para ajustar sin ir a
        # ciegas. Se toma el mínimo de la ganancia suavizada (el peor caso).
        min_gain = float(g_s.min()) * scale if g_s.size else 1.0
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
        # Ese tope tiene que ser un índice VÁLIDO: si el techo cae justo en
        # Nyquist (cualquier tasa <= 40 kHz: 32k, 22.05k, 16k...) searchsorted
        # devuelve len(spec) y reduceat aborta con IndexError, dejando el
        # espectro muerto (se veía solo como un log a nivel debug). Un cero
        # extra al final hace válido el tope sin alterar ninguna suma.
        spec_pad = np.concatenate([spec, np.zeros(1, dtype=spec.dtype)])
        sums = np.add.reduceat(spec_pad, idx)[:64]
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
            # Una sola pasada de reducciones para ambos canales (antes: 4
            # np.max/np.mean + 2 cuadrados por canal -> overhead de llamadas).
            pk = np.abs(y).max(axis=0)
            rms = np.sqrt(np.mean(np.multiply(y, y), axis=0))
            peak_l, peak_r = float(pk[0]), float(pk[1])
            rms_l, rms_r = float(rms[0]), float(rms[1])
        else:  # mono: ambos canales idénticos
            y = np.ravel(y)
            peak_l = peak_r = float(np.abs(y).max())
            rms_l = rms_r = float(np.sqrt(np.mean(np.multiply(y, y))))
        self.level_peak_l = self.level_peak_l * 0.7 + peak_l * 0.3
        self.level_peak_r = self.level_peak_r * 0.7 + peak_r * 0.3
        self.level_rms_l = self.level_rms_l * 0.85 + rms_l * 0.15
        self.level_rms_r = self.level_rms_r * 0.85 + rms_r * 0.15
        self.level_peak = max(self.level_peak_l, self.level_peak_r)
        self.level_rms = max(self.level_rms_l, self.level_rms_r)
