#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audio Enhancer estilo FxSound usando WASAPI loopback.

Motor de audio: PyAudioWPatch (PortAudio con WASAPI loopback) en MODO CALLBACK:
entrada = loopback del cable virtual donde suenan las apps; salida = parlantes
físicos. El callback es sincronizado por el hardware, lo que elimina los
chasquidos/underruns del bucle manual record()/play().
"""
import threading
import time
import json
import os
import sys
import locale
import tkinter as tk
import customtkinter as ctk

import numpy as np
from scipy import signal

try:
    import pystray
    from PIL import Image
    _HAVE_TRAY = True
except Exception:
    pystray = None
    Image = None
    _HAVE_TRAY = False

_pa_mod = None


def _pa():
    """Import perezoso de pyaudiowpatch (arranque mas rapido)."""
    global _pa_mod
    if _pa_mod is None:
        import pyaudiowpatch
        _pa_mod = pyaudiowpatch
    return _pa_mod

ACCENT = "#0078D4"       # Azul Fluent
DANGER = "#d13438"
OK = "#107c10"
WARN = "#9d5d00"

def _resource_path(name):
    """Ruta a un asset embebido: valida para .exe (PyInstaller) y fuente .py."""
    base = getattr(sys, "_MEIPASS", "")
    dirs = []
    if base:
        dirs.append(os.path.join(base, "assets"))
        dirs.append(base)
    dirs.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"))
    dirs.append(os.path.dirname(os.path.abspath(__file__)))
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return name

CONFIG_PATH = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "AudioEnhancerFxStyle",
    "config.json",
)

SAMPLE_RATE = 48000
CHUNK = 1024
RING_SECONDS = 0.2

# Localización: inglés es el idioma de respaldo.
TRANSLATIONS = {
    "Audio Enhancer": "Audio Enhancer",
    "Procesamiento del audio del sistema vía WASAPI loopback": "System audio processing via WASAPI loopback",
    "Dispositivos y ruteo": "Devices and routing",
    "Captura (loopback):": "Capture (loopback):",
    "Salida (física):": "Output (physical):",
    "Actualizar dispositivos": "Refresh devices",
    "Efectos": "Effects",
    "Configuración:": "Preset:",
    "A: Efectos ON": "A: Effects ON",
    "B: Directo (OFF)": "B: Direct (OFF)",
    "Nuevo preset:": "New preset:",
    "nombre del preset": "preset name",
    "Guardar": "Save",
    "Borrar": "Delete",
    "Volumen": "Volume",
    "Bass Boost (dB)": "Bass Boost (dB)",
    "Treble Boost (dB)": "Treble Boost (dB)",
    "Limitador suave": "Soft limiter",
    "Compresor RMS": "RMS compressor",
    "Nivel:": "Level:",
    "Ecualizador (9 bandas)": "Equalizer (9 bands)",
    "Analizador de espectro": "Spectrum analyzer",
    "Iniciar audio del sistema": "Start system audio",
    "Detener audio del sistema": "Stop system audio",
    "Instalar loopback propio (VB-CABLE)": "Install local loopback (VB-CABLE)",
    "Restablecer": "Reset",
    "Iniciar con Windows": "Start with Windows",
    "Listo. Configura el ruteo y pulsa Iniciar.": "Ready. Configure routing and press Start.",
    "Procesamiento detenido": "Processing stopped",
    "Mostrar / Ocultar": "Show / Hide",
    "Iniciar / Detener": "Start / Stop",
    "Salir": "Exit",
}


def detect_system_language():
    """Devuelve 'es' para Windows en español y 'en' para el resto."""
    try:
        lang = locale.getlocale()[0] or ""
    except Exception:
        lang = ""
    return "es" if lang.lower().startswith("es") else "en"


def translate(text, language):
    if language == "en":
        return TRANSLATIONS.get(text, text)
    return text

# Descripciones mostradas en los tooltips de la interfaz.
EXPLAIN = {
    "volumen": "Ganancia final del audio procesado. 1.0x mantiene el nivel; "
               "valores mayores aumentan el volumen y pueden activar el limitador. "
               "Úsalo con moderación para evitar saturación y fatiga auditiva.",
    "bass": "Refuerzo tipo shelf de graves, aproximadamente por debajo de 150 Hz. "
            "Aporta peso a bombos, bajos y explosiones. Si retumba o distorsiona, "
            "reduce este control o el volumen.",
    "treble": "Refuerzo tipo shelf de agudos, aproximadamente por encima de 6 kHz. "
              "Aporta claridad a voces, platos y detalles. Demasiado puede producir "
              "sibilancias o un sonido áspero.",
    "eq": "Cada banda aumenta o reduce una zona de frecuencias alrededor de su "
          "frecuencia central. Los valores positivos (+dB) realzan y los negativos "
          "(-dB) atenúan. Q=1.4 produce una curva de anchura media; mueve poco a "
          "poco los controles para evitar cambios bruscos.",
    "limiter": "Limitador suave: evita que los picos superen aproximadamente "
                "-0.4 dBFS. En lugar de cortar la señal de golpe, reduce la ganancia "
                "progresivamente para disminuir clipping y distorsión. Puede reducir "
                "algo la dinámica si el nivel es muy alto.",
    "compressor": "Compresor RMS: calcula el nivel medio de cada bloque de audio y "
                  "reduce gradualmente las partes demasiado fuertes. Suaviza la "
                  "dinámica y hace más uniforme el volumen entre voces y música; "
                  "no es un aumento de volumen automático y un exceso puede sonar "
                  "aplastado.",
}

EXPLAIN_EN = {
    "volumen": "Final gain of the processed audio. 1.0x keeps the level; higher values increase volume and may engage the limiter.",
    "bass": "Low-shelf boost below approximately 150 Hz. Adds weight to kick drums and bass; reduce it if the sound becomes boomy.",
    "treble": "High-shelf boost above approximately 6 kHz. Adds clarity and detail; too much can sound harsh or sibilant.",
    "eq": "Each band boosts (+dB) or cuts (-dB) a frequency range around its center frequency. Q=1.4 gives a medium-width curve.",
    "limiter": "Soft limiter: gently reduces peaks near 0 dBFS to prevent clipping and distortion instead of cutting them abruptly.",
    "compressor": "RMS compressor: measures average loudness and gradually reduces overly loud sections, making volume more consistent.",
}

EQ_EXPLAIN = {
    60: "Subgrave y grave profundo: golpes de bombo, sub-bajo y rumble. Realzarlo da peso; reducirlo limpia vibraciones.",
    150: "Grave alto: cuerpo de bombos, bajos y voces masculinas. Demasiado produce sonido boomy o retumbante.",
    250: "Grave medio: calidez y cuerpo. Reducirlo puede quitar barro; aumentarlo puede engrosar guitarras y voces.",
    500: "Medio bajo: cuerpo de instrumentos y voces. Atenuarlo ayuda a limpiar una mezcla congestionada.",
    1000: "Medio central: presencia general de voces, guitarras y teclados. Cambios aquí son muy perceptibles.",
    2000: "Medio alto: inteligibilidad y ataque. Realzarlo mejora definición, pero puede volver el sonido nasal o agresivo.",
    4000: "Presencia: detalle de consonantes, guitarras y percusión. Demasiado puede sonar duro o fatigante.",
    8000: "Agudo: brillo, platos y aire inicial. Útil para claridad; exceso aumenta sibilancias y ruido.",
    12000: "Aire: brillo fino y sensación de apertura. Realza detalles sutiles; reducirlo suaviza grabaciones ásperas.",
}

# Configuraciones predeterminadas: nombre -> (volumen, bass, treble, eq_gains)
# EQ bandas: [60, 150, 250, 500, 1000, 2000, 4000, 8000, 12000] Hz
# Curvas pensadas para el flujo de la app: bass/treble actuan como "shelf"
# (graves < 150 Hz y agudos > 6 kHz) y el EQ de 9 bandas hace el ajuste fino.
PRESETS = {
    "Plano (sin efectos)": (1.0, 0.0, 0.0, [0, 0, 0, 0, 0, 0, 0, 0, 0]),
    "Graves (Bass)": (1.0, 5.0, 0.5, [4, 3, 1.5, 0, 0, 0, 0, 0.5, 0.5]),
    "Música (V suave)": (1.0, 2.5, 1.5, [2, 1.5, 0, -0.5, 0, 0, 0.5, 1.5, 2]),
    "Clásica / Acústica": (1.0, 1.0, 2.0, [1, 0.5, 0.5, 0, 0, 0, 0.5, 2, 2.5]),
    "Rock": (1.0, 3.0, 1.5, [3, 2, 0.5, -0.5, -0.5, 0.5, 1, 2, 3]),
    "Electrónica": (1.0, 6.0, 1.0, [6, 5, 3, 0, 0.5, 1, 1, 1.5, 1.5]),
    "Voz / Podcast": (1.0, 0.0, 0.5, [0, 0, -0.5, 1, 3, 4.5, 3, 1, 0]),
    "Cine / Series": (1.1, 4.0, 1.5, [4, 3, 1.5, -0.5, 0, 0.5, 1, 2, 2.5]),
    "Noche (vol. baja)": (0.45, 3.0, 0.5, [3, 2, 1.5, 0.5, 0, 0, 0, 1, 1.5]),
    "Agudos (Treble)": (1.0, 0.0, 4.0, [0, 0, 0, 0, 0, 0, 1, 2.5, 3.5]),
}


class ToolTip:
    """Tooltip ligero basado en eventos, sin sondeo periódico de la UI."""

    def __init__(self, widget, text, delay=550, max_seconds=8):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.max_ms = max_seconds * 1000
        self.tip = None
        self._after_id = None
        self._hide_after_id = None
        target = widget
        for attr in ("_canvas", "_draw_engine"):
            inner = getattr(widget, attr, None)
            if inner is not None and hasattr(inner, "bind"):
                target = inner
                break
        self.target = target
        target.bind("<Enter>", self._on_enter, add="+")
        target.bind("<Leave>", self._on_leave, add="+")
        target.bind("<ButtonPress>", self._hide_now, add="+")

    def _on_enter(self, _=None):
        self._cancel_scheduled()
        if self.text:
            self._after_id = self.widget.after(self.delay, self._show_now)

    def _on_leave(self, _=None):
        self._cancel_scheduled()
        self._hide_now()

    def _cancel_scheduled(self):
        for attr in ("_after_id", "_hide_after_id"):
            ident = getattr(self, attr)
            if ident is not None:
                try:
                    self.widget.after_cancel(ident)
                except Exception:
                    pass
                setattr(self, attr, None)

    def _elapsed_ms(self):
        try:
            return int((time.time() - self._born_ms) * 1000)
        except Exception:
            return 0

    # ---------- mostrar / ocultar ----------

    def _show_now(self):
        if self.tip is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 22
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
            self.tip = ctk.CTkToplevel(self.widget.winfo_toplevel())
            self.tip.wm_overrideredirect(True)
            self.tip.geometry("+%d+%d" % (x, y))
            self.tip.attributes("-topmost", True)
            label = ctk.CTkLabel(self.tip, text=self.text, wraplength=300,
                                 justify="left", font=("Segoe UI", 11),
                                 fg_color=("gray88", "gray18"),
                                 text_color=("gray10", "gray90"),
                                 corner_radius=8)
            label.pack(padx=8, pady=6)
            self._make_click_through(label)
            self.tip.lift()
            self._born_ms = time.time()
            self._after_id = None
            self._hide_after_id = self.widget.after(self.max_ms, self._hide_now)
        except Exception:
            self.tip = None

    @staticmethod
    def _make_click_through(label):
        """La ventana del tooltip no intercepta clics (WS_EX_TRANSPARENT)."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetAncestor(
                ctypes.windll.user32.GetParent(label.winfo_id()), 2)  # GA_ROOT=2
            gwl = -20  # GWL_EXSTYLE
            wsex = ctypes.windll.user32.GetWindowLongW(hwnd, gwl)
            # WS_EX_TRANSPARENT(0x20) | WS_EX_NOACTIVATE(0x08000000) | WS_EX_TOOLWINDOW(0x80)
            ctypes.windll.user32.SetWindowLongW(hwnd, gwl, wsex | 0x20 | 0x08000000 | 0x80)
        except Exception:
            pass

    def _hide_now(self, _=None):
        if self._after_id is not None or self._hide_after_id is not None:
            self._cancel_scheduled()
        if self.tip is not None:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


