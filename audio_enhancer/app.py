# -*- coding: utf-8 -*-
"""Capa de interfaz: ventana principal, dispositivos, presets, medidor y tray.

App coordina: descubrimiento de dispositivos (PortAudio + WASAPI loopback),
configuración persistida, la UI customtkinter, el motor de audio (AudioEngine)
y el icono de bandeja. Todo el trabajo pesado corre en hilos y la UI se
actualiza desde el hilo principal.
"""

import logging
import math
import os
import sys
import threading
import time
import tkinter as tk

import customtkinter as ctk

from .config import load_config, save_config
from .constants import (ACCENT, CABLE_KEYWORDS, CONFIG_PATH, DANGER, DEFAULT_PRESET,
                        OK, VIRTUAL_CABLE_KEYWORDS, WARN, WINDOW_TITLE,
                        resource_path)
from .dsp import Enhancer
from .engine import AudioEngine, _pa
from .i18n import EQ_EXPLAIN, EQ_EXPLAIN_EN, EXPLAIN, EXPLAIN_EN, PRESETS, detect_system_language, translate
from .tray import TrayIcon
from .widgets import ScrollBody, ToolTip

logger = logging.getLogger("audio_enhancer.app")

try:
    import pystray  # noqa: F401
    _HAVE_TRAY = True
except Exception:
    _HAVE_TRAY = False


