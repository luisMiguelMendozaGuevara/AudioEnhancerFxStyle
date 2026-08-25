"""Cadena DSP de Audio Enhancer FxStyle (puro numpy/scipy, sin UI).

``Enhancer`` procesa bloques de audio en el hilo de captura de PortAudio.
Thread-safety: el hilo de audio (callbacks) lee los *targets mientras la UI
escribe en volume/bass/treble/eq_gains/blend. Para evitar zipper noise y
estados corruptos, process() desliza los valores "actuales" (_c*) hacia los
objetivos con una rampa exponencial por bloque; los filtros se rediseñan cada
bloque con el valor suavizado. Nunca se escribe el estado del DSP desde el
callback.
"""

import math
import threading

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


# Umbral de activación de una sección de filtro (en dB): por debajo se
# considera ganancia nula y la sección no se apila (estado conservado).
SECTION_GAIN_EPS = 0.1

EQ_BANDS = [60, 150, 250, 500, 1000, 2000, 4000, 8000, 12000]


class Enhancer:
    """Cadena DSP: biquads RBJ (Low/High Shelf + Peaking) con sosfilt+zi,
    compresor RMS dinámico, limitador suave y suavizado de parámetros."""

    def __init__(self) -> None:
        self.sample_rate: int = SAMPLE_RATE
        self.eq_bands: list[float] = list(EQ_BANDS)
        self.eq_gains: list[float] = [0.0] * len(self.eq_bands)
        self.bass: float = 0.0
        self.treble: float = 0.0
        self.volume: float = 1.0
        self.bass_freq: float = 150.0  # corte del Low Shelf
        self.treble_freq: float = 6000.0  # corte del High Shelf
        self.eq_q: float = 1.4  # Q de las bandas peaking
        # Limitador suave (seguridad nivel final)
        self.limiter: bool = True
        self.limiter_threshold: float = 0.95
        self.limiter_strength: float = 0.6
        # Compresor RMS dinámico (loudness)
        self.compressor: bool = True
        self.comp_threshold: float = 0.85  # ~-1.4 dBFS
        self.comp_ratio: float = 4.0  # compresión 4:1
        self.comp_attack: float = 0.005  # segundos
        self.comp_release: float = 0.2  # segundos
        self.comp_makeup: float = 1.0
        self._gain_db: float = 0.0
        # Umbral del compresor en dB: solo depende de comp_threshold, que la UI
        # cambia rara vez; se recalcula al detectar el cambio (evita un log10
        # escalar por bloque).
        self._thr_src: float = self.comp_threshold
        self._thr_db: float = 20.0 * math.log10(max(self.comp_threshold, 1e-9))
        # A/B crossfade: blend=1 efectos, blend=0 directo
        self.blend: float = 1.0
        # Medidor
        self.level_rms: float = 0.0
        self.level_peak: float = 0.0
        # Valores suavizados actuales (rampa anti-cremallera)
        self._c_vol: float = 1.0
        self._c_bass: float = 0.0
        self._c_treble: float = 0.0
        self._c_eq = np.zeros(len(self.eq_bands), dtype=np.float32)
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
        self._ramp_cache: dict = {}  # cache (n, sample_rate) -> rampa

    # ---------- diseño de filtros RBJ (Audio EQ Cookbook) ----------

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
        y = np.clip(y, -1.0, 1.0).astype(np.float32)
        self._measure_levels(y)
        return y[:, 0] if mono else y

    def _process_dsp(self, data, block_sec):
        y = data.copy()
        nyquist = self.sample_rate / 2
        channels = data.shape[1]
        if self._channels != channels:
            self._states = {}
            self._sos_cache = {}
            self._channels = channels
        # rampa anti-cremallera de los objetivos de la UI (el volumen se
        # suaviza aparte, por muestra, en _apply_volume)
        alpha_eq = 1.0 - np.exp(-block_sec / 0.03)
        self._c_bass = self._ramp(self._c_bass, self.bass, alpha_eq)
        self._c_treble = self._ramp(self._c_treble, self.treble, alpha_eq)
        self._c_eq = self._ramp(self._c_eq, np.asarray(self.eq_gains, dtype=np.float32), alpha_eq)
        bass = float(self._c_bass)
        treb = float(self._c_treble)
        # Apila las secciones activas en una sola matriz SOS: sosfilt aplica la
        # cascada en el orden dado (bass -> treble -> eq0..8), idéntico a llamar
        # por sección, con una única ida y vuelta a scipy.
        sections = []
        if abs(bass) >= SECTION_GAIN_EPS and self.bass_freq < nyquist:
            b0, b1, b2, a1, a2 = self._shelf(self.bass_freq, bass, self.sample_rate, 1.0, low=True)
            sections.append(self._section("bass", b0, b1, b2, a1, a2))
        if abs(treb) >= SECTION_GAIN_EPS and self.treble_freq < nyquist:
            b0, b1, b2, a1, a2 = self._shelf(self.treble_freq, treb, self.sample_rate, 1.0, low=False)
            sections.append(self._section("treble", b0, b1, b2, a1, a2))
        for i, (freq, g) in enumerate(zip(self.eq_bands, self._c_eq, strict=False)):
            if abs(float(g)) >= SECTION_GAIN_EPS and freq < nyquist:
                b0, b1, b2, a1, a2 = self._peaking(freq, float(g), self.sample_rate, self.eq_q)
                sections.append(self._section("eq_%d" % i, b0, b1, b2, a1, a2))
        if sections:
            sos = np.concatenate([s[1] for s in sections], axis=0)
            zi = np.stack([s[2] for s in sections])
            out, zi_out = _scipy_signal().sosfilt(sos, y, axis=0, zi=zi)
            for s, z in zip(sections, zi_out, strict=False):
                self._states[s[0]] = np.asarray(z, dtype=np.float32)
            y = out
        y = self._apply_volume(y, block_sec)
        if self.compressor:
            y = self._compress(y, block_sec)
        if self.limiter:
            y = self._soft_limiter(y, self.limiter_threshold, self.limiter_strength)
        return np.clip(y, -1.0, 1.0).astype(np.float32)

    def _apply_volume(self, data, block_sec):
        """Aplica el volumen con una rampa exponencial POR MUESTRA (tau ~100 ms).

        Antes se acercaba al objetivo en un solo bloque (~21 ms): al arrastrar
        el slider rápido el salto de ganancia se oía como un clic. Aquí la
        ganancia se desliza muestra a muestra, sin discontinuidades."""
        target = float(self.volume)
        start = self._c_vol
        n = data.shape[0]
        tau = 0.10  # segundos
        key = (n, self.sample_rate)
        ramp = self._ramp_cache.get(key)
        if ramp is None:
            # Misma recurrencia que lfilter, expresada directamente. La rampa
            # solo depende de (n, sample_rate): se cachea para no alocar
            # arange + power en cada callback.
            pole = float(np.exp(-1.0 / (self.sample_rate * tau)))
            steps = np.arange(1, n + 1, dtype=np.float32)
            ramp = np.power(pole, steps).astype(np.float32, copy=False)
            self._ramp_cache[key] = ramp
        gain = target + (start - target) * ramp
        self._c_vol = float(gain[-1])
        return data * gain[:, None]

    def _compress(self, y, block_sec):
        """Compresor RMS feed-forward con attack/release y make-up gain."""
        rms = float(np.sqrt(np.mean(y * y))) if y.size else 0.0
        db = 20.0 * math.log10(rms) if rms > 1e-9 else -90.0
        if self._thr_src != self.comp_threshold:
            self._thr_src = self.comp_threshold
            self._thr_db = 20.0 * math.log10(max(self.comp_threshold, 1e-9))
        over = db - self._thr_db
        # over > 0 => la senal supera el umbral: la reduccion va en dB negativos
        # (atenuar). El monolito original usaba el valor positivo, lo que
        # amplificaba en vez de comprimir.
        target_db = -over * (1.0 - 1.0 / self.comp_ratio) if over > 0 else 0.0
        if target_db < self._gain_db:
            coeff = 1.0 - math.exp(-block_sec / max(self.comp_attack, 1e-4))
        else:
            coeff = 1.0 - math.exp(-block_sec / max(self.comp_release, 1e-4))
        self._gain_db += (target_db - self._gain_db) * coeff
        gain = (10.0 ** (self._gain_db / 20.0)) * self.comp_makeup
        return y * gain

    @staticmethod
    def _soft_limiter(x, threshold, strength):
        """Curva de ganancia suave: deja pasar <=threshold, comprime después."""
        a = np.abs(x)
        # Caso común (bloque por debajo del umbral): una sola reducción y
        # salir, sin alocar los arrays temporales de la curva de ganancia.
        if a.size == 0 or float(np.max(a)) <= threshold:
            return x
        over = a - threshold
        k = 1.0 / (1.0 + np.exp(-5.0 * over))  # transición suave 0..1
        g = 1.0 - strength * (over / (threshold + 1e-9)) * k
        g = np.clip(g, 0.0, 1.0)
        return x * np.where(over > 0, g, 1.0)

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
        """Actualiza el medidor de nivel (RMS y pico suavizados) del bloque."""
        peak = float(np.max(np.abs(y))) if y.size else 0.0
        rms = float(np.sqrt(np.mean(y**2))) if y.size else 0.0
        self.level_peak = self.level_peak * 0.7 + peak * 0.3
        self.level_rms = self.level_rms * 0.85 + rms * 0.15