class Enhancer:
    """Cadena DSP: biquads RBJ (Low/High Shelf + Peaking) con sosfilt+zi,
    compresor RMS dinamico, limitador suave y suavizado de parametros.

    Thread-safety: el hilo de audio (callbacks) lee los _target* mientras la
    UI escribe en volume/bass/treble/eq_gains/blend. Para evitar zipper noise
    y estados corruptos, process() desliza los valores "actuales" (_c*) hacia
    los objetivos con una rampa exponencial por bloque; los filtros se
    rediseñan cada bloque con el valor suavizado. Nunca se escribe el estado
    del DSP desde el callback.
    """

    def __init__(self):
        self.sample_rate = SAMPLE_RATE
        self.eq_bands = [60, 150, 250, 500, 1000, 2000, 4000, 8000, 12000]
        self.eq_gains = [0.0] * len(self.eq_bands)
        self.bass = 0.0
        self.treble = 0.0
        self.volume = 1.0
        self.bass_freq = 150.0      # corte del Low Shelf
        self.treble_freq = 6000.0   # corte del High Shelf
        self.eq_q = 1.4             # Q de las bandas peaking
        # Limitador suave (seguridad nivel final)
        self.limiter = True
        self.limiter_threshold = 0.95
        self.limiter_strength = 0.6
        # Compresor RMS dinamico (FxSound "loudness")
        self.compressor = True
        self.comp_threshold = 0.85          # umbral (~-1.4 dBFS)
        self.comp_ratio = 4.0               # compression 4:1
        self.comp_attack = 0.005            # segundos
        self.comp_release = 0.2             # segundos
        self.comp_makeup = 1.0              # ganancia de compensacion (linear)
        self._gain_db = 0.0                 # reduccion de ganancia actual (dB)
        # A/B crossfade: blend=1 efectos, blend=0 directo
        self.blend = 1.0
        # Medidor
        self.level_rms = 0.0
        self.level_peak = 0.0
        # Valores suavizados actuales (rampa anti-cremallera)
        self._c_vol = 1.0
        self._c_bass = 0.0
        self._c_treble = 0.0
        self._c_eq = np.zeros(len(self.eq_bands), dtype=np.float32)
        self._c_blend = 1.0
        # Estados de filtros y analizador
        self._states = {}
        self._sos_cache = {}
        self._channels = None
        self.spectrum = None            # array de barras (dB) para el canvas
        self.spectrum_enabled = True
        self._win = None
        self._win_n = 0
        self._spec_idx = None
        self._spec_tick = 0

    # ---------- diseno de filtros RBJ (Audio EQ Cookbook) ----------

    @staticmethod
    def _low_shelf(freq, gain_db, fs, s=1.0):
        A = 10.0 ** (gain_db / 40.0)
        w0 = 2.0 * np.pi * freq / fs
        cw = np.cos(w0); sw = np.sin(w0)
        alpha = sw / 2.0 * np.sqrt((A + 1.0 / A) * (1.0 / s - 1.0) + 2.0)
        sa = 2.0 * np.sqrt(A) * alpha
        b0 = A * ((A + 1) - (A - 1) * cw + sa)
        b1 = 2 * A * ((A - 1) - (A + 1) * cw)
        b2 = A * ((A + 1) - (A - 1) * cw - sa)
        a0 = (A + 1) + (A - 1) * cw + sa
        a1 = -2 * ((A - 1) + (A + 1) * cw)
        a2 = (A + 1) + (A - 1) * cw - sa
        return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0

    @staticmethod
    def _high_shelf(freq, gain_db, fs, s=1.0):
        A = 10.0 ** (gain_db / 40.0)
        w0 = 2.0 * np.pi * freq / fs
        cw = np.cos(w0); sw = np.sin(w0)
        alpha = sw / 2.0 * np.sqrt((A + 1.0 / A) * (1.0 / s - 1.0) + 2.0)
        sa = 2.0 * np.sqrt(A) * alpha
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
        cw = np.cos(w0); sw = np.sin(w0)
        alpha = sw / (2.0 * q)
        b0 = 1.0 + alpha * A
        b1 = -2.0 * cw
        b2 = 1.0 - alpha * A
        a0 = 1.0 + alpha / A
        a1 = -2.0 * cw
        a2 = 1.0 - alpha / A
        return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0

    def _biquad(self, key, b0, b1, b2, a1, a2, data):
        """Aplica una seccion biquad (SOS) con estado persistente entre bloques."""
        channels = data.shape[1]
        if self._channels != channels:
            self._states = {}
            self._sos_cache = {}
            self._channels = channels
        zi = self._states.get(key)
        if zi is None or zi.shape != (1, 2, channels):
            zi = np.zeros((1, 2, channels), dtype=np.float32)
        coeffs = (b0, b1, b2, a1, a2)
        cached = self._sos_cache.get(key)
        if cached is None or cached[0] != coeffs:
            sos = np.array([[b0, b1, b2, 1.0, a1, a2]], dtype=np.float32)
            self._sos_cache[key] = (coeffs, sos)
        else:
            sos = cached[1]
        out, zi = signal.sosfilt(sos, data, axis=0, zi=zi)
        self._states[key] = zi.astype(np.float32, copy=False)
        return out

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
            # El analizador visual no necesita la tasa del audio. Una FFT cada
            # tres bloques mantiene el medidor fluido y libera CPU para la UI.
            self._spec_tick = (self._spec_tick + 1) % 3
            if self._spec_tick == 0 or self.spectrum is None:
                self._compute_spectrum(data)
        block_sec = n / self.sample_rate
        # crossfade A/B: blend se desliza hacia el objetivo en ~50 ms
        alpha_blend = 1.0 - np.exp(-block_sec / 0.02)
        self._c_blend = self._ramp(self._c_blend, self.blend, alpha_blend)
        # si esta en directo (B) estabilizado, omitir todo el DSP
        if self._c_blend < 1e-4 and self.blend <= 0.0:
            y = data.copy()
            self._measure_levels(y)
            return y[:, 0] if mono else y
        wet = self._process_dsp(data, block_sec)
        dry = data.copy()
        y = dry * (1.0 - self._c_blend) + wet * self._c_blend
        y = np.clip(y, -1.0, 1.0).astype(np.float32)
        self._measure_levels(y)
        return y[:, 0] if mono else y

    def _process_dsp(self, data, block_sec):
        y = data.copy()
        nyquist = self.sample_rate / 2
        # rampa anti-cremallera de los objetivos de la UI
        # (el volumen se suaviza aparte, por muestra, en _apply_volume)
        alpha_eq = 1.0 - np.exp(-block_sec / 0.03)
        self._c_bass = self._ramp(self._c_bass, self.bass, alpha_eq)
        self._c_treble = self._ramp(self._c_treble, self.treble, alpha_eq)
        self._c_eq = self._ramp(self._c_eq,
                                np.asarray(self.eq_gains, dtype=np.float32), alpha_eq)
        bass = float(self._c_bass)
        treb = float(self._c_treble)
        if abs(bass) >= 0.1 and self.bass_freq < nyquist:
            b0, b1, b2, a1, a2 = self._low_shelf(self.bass_freq, bass,
                                                 self.sample_rate, 1.0)
            y = self._biquad("bass", b0, b1, b2, a1, a2, y)
        if abs(treb) >= 0.1 and self.treble_freq < nyquist:
            b0, b1, b2, a1, a2 = self._high_shelf(self.treble_freq, treb,
                                                  self.sample_rate, 1.0)
            y = self._biquad("treble", b0, b1, b2, a1, a2, y)
        for i, (freq, g) in enumerate(zip(self.eq_bands, self._c_eq)):
            if abs(float(g)) >= 0.1 and freq < nyquist:
                b0, b1, b2, a1, a2 = self._peaking(freq, float(g),
                                                   self.sample_rate, self.eq_q)
                y = self._biquad("eq_%d" % i, b0, b1, b2, a1, a2, y)
        y = self._apply_volume(y, block_sec)
        if self.compressor:
            y = self._compress(y, block_sec)
        if self.limiter:
            y = self._soft_limiter(y, self.limiter_threshold, self.limiter_strength)
        return np.clip(y, -1.0, 1.0).astype(np.float32)

    def _apply_volume(self, data, block_sec):
        """Aplica el volumen con una rampa exponencial POR MUESTRA (tau ~100 ms).

        Antes se acercaba al objetivo en un solo bloque (~21 ms): al arrastrar
        el slider rapido el salto de ganancia se oia como un clic. Aqui la
        ganancia se desliza muestra a muestra, sin discontinuidades."""
        target = float(self.volume)
        start = self._c_vol
        n = data.shape[0]
        tau = 0.10  # segundos
        pole = float(np.exp(-1.0 / (self.sample_rate * tau)))
        # Misma recurrencia que lfilter, expresada directamente. Evita crear
        # un objeto scipy y un vector de entrada constante en cada callback.
        steps = np.arange(1, n + 1, dtype=np.float32)
        gain = target + (start - target) * np.power(pole, steps)
        self._c_vol = float(gain[-1])
        return data * gain[:, None]

    def _compress(self, y, block_sec):
        """Compresor RMS feed-forward con attack/release y make-up gain."""
        rms = float(np.sqrt(np.mean(y * y))) if y.size else 0.0
        db = 20.0 * np.log10(rms) if rms > 1e-9 else -90.0
        thr = 20.0 * np.log10(max(self.comp_threshold, 1e-9))
        over = db - thr
        target_db = over * (1.0 - 1.0 / self.comp_ratio) if over > 0 else 0.0
        if target_db < self._gain_db:
            coeff = 1.0 - np.exp(-block_sec / max(self.comp_attack, 1e-4))
        else:
            coeff = 1.0 - np.exp(-block_sec / max(self.comp_release, 1e-4))
        self._gain_db += (target_db - self._gain_db) * coeff
        gain = (10.0 ** (self._gain_db / 20.0)) * self.comp_makeup
        return y * gain

    @staticmethod
    def _soft_limiter(x, threshold, strength):
        """Curva de ganancia suave: deja pasar <=threshold, comprime despues."""
        a = np.abs(x)
        over = a - threshold
        if not np.any(over > 0):
            return x
        k = 1.0 / (1.0 + np.exp(-5.0 * over))   # transicion suave 0..1
        g = 1.0 - strength * (over / (threshold + 1e-9)) * k
        g = np.clip(g, 0.0, 1.0)
        return x * np.where(over > 0, g, 1.0)

    # ---------- analizador de espectro ----------

    def _compute_spectrum(self, data):
        n = data.shape[0]
        if n < 64:
            return
        if self._win_n != n:
            self._win = np.hanning(n).astype(np.float32)
            self._win_n = n
            self._spec_idx = None
        mono = data[:, 0] * self._win
        spec = np.abs(np.fft.rfft(mono)) / n
        freqs = np.fft.rfftfreq(n, 1.0 / self.sample_rate)
        if self._spec_idx is None:
            hi = min(self.sample_rate / 2.0, 20000.0)
            edges = np.geomspace(40.0, max(hi, 200.0), 65)
            self._spec_idx = np.searchsorted(freqs, edges, side="right")
        out = np.full(64, -80.0, dtype=np.float32)
        idx = self._spec_idx
        for i in range(64):
            lo, hi = idx[i], idx[i + 1]
            if lo < hi:
                m = float(np.mean(spec[lo:hi]))
                if m > 1e-9:
                    out[i] = 20.0 * np.log10(m)
        self.spectrum = out

    def _measure_levels(self, y):
        """Actualiza el medidor de nivel (RMS y pico suavizados) del bloque."""
        peak = float(np.max(np.abs(y))) if y.size else 0.0
        rms = float(np.sqrt(np.mean(y ** 2))) if y.size else 0.0
        self.level_peak = self.level_peak * 0.7 + peak * 0.3
        self.level_rms = self.level_rms * 0.85 + rms * 0.15