class App:
    def __init__(self, root):
        self.root = root
        self.language = detect_system_language()
        self.root.title(WINDOW_TITLE)
        self.root.geometry("860x740")
        self.root.minsize(760, 560)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.enhancer = Enhancer()
        self.engine = AudioEngine(self.enhancer)
        self.custom_presets = {}
        self.running = False
        self._closing = False
        self.pa = None
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
        self._cable_guide_shown = False
        self._prefill_deadline = 0.0
        self._open_output_args = None

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
            logger.exception("Fallo al descubrir dispositivos")
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
            self.status.configure(text=self._t("Dispositivos actualizados."), text_color=OK)
        self._keep_src = ""
        self._keep_out = ""
        if not self._has_cable() and not self._cable_guide_shown:
            self._cable_guide_shown = True
            self.root.after(400, self._show_cable_guide)

    def _discover_devices(self):
        pa = _pa()
        if self.pa is None:
            try:
                self.pa = pa.PyAudio()
            except Exception as exc:
                self.pa = None
                logger.exception("PyAudio error: %s", exc)
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
                if self._is_fxsound(n) or any(k in n for k in VIRTUAL_CABLE_KEYWORDS):
                    continue  # excluir virtuales/cables de la salida
                self.speakers.append(d)

    def _has_cable(self):
        return any(any(k in m["name"].lower() for k in CABLE_KEYWORDS)
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
        self.status.configure(text=self._t("Detectando dispositivos..."), text_color=WARN)
        self._waiting_devices = True
        threading.Thread(target=self._discover_in_thread, daemon=True).start()
        self.root.after(50, self._poll_devices)

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
                if any(k in n for k in CABLE_KEYWORDS):
                    idx = i
                    break
            else:
                for i, d in enumerate(self.loopbacks):
                    if any(k in d["name"].lower() for k in VIRTUAL_CABLE_KEYWORDS) \
                            and not self._is_fxsound(d["name"]):
                        idx = i
                        break
            self.source_var.set(self.loopbacks[idx]["name"])
        if self.speakers:
            # preferir salida fisica real (Synaptics/Realtek...) excluyendo virtuales
            pref = 0
            for i, d in enumerate(self.speakers):
                n = d["name"].lower()
                if self._is_fxsound(n) or any(k in n for k in VIRTUAL_CABLE_KEYWORDS):
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
        self.preset_var = ctk.StringVar(value=DEFAULT_PRESET)
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
        eq_explain = EQ_EXPLAIN_EN if self.language == "en" else EQ_EXPLAIN
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
            band_help = eq_explain.get(freq, EXPLAIN["eq"])
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
            self.status.configure(text=self._t("A/B: audio directo sin efectos (B)"), text_color=WARN)
        else:
            self.ab_button.configure(text="A: Efectos ON", fg_color=ACCENT)
            self.status.configure(text=self._t("A/B: audio procesado con efectos (A)"), text_color=OK)

    def toggle_limiter(self):
        self.enhancer.limiter = bool(self.limiter_switch.get())
        self.status.configure(text=self._t("Limitador suave: %s") % ("ON" if self.enhancer.limiter else "OFF"),
                              text_color=OK)

    def toggle_compressor(self):
        self.enhancer.compressor = bool(self.comp_switch.get())
        self.status.configure(text=self._t("Compresor RMS: %s") % ("ON" if self.enhancer.compressor else "OFF"),
                              text_color=OK)

    def _on_autostart_toggle(self):
        ok = self._set_auto_start(bool(self.autostart_var.get()))
        if not ok:
            self.autostart_var.set(not self.autostart_var.get())
        text = (self._t("Inicio con Windows: activado") if ok
                else self._t("Inicio con Windows: fallo al configurar"))
        self.status.configure(text=text, text_color=OK if ok else DANGER)

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
            db = 20 * math.log10(peak) if peak > 1e-6 else -60.0
            self.meter_label.configure(text="%.1f dB" % db)
            color = OK
            if bar > 0.85:
                color = DANGER
            elif bar > 0.6:
                color = WARN
            self.meter_bar.configure(progress_color=color)
            if self.running:
                if self.enhancer.spectrum_enabled:
                    self.enhancer.compute_spectrum()
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
        self.status.configure(text=self._t("Configuración aplicada: %s") % name, text_color=OK)

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
            self.status.configure(text=self._t("Escribe un nombre para el preset personalizado."),
                                  text_color=WARN)
            return
        self.custom_presets[name] = (
            float(self.enhancer.volume), float(self.enhancer.bass),
            float(self.enhancer.treble),
            [float(g) for g in self.enhancer.eq_gains])
        self.preset_entry.delete(0, "end")
        self._refresh_preset_list(keep=name)
        self._save_config()
        self.status.configure(text=self._t("Preset personalizado guardado: %s") % name, text_color=OK)

    def _delete_custom_preset(self):
        name = self.preset_var.get()
        if name not in self.custom_presets:
            self.status.configure(text=self._t("Selecciona un preset personalizado para borrarlo."),
                                  text_color=WARN)
            return
        del self.custom_presets[name]
        self._refresh_preset_list(keep=DEFAULT_PRESET)
        self._save_config()
        self.status.configure(text=self._t("Preset personalizado borrado: %s") % name, text_color=OK)

    def reset(self):
        self.apply_preset(DEFAULT_PRESET)
        self.preset_var.set(DEFAULT_PRESET)
        self.status.configure(text=self._t("Controles restablecidos a plano"), text_color=OK)

    def _on_close(self):
        """Al cerrar la ventana: si hay bandeja, minimiza a ella; si no, sale."""
        if _HAVE_TRAY:
            self._start_tray()
            self._save_config()
            self.root.withdraw()
            self.status.configure(text=self._t("Procesando en segundo plano (icono en bandeja)."),
                                  text_color=WARN)
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
                logger.exception("Error al detener audio en shutdown")
        try:
            if self.pa is not None:
                self.pa.terminate()
        except Exception:
            logger.exception("Error al terminar PyAudio en shutdown")
        try:
            if self.tray_icon is not None:
                self.tray_icon.stop()
        except Exception:
            logger.exception("Error al detener el icono de bandeja")
        self.root.destroy()

    # ---------- bandeja del sistema ----------

    def _set_window_icon(self):
        try:
            ico = resource_path("app.ico")
            if os.path.exists(ico):
                self.root.iconbitmap(default=ico)
        except Exception:
            pass

    def _tray_image(self):
        try:
            from PIL import Image
            path = resource_path("tray.png")
            return Image.open(path)
        except Exception:
            return None

    def _start_tray(self):
        if not _HAVE_TRAY or self.tray_icon is not None:
            return
        img = self._tray_image()
        if img is None:
            return
        self.tray_icon = TrayIcon(self.root, img,
                                  show_hide=self._do_toggle_show,
                                  toggle_audio=self.toggle,
                                  on_quit=self._shutdown)
        self.tray_icon.start()

    def _tray_toggle_show(self, icon=None, item=None):
        self.root.after(0, self._do_toggle_show)

    def _do_toggle_show(self):
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
            logger.exception("Fallo al configurar el inicio con Windows")
            return False

    def _apply_config(self):
        cfg = load_config()
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
        self._refresh_preset_list(keep=cfg.get("preset", DEFAULT_PRESET))
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
        if not save_config(cfg):
            logger.warning("No se pudo guardar config en %s", CONFIG_PATH)

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
            label.configure(text=self._t("Selecciona una fuente de captura y una salida física."),
                            text_color=WARN)
            return

        same = self._norm(src_name) == self._norm(out_name)
        src_is_virtual = any(k in src_name.lower() for k in VIRTUAL_CABLE_KEYWORDS)
        src_is_fxsound = self._is_fxsound(src_name)
        out_is_virtual = any(k in out_name.lower() for k in VIRTUAL_CABLE_KEYWORDS)

        if same:
            self.go = False
            label.configure(
                text=self._t("⚠  ECO: capturas y reproduces el mismo dispositivo (A → A). "
                             "Selecciona como captura el cable virtual donde suenan las apps "
                             "(p. ej. CABLE Input de VB-Audio) y como salida la física."),
                text_color=DANGER)
            return

        if out_is_virtual:
            self.go = False
            label.configure(
                text=self._t("⚠  La salida es virtual (cable). Reproduce en la salida FÍSICA "
                             "(parlantes reales) para no realimentar el cable."),
                text_color=DANGER)
            return

        if src_is_fxsound:
            label.configure(
                text=self._t("⚠  Estás capturando el loopback de FxSound (otra app). Si no percibes "
                             "efecto o hay conflicto, instala VB-CABLE y captura 'CABLE Input'."),
                text_color=WARN)
        elif src_is_virtual:
            label.configure(
                text=self._t("✔  Ruteo correcto: capturas tu cable virtual y solo la salida física "
                             "reproduce el audio procesado. Cierra FxSound para no duplicar el efecto."),
                text_color=OK)
        else:
            label.configure(
                text=self._t("Info: capturas un parlante físico. Asegúrate de que sea el dispositivo "
                             "donde suenan las apps y que la salida sea otro distinto."),
                text_color=("gray30", "gray60"))
        self.go = True

    def toggle(self):
        if self.running:
            self._stop()
            return
        src, out = self._selected()
        if not self.go or src is None or out is None:
            self._route_guard()
            self.status.configure(text=self._t("Revisa el ruteo: no captures y reproduzcas el mismo dispositivo."),
                                  text_color=DANGER)
            return
        try:
            self._start(src, out)
        except Exception as exc:
            logger.exception("No se pudo iniciar el loopback")
            self.status.configure(text=self._t("No se pudo iniciar el loopback: %s") % exc, text_color=DANGER)
            self.running = False

    def _start(self, src, out):
        pa = _pa()
        if self.pa is None:
            self.pa = pa.PyAudio()
        in_idx = src["index"]
        out_idx = out["index"]

        # Frecuencia de muestreo dinamica: usa la del dispositivo de salida
        # (evita el re-muestreo del mezclador y su latencia extra).
        rate = int(out.get("defaultSampleRate", 48000))
        if rate < 8000 or rate > 384000:
            rate = 48000
        if rate != self.enhancer.sample_rate:
            self.enhancer.sample_rate = rate
            self.enhancer._spec_meta = None

        self.engine.configure_ring(rate)
        self.engine.start_capture(self.pa, in_idx, rate)
        self.running = True
        self.start_button.configure(text="⏹  " + self._t("Detener audio del sistema"))
        self.status.configure(text=self._t("Activando salida física..."), text_color=WARN)
        self._active_names = (src["name"], out["name"])
        self._prefill_deadline = time.time() + 0.5
        self._open_output_args = (out_idx, rate)
        self.root.after(10, self._poll_prefill)

    def _poll_prefill(self):
        """Espera sin bloquear la UI a que el ring llegue a ~medio antes de
        abrir la salida (fija la latencia inicial dentro de la banda ~85 ms)."""
        if not self.running:
            return  # se detuvo durante la espera
        if self.engine.fill() >= self.engine.nframes // 2 or time.time() > self._prefill_deadline:
            self._open_output()
        else:
            self.root.after(10, self._poll_prefill)

    def _open_output(self):
        out_idx, rate = self._open_output_args
        self._open_output_args = None
        try:
            self.engine.open_output(out_idx, rate)
        except Exception as exc:
            # Rollback: si la salida no pudo abrirse, la captura no debe quedar
            # abierta y activa para siempre (fuga de stream).
            self.engine.stop()
            self.running = False
            self.start_button.configure(text="▶  " + self._t("Iniciar audio del sistema"))
            self.status.configure(text=self._t("No se pudo iniciar el loopback: %s") % exc, text_color=DANGER)
            return
        src_name, out_name = self._active_names
        self.status.configure(text=self._t("Activo (ring buffer): %s → %s") % (src_name, out_name),
                              text_color=OK)

    def _stop(self):
        self.engine.stop()
        self.running = False
        self.start_button.configure(text="▶  " + self._t("Iniciar audio del sistema"))
        self.status.configure(text=self._t("Procesamiento detenido"), text_color=WARN)

    # ---------- guia VB-CABLE ----------

    def _open_cable_folder(self):
        import subprocess
        import tempfile
        folder = os.path.join(tempfile.gettempdir(), "opencode", "VBCABLE", "extracted")
        if os.path.isdir(folder):
            subprocess.Popen(["explorer", folder])
            self.status.configure(text=self._t("Se abrió la carpeta con el instalador de VB-CABLE."),
                                  text_color=OK)
        else:
            self.status.configure(text=self._t("Carpeta del instalador VB-CABLE no encontrada."),
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