class ScrollBody(ctk.CTkFrame):
    """Contenedor con scroll vertical fluido (tk.Canvas + frame interno).

    En lugar de CTkScrollableFrame (lento y con artefactos en Windows) usa un
    canvas plano: los widgets se colocan de una vez en ``inner`` y el scroll
    solo mueve la vista. El scrollbar se oculta cuando el contenido cabe.
    """

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(
            self, highlightthickness=0, bd=0,
            bg=ctk.ThemeManager.theme["CTk"]["fg_color"][1],
            yscrollincrement=24)
        self._vsb = ctk.CTkScrollbar(self, orientation="vertical",
                                     command=self.canvas.yview, corner_radius=8)
        self.canvas.configure(yscrollcommand=self._on_vsb_needed)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._vsb.pack(side="right", fill="y", padx=(2, 1))
        self._vsb_visible = True
        self.inner = ctk.CTkFrame(self.canvas, fg_color="transparent")
        self._win_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_cfg)
        self.canvas.bind("<Configure>", self._on_canvas_cfg)
        self.canvas.bind("<Enter>", lambda _: self._bind_wheel(True))
        self.canvas.bind("<Leave>", lambda _: self._bind_wheel(False))
        self.canvas.bind("<Button-4>", self._wheel_up)   # soporte Linux
        self.canvas.bind("<Button-5>", self._wheel_down)
        self.canvas.bind("<MouseWheel>", self._on_wheel)

    def _on_vsb_needed(self, first, last):
        self._vsb.set(first, last)
        # No repaquetar el scrollbar en cada evento de scroll: en Windows
        # provocaba relayout y tirones visibles durante la rueda.
        try:
            needed = not (float(first) <= 0.0 and float(last) >= 1.0)
            if needed == self._vsb_visible:
                return
            self._vsb_visible = needed
            if needed:
                self._vsb.pack(side="right", fill="y", padx=(2, 1))
            else:
                self._vsb.pack_forget()
        except Exception:
            pass

    def _on_inner_cfg(self, _):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_cfg(self, e):
        self.canvas.itemconfigure(self._win_id, width=e.width)

    def _bind_wheel(self, on):
        if on:
            self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        else:
            self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def _wheel_up(self, _):
        self.canvas.yview_scroll(-1, "units")

    def _wheel_down(self, _):
        self.canvas.yview_scroll(1, "units")


class App:
    def __init__(self, root):
        self.root = root
        self.language = detect_system_language()
        self.root.title(translate("Audio Enhancer", self.language) + " - FxStyle")
        self.root.geometry("860x740")
        self.root.minsize(760, 560)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.enhancer = Enhancer()
        self.custom_presets = {}
        self.running = False
        self.pa = None
        self.stream = None
        self.out_stream = None
        self._ring = None            # ring buffer np (N,2) separando captura/salida
        self._rhead = 0              # posicion de escritura (frames)
        self._whead = 0              # posicion de lectura (frames)
        self._lock = threading.Lock()
        self._closing = False
        self.tray_icon = None
        self.loopbacks = []          # lista de dicts: indices loopback WASAPI
        self.speakers = []           # lista de dicts: salidas fisicas WASAPI
        self.go = False
        self._keep_src = ""          # selecciones a conservar tras refresh
        self._keep_out = ""
        # Atributos creados por _scale/_build_ui; se declaran aquí para que
        # existan antes de aplicar la configuración persistida y sean visibles
        # para analizadores estáticos como Pylint.
        self.volumen_slider = None
        self.bass_slider = None
        self.treble_slider = None
        self.vol_label = None
        self.bass_label = None
        self.treble_label = None
        self._spec_ids = None

        self._build_ui()
        self._apply_config()
        self._refresh_preset_list(keep=None)
        self._update_meter()
        self._set_window_icon()
        # el descubrimiento de dispositivos (PortAudio + enumerar WASAPI) va en
        # un hilo para que la ventana aparezca de inmediato. Un sondeo desde el
        # hilo principal (root.after) recoge el resultado: llamar a Tk desde un
        # hilo secundario falla en Windows.
        self._waiting_devices = True
        threading.Thread(target=self._discover_in_thread, daemon=True).start()
        self.root.after(50, self._poll_devices)

    # ---------- deteccion de dispositivos ----------

    def _discover_in_thread(self):
        try:
            self._discover_devices()
        except Exception:
            pass
        self._waiting_devices = False

    def _poll_devices(self):
        if self._closing:
            return
        if self._waiting_devices:
            self.root.after(50, self._poll_devices)
            return
        self._on_devices_ready()

    def _on_devices_ready(self):
        loop_names = [d["name"] for d in self.loopbacks]
        speaker_names = [d["name"] for d in self.speakers]
        self.source_box.configure(values=loop_names)
        self.output_box.configure(values=speaker_names)
        self._apply_config()   # reintenta seleccionar los dispositivos guardados
        if self._keep_src in loop_names:
            self.source_var.set(self._keep_src)
        if self._keep_out in speaker_names:
            self.output_var.set(self._keep_out)
        if not self.source_var.get() or not self.output_var.get():
            self._auto_select()
        self._route_guard()
        if self._keep_src or self._keep_out:
            self.status.configure(text="Dispositivos actualizados.", text_color=OK)
        self._keep_src = ""
        self._keep_out = ""
        if not self._has_cable():
            self.root.after(400, self._show_cable_guide)

    def _discover_devices(self):
        pa = _pa()
        if self.pa is None:
            try:
                self.pa = pa.PyAudio()
            except Exception as exc:
                self.pa = None
                print("PyAudio error:", exc)
                return
        wasapi_idx = None
        try:
            wasapi_info = self.pa.get_host_api_info_by_type(pa.paWASAPI)
            wasapi_idx = wasapi_info["index"]
        except Exception:
            wasapi_idx = None
        for d in self.pa.get_loopback_device_info_generator():
            self.loopbacks.append(d)
        for i in range(self.pa.get_device_count()):
            d = self.pa.get_device_info_by_index(i)
            if d.get("isLoopbackDevice"):
                continue
            if wasapi_idx is not None and d["hostApi"] != wasapi_idx:
                continue
            if d["maxOutputChannels"] > 0 and d["maxInputChannels"] == 0:
                n = d["name"].lower()
                if self._is_fxsound(n) or any(k in n for k in self._virtual_keys()):
                    continue  # excluir virtuales/cables de la salida
                self.speakers.append(d)

    def _has_cable(self):
        return any(any(k in m["name"].lower() for k in ("cable", "vb-audio", "voicemeeter"))
                   for m in self.loopbacks)

    def _refresh_devices(self):
        """Hot-plug: re-descubre dispositivos (en hilo) sin congelar la UI."""
        if self.running:
            self._stop()
        self._keep_src = self.source_var.get() if hasattr(self, "source_var") else ""
        self._keep_out = self.output_var.get() if hasattr(self, "output_var") else ""
        self._keep_src = self._keep_src or ""
        self._keep_out = self._keep_out or ""
        try:
            if self.pa is not None:
                self.pa.terminate()
        except Exception:
            pass
        self.loopbacks = []
        self.speakers = []
        self.pa = None
        self.status.configure(text="Detectando dispositivos...", text_color=WARN)
        self._waiting_devices = True
        threading.Thread(target=self._discover_in_thread, daemon=True).start()
        self.root.after(50, self._poll_devices)

    def _virtual_keys(self):
        return ("cable", "vb-audio", "voicemeeter", "virtual")

    def _is_fxsound(self, name):
        return "fxsound" in name.lower()

    @staticmethod
    def _norm(name):
        return "".join(ch for ch in name.lower() if ch.isalnum())

    def _auto_select(self):
        idx = 0
        if self.loopbacks:
            for i, d in enumerate(self.loopbacks):
                n = d["name"].lower()
                if "cable" in n or "vb-audio" in n or "voicemeeter" in n:
                    idx = i
                    break
            else:
                for i, d in enumerate(self.loopbacks):
                    if any(k in d["name"].lower() for k in self._virtual_keys()) \
                            and not self._is_fxsound(d["name"]):
                        idx = i
                        break
            self.source_var.set(self.loopbacks[idx]["name"])
        if self.speakers:
            # preferir salida fisica real (Synaptics/Realtek...) excluyendo virtuales
            pref = 0
            for i, d in enumerate(self.speakers):
                n = d["name"].lower()
                if self._is_fxsound(n) or any(k in n for k in self._virtual_keys()):
                    continue
                if any(k in n for k in ("synaptics", "realtek", "speaker", "altavoces", "hd audio")):
                    pref = i
                    break
            self.output_var.set(self.speakers[pref]["name"])

    def _t(self, text):
        return translate(text, self.language)

    def _help(self, key):
        return EXPLAIN_EN.get(key, EXPLAIN[key]) if self.language == "en" else EXPLAIN[key]

    # ---------- interfaz ----------

    def _build_ui(self):
        root = self.root
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(root, corner_radius=0, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(12, 2))
        ctk.CTkLabel(header, text=self._t("Audio Enhancer"), font=("Segoe UI", 26, "bold"),
                     text_color=ACCENT).pack(anchor="w")
        ctk.CTkLabel(header, text=self._t("Procesamiento del audio del sistema vía WASAPI loopback"),
                     font=("Segoe UI", 12), text_color=("gray30", "gray70")).pack(anchor="w")

        body = ScrollBody(root, corner_radius=0, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=20, pady=8)
        body.inner.grid_columnconfigure(0, weight=1)

        # Dispositivos y ruteo
        card = ctk.CTkFrame(body.inner, corner_radius=14)
        card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(card, text=self._t("Dispositivos y ruteo"), font=("Segoe UI", 16, "bold"),
                     anchor="w").grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 6))

        ctk.CTkLabel(card, text=self._t("Captura (loopback):"), anchor="w").grid(row=1, column=0, sticky="w", padx=16, pady=4)
        self.source_var = ctk.StringVar()
        self.source_box = ctk.CTkComboBox(card, variable=self.source_var, state="readonly",
                                          values=[d["name"] for d in self.loopbacks],
                                          command=self._route_guard)
        self.source_box.grid(row=1, column=1, sticky="ew", padx=16, pady=4)

        ctk.CTkLabel(card, text=self._t("Salida (física):"), anchor="w").grid(row=2, column=0, sticky="w", padx=16, pady=4)
        self.output_var = ctk.StringVar()
        self.output_box = ctk.CTkComboBox(card, variable=self.output_var, state="readonly",
                                          values=[d["name"] for d in self.speakers],
                                          command=self._route_guard)
        self.output_box.grid(row=2, column=1, sticky="ew", padx=16, pady=4)

        self.route_label = ctk.CTkLabel(card, text="", font=("Segoe UI", 12, "bold"), wraplength=560,
                                        justify="left", anchor="w")
        self.route_label.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(8, 4))
        self.refresh_btn = ctk.CTkButton(card, text=self._t("Actualizar dispositivos"), height=32,
                                         corner_radius=8, fg_color=("gray70", "gray25"),
                                         font=("Segoe UI", 12), command=self._refresh_devices)
        self.refresh_btn.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 12))
        card.grid_columnconfigure(1, weight=1)

        # Efectos
        fx = ctk.CTkFrame(body.inner, corner_radius=14)
        fx.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(fx, text=self._t("Efectos"), font=("Segoe UI", 16, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=4, sticky="ew", padx=16, pady=(12, 4))

        preset_row = ctk.CTkFrame(fx, fg_color="transparent")
        preset_row.grid(row=1, column=0, columnspan=4, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(preset_row, text=self._t("Configuración:"), font=("Segoe UI", 12, "bold")).pack(side="left")
        self.preset_var = ctk.StringVar(value="Plano (sin efectos)")
        self.preset_box = ctk.CTkComboBox(preset_row, variable=self.preset_var, state="readonly",
                                          values=list(PRESETS.keys()), width=240,
                                          command=self.apply_preset)
        self.preset_box.pack(side="left", padx=8)
        # Boton A/B: compara el audio procesado con el directo (sin efectos)
        self.ab_button = ctk.CTkButton(preset_row, text=self._t("A: Efectos ON"),
                                       width=120, height=30, corner_radius=8,
                                       font=("Segoe UI", 12, "bold"), fg_color=ACCENT,
                                       command=self.toggle_ab)
        self.ab_button.pack(side="right")

        # Presets personalizados: guardar el estado actual / borrar el seleccionado
        custom_row = ctk.CTkFrame(fx, fg_color="transparent")
        custom_row.grid(row=2, column=0, columnspan=4, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(custom_row, text=self._t("Nuevo preset:"), font=("Segoe UI", 12)).pack(side="left")
        self.preset_entry = ctk.CTkEntry(custom_row, placeholder_text=self._t("nombre del preset"), width=220,
                                         font=("Segoe UI", 12))
        self.preset_entry.pack(side="left", padx=8)
        ctk.CTkButton(custom_row, text=self._t("Guardar"), width=90, height=30, corner_radius=8,
                      font=("Segoe UI", 12, "bold"), command=self._save_custom_preset).pack(
                      side="left", padx=(0, 6))
        ctk.CTkButton(custom_row, text=self._t("Borrar"), width=90, height=30, corner_radius=8,
                      fg_color=DANGER, font=("Segoe UI", 12, "bold"),
                      command=self._delete_custom_preset).pack(side="left")

        self._scale(fx, self._t("Volumen"), 0.0, 2.0, 1.0, 200, 3, self.set_volume, "mult", "vol_label",
                    help_text=self._help("volumen"))
        self._scale(fx, self._t("Bass Boost (dB)"), 0, 12, 0, 200, 4, self.set_bass, "db", "bass_label",
                    help_text=self._help("bass"))
        self._scale(fx, self._t("Treble Boost (dB)"), 0, 12, 0, 200, 5, self.set_treble, "db", "treble_label",
                    help_text=self._help("treble"))
        for idx in range(1, 5):
            fx.grid_columnconfigure(idx, weight=1)

        # Limitador, compresor + medidor de nivel
        meter_row = ctk.CTkFrame(fx, fg_color="transparent")
        meter_row.grid(row=6, column=0, columnspan=4, sticky="ew", padx=16, pady=(4, 0))
        self.limiter_switch = ctk.CTkSwitch(meter_row, text=self._t("Limitador suave"), font=("Segoe UI", 12),
                                            command=self.toggle_limiter)
        self.limiter_switch.select()
        self.limiter_switch.pack(side="left")
        ToolTip(self.limiter_switch, self._help("limiter"))
        self.comp_switch = ctk.CTkSwitch(meter_row, text=self._t("Compresor RMS"), font=("Segoe UI", 12),
                                         command=self.toggle_compressor)
        self.comp_switch.select()
        self.comp_switch.pack(side="left", padx=(12, 0))
        ToolTip(self.comp_switch, self._help("compressor"))
        ctk.CTkLabel(meter_row, text=self._t("Nivel:"), font=("Segoe UI", 11, "bold"),
                     text_color=("gray30", "gray60")).pack(side="left", padx=(12, 4))
        self.meter_bar = ctk.CTkProgressBar(meter_row, width=200, height=12,
                                            fg_color=("gray78", "gray22"))
        self.meter_bar.set(0)
        self.meter_bar.pack(side="left")
        self.meter_label = ctk.CTkLabel(meter_row, text="0 dB", width=70,
                                        font=("Segoe UI Mono", 11), text_color=("gray30", "gray70"))
        self.meter_label.pack(side="left", padx=6)

        # Ecualizador
        eq = ctk.CTkFrame(body.inner, corner_radius=14)
        eq.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(eq, text=self._t("Ecualizador (9 bandas)"), font=("Segoe UI", 16, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=9, sticky="ew", padx=16, pady=(10, 4))
        self.eq_scales = []
        for i, freq in enumerate(self.enhancer.eq_bands):
            frame = ctk.CTkFrame(eq, fg_color="transparent")
            frame.grid(row=1, column=i, padx=4, sticky="ns")
            label = "%d Hz" % freq if freq < 1000 else "%d kHz" % (freq // 1000)
            ctk.CTkLabel(frame, text=label, font=("Segoe UI", 10, "bold"),
                         text_color=("gray30", "gray65")).pack()
            slider = ctk.CTkSlider(frame, from_=-12, to=12, number_of_steps=48, height=104,
                                   orientation="vertical", width=22,
                                   command=lambda v, idx=i: self.set_eq(idx, v))
            slider.set(0)
            slider.pack(pady=2)
            band_help = EQ_EXPLAIN.get(freq, EXPLAIN["eq"])
            ToolTip(slider, "%s (+/- dB): %s\n\n%s" % (label, band_help, self._help("eq")))
            self.eq_scales.append(slider)

        # Analizador de espectro
        spec = ctk.CTkFrame(body.inner, corner_radius=14)
        spec.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(spec, text=self._t("Analizador de espectro"), font=("Segoe UI", 16, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 4))
        self.spec_canvas = tk.Canvas(spec, height=80, bg="#16181d", highlightthickness=0)
        self.spec_canvas.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))
        spec.grid_columnconfigure(0, weight=1)

        # Acciones
        actions = ctk.CTkFrame(body.inner, corner_radius=14, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew")
        self.start_button = ctk.CTkButton(actions, text="▶  " + self._t("Iniciar audio del sistema"),
                                          command=self.toggle, corner_radius=10, height=40,
                                          font=("Segoe UI", 15, "bold"))
        self.start_button.pack(fill="x", padx=4, pady=3)
        btn_row = ctk.CTkFrame(actions, fg_color="transparent")
        btn_row.pack(fill="x", padx=4)
        ctk.CTkButton(btn_row, text=self._t("Instalar loopback propio (VB-CABLE)"),
                      command=self._show_cable_guide, corner_radius=10, height=34,
                      fg_color=("gray70", "gray25"), font=("Segoe UI", 12)).pack(
                      side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(btn_row, text=self._t("Restablecer"), command=self.reset, corner_radius=10,
                      height=34, fg_color=("gray75", "gray30")).pack(
                      side="left", fill="x", expand=True, padx=(4, 0))
        self.autostart_var = ctk.BooleanVar(value=self._auto_start_enabled())
        ctk.CTkCheckBox(btn_row, text=self._t("Iniciar con Windows"), variable=self.autostart_var,
                        command=self._on_autostart_toggle, font=("Segoe UI", 12)).pack(
                        side="left", fill="x", expand=True, padx=(8, 0))

        self.status = ctk.CTkLabel(root, text=self._t("Listo. Configura el ruteo y pulsa Iniciar."),
                                   font=("Segoe UI", 12, "bold"), text_color=OK)
        self.status.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 12))

    def _scale(self, parent, text, lo, hi, value, steps, row, callback, fmt, label_attr, help_text=None):
        ctk.CTkLabel(parent, text=text, font=("Segoe UI", 12), anchor="w").grid(
            row=row, column=0, sticky="w", padx=16, pady=6)
        _label = ctk.CTkLabel(parent, text="", width=64, font=("Segoe UI Mono", 12, "bold"))
        _label.grid(row=row, column=3, sticky="e", padx=(12, 16), pady=6)
        if fmt == "db":
            _label.configure(text="+0.0 dB")
        else:
            _label.configure(text="1.0x")

        def _fmt(v):
            if fmt == "db":
                return "+%.1f dB" % float(v)
            return "%.2fx" % float(v)

        slider = ctk.CTkSlider(parent, from_=lo, to=hi, number_of_steps=steps,
                               command=lambda v, f=callback, l=_label, fm=_fmt: (f(v), l.configure(text=fm(v))))
        slider.set(value)
        slider.grid(row=row, column=1, columnspan=2, sticky="ew", padx=8, pady=6)
        setattr(self, label_attr, _label)
        slider_attr = {
            "vol_label": "volumen_slider",
            "bass_label": "bass_slider",
            "treble_label": "treble_slider",
        }.get(label_attr, "%s_slider" % text.split()[0].lower())
        setattr(self, slider_attr, slider)
        if help_text:
            ToolTip(slider, help_text)

    # ---------- logica ----------

    def set_volume(self, v): self.enhancer.volume = float(v)
    def set_bass(self, v): self.enhancer.bass = float(v)
    def set_treble(self, v): self.enhancer.treble = float(v)
    def set_eq(self, i, v): self.enhancer.eq_gains[i] = float(v)

    # ---------- A/B, limitador, medidor, autoinicio ----------

    def toggle_ab(self):
        """Alterna entre audio procesado (A) y directo sin efectos (B) con crossfade."""
        target = 0.0 if self.enhancer.blend > 0.5 else 1.0
        self.enhancer.blend = target
        if target == 0.0:
            self.ab_button.configure(text="B: Directo (OFF)", fg_color=("gray60", "gray40"))
            self.status.configure(text="A/B: audio directo sin efectos (B)", text_color=WARN)
        else:
            self.ab_button.configure(text="A: Efectos ON", fg_color=ACCENT)
            self.status.configure(text="A/B: audio procesado con efectos (A)", text_color=OK)

    def toggle_limiter(self):
        self.enhancer.limiter = bool(self.limiter_switch.get())
        self.status.configure(text="Limitador suave: %s" % ("ON" if self.enhancer.limiter else "OFF"),
                              text_color=OK)

    def toggle_compressor(self):
        self.enhancer.compressor = bool(self.comp_switch.get())
        self.status.configure(text="Compresor RMS: %s" % ("ON" if self.enhancer.compressor else "OFF"),
                              text_color=OK)

    def _on_autostart_toggle(self):
        ok = self._set_auto_start(bool(self.autostart_var.get()))
        if not ok:
            self.autostart_var.set(not self.autostart_var.get())
        self.status.configure(
            text="Inicio con Windows: %s" % ("activado" if ok else "fallo al configurar"),
            text_color=OK if ok else DANGER)

    def _update_meter(self):
        """Refresca el medidor de nivel desde el ultimo bloque procesado."""
        if self._closing:
            return
        try:
            peak = self.enhancer.level_peak
            rms = self.enhancer.level_rms
            # escala visual: 0..1 -> dBFS (0 dB = 1.0)
            bar = float(min(peak, 1.0))
            self.meter_bar.set(bar)
            db = 20 * np.log10(peak) if peak > 1e-6 else -60.0
            self.meter_label.configure(text="%.1f dB" % db)
            color = OK
            if bar > 0.85:
                color = DANGER
            elif bar > 0.6:
                color = WARN
            self.meter_bar.configure(progress_color=color)
            if self.running:
                self._draw_spectrum()
        except Exception:
            pass
        # mas lenta cuando no hay audio, para no cargar la UI en reposo
        delay = 100 if self.running else 400
        self.root.after(delay, self._update_meter)

    def _draw_spectrum(self):
        """Dibuja las barras del analizador FFT reutilizando los rectangulos."""
        try:
            cv = self.spec_canvas
            w = cv.winfo_width()
            h = cv.winfo_height()
            if w < 20 or h < 10:
                return
            spec = self.enhancer.spectrum
            if spec is None:
                return
            n = len(spec)
            if getattr(self, "_spec_ids", None) is None or len(self._spec_ids) != n:
                cv.delete("all")
                self._spec_ids = [cv.create_rectangle(0, 0, 0, 0, fill=ACCENT, outline="")
                                  for _ in range(n)]
            bw = w / n
            for i, db in enumerate(spec):
                # escala: -60 dBFS..0 -> altura
                v = (float(db) + 60.0) / 60.0
                v = max(0.0, min(1.0, v))
                bh = int(v * (h - 4))
                color = ACCENT
                if float(db) > -12:
                    color = DANGER
                elif float(db) > -25:
                    color = WARN
                x0 = i * bw + 1
                rid = self._spec_ids[i]
                cv.coords(rid, x0, h - bh - 2, x0 + bw - 2, h - 2)
                cv.itemconfigure(rid, fill=color)
        except Exception:
            pass

    def _sync_ui_from_state(self):
        """Sincroniza sliders y etiquetas con el estado del enhancer."""
        self.volumen_slider.set(self.enhancer.volume)
        self.bass_slider.set(self.enhancer.bass)
        self.treble_slider.set(self.enhancer.treble)
        self.vol_label.configure(text="%.2fx" % self.enhancer.volume)
        self.bass_label.configure(text="+%.1f dB" % self.enhancer.bass)
        self.treble_label.configure(text="+%.1f dB" % self.enhancer.treble)
        for s, g in zip(self.eq_scales, self.enhancer.eq_gains):
            s.set(g)

    def apply_preset(self, name):
        name = self.preset_var.get() if name is None else name
        presets = self._all_presets()
        if name not in presets:
            return
        volume, bass, treble, gains = presets[name]
        self.enhancer.volume = float(volume)
        self.enhancer.bass = float(bass)
        self.enhancer.treble = float(treble)
        self.enhancer.eq_gains = [float(g) for g in gains]
        self._sync_ui_from_state()
        self.status.configure(text="Configuración aplicada: %s" % name, text_color=OK)

    def _all_presets(self):
        merged = dict(PRESETS)
        merged.update(self.custom_presets)
        return merged

    def _refresh_preset_list(self, keep=None):
        names = list(self._all_presets().keys())
        self.preset_box.configure(values=names)
        if keep is not None:
            if keep in names:
                self.preset_var.set(keep)
            elif names:
                self.preset_var.set(names[0])

    def _save_custom_preset(self):
        name = self.preset_entry.get().strip()
        if not name:
            self.status.configure(text="Escribe un nombre para el preset personalizado.",
                                  text_color=WARN)
            return
        self.custom_presets[name] = (
            float(self.enhancer.volume), float(self.enhancer.bass),
            float(self.enhancer.treble),
            [float(g) for g in self.enhancer.eq_gains])
        self.preset_entry.delete(0, "end")
        self._refresh_preset_list(keep=name)
        self._save_config()
        self.status.configure(text="Preset personalizado guardado: %s" % name, text_color=OK)

    def _delete_custom_preset(self):
        name = self.preset_var.get()
        if name not in self.custom_presets:
            self.status.configure(text="Selecciona un preset personalizado para borrarlo.",
                                  text_color=WARN)
            return
        del self.custom_presets[name]
        self._refresh_preset_list(keep="Plano (sin efectos)")
        self._save_config()
        self.status.configure(text="Preset personalizado borrado: %s" % name, text_color=OK)

    def reset(self):
        self.apply_preset("Plano (sin efectos)")
        self.preset_var.set("Plano (sin efectos)")
        self.status.configure(text="Controles restablecidos a plano", text_color=OK)

    def _on_close(self):
        """Al cerrar la ventana: si hay bandeja, minimiza a ella; si no, sale."""
        if _HAVE_TRAY:
            self._start_tray()
            self._save_config()
            self.root.withdraw()
            self.status.configure(text="Procesando en segundo plano (icono en bandeja).", text_color=WARN)
            return
        self._shutdown()

    def _shutdown(self):
        """Detiene audio, guarda config, cierra pystray y destruye la ventana."""
        self._closing = True
        self._save_config()
        if self.running:
            try:
                self._stop()
            except Exception:
                pass
        try:
            if self.pa is not None:
                self.pa.terminate()
        except Exception:
            pass
        try:
            if self.tray_icon is not None:
                self.tray_icon.stop()
        except Exception:
            pass
        self.root.destroy()

    # ---------- bandeja del sistema ----------

    def _set_window_icon(self):
        try:
            ico = _resource_path("app.ico")
            if os.path.exists(ico):
                self.root.iconbitmap(default=ico)
        except Exception:
            pass

    def _tray_image(self):
        try:
            path = _resource_path("tray.png")
            return Image.open(path)
        except Exception:
            return None

    def _start_tray(self):
        if not _HAVE_TRAY or self.tray_icon is not None:
            return
        img = self._tray_image()
        if img is None:
            return
        menu = pystray.Menu(
            pystray.MenuItem(self._t("Mostrar / Ocultar"), self._tray_toggle_show, default=True),
            pystray.MenuItem(self._t("Iniciar / Detener"), self._tray_toggle_audio),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._t("Salir"), self._tray_quit),
        )
        self.tray_icon = pystray.Icon("AudioEnhancerFxStyle", img, "Audio Enhancer - FxStyle", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _tray_toggle_show(self, icon=None, item=None):
        if self.root.state() == "withdrawn":
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        else:
            self.root.withdraw()

    def _tray_toggle_audio(self, icon=None, item=None):
        self.root.after(0, self.toggle)

    def _tray_quit(self, icon=None, item=None):
        self.root.after(0, self._shutdown)

    # ---------- persistencia ----------

    def _auto_start_enabled(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            try:
                val, _ = winreg.QueryValueEx(key, "AudioEnhancerFxStyle")
                return bool(val)
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False

    def _set_auto_start(self, enable):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            if enable:
                exe = sys.executable
                target = '"%s" "%s"' % (exe, os.path.abspath(sys.argv[0]))
                winreg.SetValueEx(key, "AudioEnhancerFxStyle", 0, winreg.REG_SZ, target)
            else:
                try:
                    winreg.DeleteValue(key, "AudioEnhancerFxStyle")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    def _load_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return {}
        return cfg if isinstance(cfg, dict) else {}

    def _apply_config(self):
        cfg = self._load_config()
        if not cfg:
            return
        # dispositivos guardados por nombre (estable frente a cambios de indice)
        cleaned_loopbacks = [d["name"] for d in self.loopbacks]
        cleaned_speakers = [d["name"] for d in self.speakers]
        src = cfg.get("source")
        out = cfg.get("output")
        if src in cleaned_loopbacks:
            self.source_var.set(src)
        if out in cleaned_speakers:
            self.output_var.set(out)
        # presets personalizados guardados
        cp = cfg.get("custom_presets")
        if isinstance(cp, dict):
            self.custom_presets = {}
            for k, v in cp.items():
                if isinstance(v, (list, tuple)) and len(v) == 4:
                    self.custom_presets[str(k)] = tuple(v)
        self._refresh_preset_list(keep=cfg.get("preset", "Plano (sin efectos)"))
        self.enhancer.volume = float(cfg.get("volume", 1.0))
        self.enhancer.bass = float(cfg.get("bass", 0.0))
        self.enhancer.treble = float(cfg.get("treble", 0.0))
        gains = cfg.get("eq_gains")
        if isinstance(gains, list) and len(gains) == len(self.enhancer.eq_gains):
            self.enhancer.eq_gains = [float(g) for g in gains]
        self.enhancer.limiter = bool(cfg.get("limiter", True))
        self.enhancer.compressor = bool(cfg.get("compressor", True))
        self._sync_ui_from_state()
        self.limiter_switch.set(bool(cfg.get("limiter", True)))
        self.comp_switch.set(bool(cfg.get("compressor", True)))
        self._route_guard()

    def _save_config(self):
        cfg = {
            "source": self.source_var.get(),
            "output": self.output_var.get(),
            "preset": self.preset_var.get(),
            "volume": float(self.enhancer.volume),
            "bass": float(self.enhancer.bass),
            "treble": float(self.enhancer.treble),
            "eq_gains": [float(g) for g in self.enhancer.eq_gains],
            "limiter": bool(self.enhancer.limiter),
            "compressor": bool(self.enhancer.compressor),
            "custom_presets": {k: list(v) for k, v in self.custom_presets.items()},
        }
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            print("No se pudo guardar config:", exc)

    def _selected(self):
        src = out = None
        for m in self.loopbacks:
            if m["name"] == self.source_var.get():
                src = m
        for s in self.speakers:
            if s["name"] == self.output_var.get():
                out = s
        return src, out

    def _route_guard(self, *_):
        src_name = self.source_var.get() or ""
        out_name = self.output_var.get() or ""
        label = self.route_label

        if not src_name or not out_name:
            self.go = False
            label.configure(text="Selecciona una fuente de captura y una salida física.", text_color=WARN)
            return

        same = self._norm(src_name) == self._norm(out_name)
        src_is_virtual = any(k in src_name.lower() for k in self._virtual_keys())
        src_is_fxsound = self._is_fxsound(src_name)
        out_is_virtual = any(k in out_name.lower() for k in self._virtual_keys())

        if same:
            self.go = False
            label.configure(
                text="⚠  ECO: capturas y reproduces el mismo dispositivo (A → A). "
                     "Selecciona como captura el cable virtual donde suenan las apps "
                     "(p. ej. CABLE Input de VB-Audio) y como salida la física.",
                text_color=DANGER)
            return

        if out_is_virtual:
            self.go = False
            label.configure(
                text="⚠  La salida es virtual (cable). Reproduce en la salida FÍSICA "
                     "(parlantes reales) para no realimentar el cable.",
                text_color=DANGER)
            return

        if src_is_fxsound:
            label.configure(
                text="⚠  Estás capturando el loopback de FxSound (otra app). Si no percibes "
                     "efecto o hay conflicto, instala VB-CABLE y captura 'CABLE Input'.",
                text_color=WARN)
        elif src_is_virtual:
            label.configure(
                text="✔  Ruteo correcto: capturas tu cable virtual y solo la salida física "
                     "reproduce el audio procesado. Cierra FxSound para no duplicar el efecto.",
                text_color=OK)
        else:
            label.configure(
                text="Info: capturas un parlante físico. Asegúrate de que sea el dispositivo "
                     "donde suenan las apps y que la salida sea otro distinto.",
                text_color=("gray30", "gray60"))
        self.go = True

    def toggle(self):
        if self.running:
            self._stop()
            return
        src, out = self._selected()
        if not self.go or src is None or out is None:
            self._route_guard()
            self.status.configure(text="Revisa el ruteo: no captures y reproduzcas el mismo dispositivo.",
                                  text_color=DANGER)
            return
        try:
            self._start(src, out)
        except Exception as exc:
            self.status.configure(text="No se pudo iniciar el loopback: %s" % exc, text_color=DANGER)
            self.running = False

    def _start(self, src, out):
        pa = _pa()
        if self.pa is None:
            self.pa = pa.PyAudio()
        in_idx = src["index"]
        out_idx = out["index"]

        # Frecuencia de muestreo dinamica: usa la del dispositivo de salida
        # (evita el re-muestreo del mezclador y su latencia extra).
        rate = int(out.get("defaultSampleRate", SAMPLE_RATE))
        if rate < 8000 or rate > 384000:
            rate = SAMPLE_RATE
        if rate != self.enhancer.sample_rate:
            self.enhancer.sample_rate = rate
            self.enhancer._spec_idx = None

        # Ring buffer de RING_SECONDS segundos que desacopla la captura (loopback
        # WASAPI, paquetes irregulares) de la salida fisica. Evita los
        # OutputUnderflow del modo full-duplex (miniaudio #204).
        nframes = int(rate * RING_SECONDS)
        self._ring = np.zeros((nframes, 2), dtype=np.float32)
        self._rhead = 0
        self._whead = 0
        self._fadein_frames = 0
        self._in_gap = False
        FADE = max(1, int(rate * 0.005))    # fundido ~5 ms
        # La deriva se corrige alrededor del punto medio del ring. El ajuste
        # queda limitado a pocos frames por bloque para que no sea audible.
        drift_target = nframes // 2
        drift_deadband = max(CHUNK // 8, int(rate * 0.0025))
        drift_gain = 0.0001
        drift_accum = 0.0
        max_drift_frames = 4

        def _put(data):
            n = len(data)
            with self._lock:
                avail = self._rhead - self._whead
                if avail + n > nframes:
                    # descartar lo mas viejo si la salida va mas lenta
                    drop = avail + n - nframes
                    self._whead += drop
                    idx = self._whead % nframes
                    if idx + drop <= nframes:
                        self._ring[idx:idx + drop] = 0.0
                    else:
                        a = nframes - idx
                        self._ring[idx:] = 0.0
                        self._ring[:drop - a] = 0.0
                idx = self._rhead % nframes
                if idx + n <= nframes:
                    self._ring[idx:idx + n] = data
                else:
                    a = nframes - idx
                    self._ring[idx:] = data[:a]
                    self._ring[:n - a] = data[a:]
                self._rhead += n

        def _get(n):
            """Devuelve n frames para la salida.

            Si el ring no tiene suficiente audio (la salida corrio mas rapido
            que la captura) NO inserta un bloque seco de silencio: suaviza el
            hueco con fundidos de entrada/salida para que no haya chasquidos.
            """
            with self._lock:
                avail = self._rhead - self._whead
                if avail >= n:
                    idx = self._whead % nframes
                    if idx + n <= nframes:
                        data = self._ring[idx:idx + n].copy()
                    else:
                        a = nframes - idx
                        data = np.concatenate([self._ring[idx:], self._ring[:n - a]])
                    self._whead += n
                    # Solo se aplica fade-in al salir de un hueco. Mantener
                    # el estado explícito evita perderlo en huecos consecutivos.
                    if self._in_gap:
                        f = min(self._fadein_frames or FADE, n)
                        if f > 0:
                            fade_in = np.linspace(0.0, 1.0, f, dtype=np.float32)
                            data[:f] *= fade_in[:, None]
                        self._fadein_frames = 0
                        self._in_gap = False
                    return data
                # hueco: silencio con fundido de salida (sin corte seco)
                m = avail
                out = np.zeros((n, 2), dtype=np.float32)
                if m > 0:
                    idx = self._whead % nframes
                    if idx + m <= nframes:
                        real = self._ring[idx:idx + m].copy()
                    else:
                        a = nframes - idx
                        real = np.concatenate([self._ring[idx:], self._ring[:m - a]])
                    self._whead += m
                    out[:m] = real
                    f = min(FADE, m)
                    if f > 0:
                        fade_out = np.linspace(1.0, 0.0, f, dtype=np.float32)
                        out[m - f:m] *= fade_out[:, None]
                self._in_gap = True
                self._fadein_frames = max(self._fadein_frames, FADE)
                return out

        def cap_callback(in_data, frame_count, time_info, status):
            x = np.frombuffer(in_data, dtype=np.float32)
            x = np.asarray(x[: frame_count * 2], dtype=np.float32)
            try:
                x = x.reshape(frame_count, 2)
            except ValueError:
                return (None, pa.paContinue)
            y = self.enhancer.process(x)
            _put(y)
            return (None, pa.paContinue)

        def _match_frame_count(data, frame_count):
            """Ajusta suavemente la cantidad leída al bloque solicitado.

            Leer unos frames de más o de menos corrige los relojes de captura y
            salida, pero interpolar el bloque evita el salto que producía quitar
            el primer frame o duplicar el último.
            """
            count = data.shape[0]
            if count == frame_count:
                return data
            if count <= 1 or frame_count <= 1:
                return np.resize(data, (frame_count, 2)).astype(np.float32, copy=False)
            positions = np.linspace(0.0, count - 1.0, frame_count, dtype=np.float32)
            base = np.arange(count, dtype=np.float32)
            return np.column_stack((
                np.interp(positions, base, data[:, 0]),
                np.interp(positions, base, data[:, 1]),
            )).astype(np.float32, copy=False)

        def out_callback(in_data, frame_count, time_info, status):
            # Control proporcional: la velocidad de captura y la de salida
            # pueden pertenecer a relojes distintos. Ajustar solo +-1 frame
            # fijo era demasiado lento; el ajuste ahora depende del error frente
            # al centro del ring y tiene límites inaudibles.
            with self._lock:
                fill = self._rhead - self._whead
            nonlocal drift_accum
            error = fill - drift_target
            if abs(error) <= drift_deadband:
                # Evita que el ruido normal del ring provoque resampling.
                drift_accum *= 0.95
                n_adj = 0
            else:
                # Acumulador fraccionario: una diferencia de reloj de 100 ppm
                # se reparte como un frame ocasional, no como un salto fijo.
                drift_accum += error * drift_gain
                n_adj = int(np.trunc(drift_accum))
                n_adj = max(-max_drift_frames, min(max_drift_frames, n_adj))
                drift_accum -= n_adj
            data = _get(max(1, frame_count + n_adj))
            data = _match_frame_count(data, frame_count)
            return (data.tobytes(), pa.paContinue)

        self.stream = self.pa.open(
            format=pa.paFloat32,
            channels=2,
            rate=rate,
            frames_per_buffer=CHUNK,
            input=True,
            output=False,
            input_device_index=in_idx,
            stream_callback=cap_callback,
        )
        self.stream.start_stream()
        # Pre-carga el ring antes de abrir la salida: si la salida arranca con
        # el ring vacio, la compensacion de deriva tarda mucho en llenarlo y se
        # oye silencio/retraso. Unos milisegundos fijan la latencia inicial
        # dentro de la banda (== alta ~85 ms) sin cortar nada.
        time.sleep(0.08)
        self.out_stream = self.pa.open(
            format=pa.paFloat32,
            channels=2,
            rate=rate,
            frames_per_buffer=CHUNK,
            input=False,
            output=True,
            output_device_index=out_idx,
            stream_callback=out_callback,
        )
        self.out_stream.start_stream()
        self.running = True
        self.start_button.configure(text="⏹  " + self._t("Detener audio del sistema"))
        self.status.configure(text="Activo (ring buffer): %s → %s" % (src["name"], out["name"]), text_color=OK)

    def _stop(self):
        for s in (self.stream, self.out_stream):
            if s is not None:
                try:
                    s.stop_stream()
                    s.close()
                except Exception:
                    pass
        self.stream = None
        self.out_stream = None
        self._ring = None
        self.running = False
        self.start_button.configure(text="▶  " + self._t("Iniciar audio del sistema"))
        self.status.configure(text=self._t("Procesamiento detenido"), text_color=WARN)

    # ---------- guia VB-CABLE ----------

    def _open_cable_folder(self):
        import subprocess, os, tempfile
        folder = os.path.join(tempfile.gettempdir(), "opencode", "VBCABLE", "extracted")
        if os.path.isdir(folder):
            subprocess.Popen(["explorer", folder])
            self.status.configure(text="Se abrió la carpeta con el instalador de VB-CABLE.",
                                  text_color=OK)
        else:
            self.status.configure(text="Carpeta del instalador VB-CABLE no encontrada.",
                                  text_color=DANGER)

    def _show_cable_guide(self):
        msg = (
            "Para un loopback propio (sin el APO de FxSound) hace falta el driver "
            "virtual VB-CABLE.\n\n"
            "1) Se abrió la carpeta con el instalador descargado.\n"
            "2) Ejecuta VBCABLE_Setup_x64.exe COMO ADMINISTRADOR "
            "(clic derecho > Ejecutar como administrador).\n"
            "3) Pulsa 'Install Driver' y espera el mensaje de éxito.\n"
            "4) Reinicia Windows.\n"
            "5) En Sonido > Salida, pon 'CABLE Input (VB-Audio Virtual Cable)' "
            "como dispositivo predeterminado.\n\n"
            "Después, esta app capturará 'CABLE Input' y solo la salida física sonará. "
            "No necesita FxSound."
        )
        from tkinter import messagebox
        self.root.after(0, lambda: messagebox.showinfo("Loopback propio (VB-CABLE)", msg))
        self.root.after(100, self._open_cable_folder)


def _bring_existing_to_front():
    """Busca una ventana principal de otra instancia por titulo y la restaura.
    Devuelve True si existe (esta copia debe salir). Cubre tambien instancias
    viejas que no usan mutex (p.ej. autostart de una build anterior)."""
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32

        u32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
        u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u32.GetWindowTextW.restype = ctypes.c_int
        u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u32.GetWindowTextLengthW.restype = ctypes.c_int
        u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u32.SetForegroundWindow.argtypes = [wintypes.HWND]
        u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
        u32.GetWindowThreadProcessId.restype = wintypes.DWORD

        my_pid = k32.GetCurrentProcessId()
        expected = "Audio Enhancer - FxStyle"
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, lparam):
            if u32.GetWindowTextLengthW(hwnd) <= 0:
                return True
            buf = ctypes.create_unicode_buffer(256)
            n = u32.GetWindowTextW(hwnd, buf, 256)
            if n > 0 and buf.value == expected:
                pid = wintypes.DWORD()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value and pid.value != my_pid:
                    found.append(hwnd)
                    return False
            return True

        u32.EnumWindows(_cb, 0)
        if found:
            hwnd = found[0]
            u32.ShowWindow(hwnd, 9)      # SW_RESTORE
            u32.SetForegroundWindow(hwnd)
            return True
        return False
    except Exception:
        return False


def _acquire_single_instance():
    """Candado de instancia unica: mutex nombrado + deteccion por titulo de
    ventana. Si ya hay otra instancia (antigua o nueva), la trae al frente y
    devuelve None para que esta salga sin abrir otra ventana."""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE

        # 1) cualquier ventana existente de otra instancia (incluidas viejas
        #    builds que no tienen mutex): restaurar y salir.
        if _bring_existing_to_front():
            return None

        # 2) mutex para bloquear instancias nuevas.
        mutex_name = "Local\\AudioEnhancerFxStyle_SingleInstance"
        handle = k32.CreateMutexW(None, False, mutex_name)
        err = k32.GetLastError()
        if err == 183:  # ERROR_ALREADY_EXISTS
            _bring_existing_to_front()
            return None
        return handle
    except Exception:
        return object()  # sin guarda: dejar pasar


def main():
    mutex = _acquire_single_instance()
    if mutex is None:
        return  # ya hay otra instancia corriendo
    root = ctk.CTk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app._on_close)
    # icono de bandeja desde el arranque (no solo al cerrar la ventana)
    app.root.after(600, app._start_tray)
    root.mainloop()


if __name__ == "__main__":
    main()