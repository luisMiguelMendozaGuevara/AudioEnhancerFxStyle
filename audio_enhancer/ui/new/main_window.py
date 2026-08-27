from __future__ import annotations

import contextlib
import logging
import threading
import time

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ...config import load_config, save_config
from ...constants import (
    CABLE_KEYWORDS,
    CHUNK,
    DANGER,
    DEFAULT_PRESET,
    LATENCY_CHOICES_MS,
    OK,
    VIRTUAL_CABLE_KEYWORDS,
    WARN,
    WINDOW_TITLE,
    resource_path,
)
from ...dsp import Enhancer, EnhancerParams
from ...engine import AudioEngine, _pa
from ...i18n import PRESETS, detect_system_language, translate
from ...startup_metrics import StartupMetrics
from .audio_state import AudioState
from .pages.audio import AudioPage
from .pages.effects import EffectsPage
from .pages.equalizer import EqualizerPage
from .pages.home import HomePage
from .pages.presets import PresetsPage
from .pages.settings import SettingsPage
from .theme.colors import Theme
from .widgets.sidebar import Sidebar
from .widgets.status_bar import AppStatusBar

logger = logging.getLogger("audio_enhancer.new_ui")
AUTOSTART_KEY = "AudioEnhancerFxStyle"


class DeviceDiscoveryWorker(QObject):
    finished = Signal(object, object, object, str)

    @Slot()
    def run(self) -> None:
        pa = None
        try:
            pa_mod = _pa()
            pa = pa_mod.PyAudio()
            wasapi_idx = None
            with contextlib.suppress(Exception):
                wasapi_idx = pa.get_host_api_info_by_type(pa_mod.paWASAPI)["index"]
            loopbacks = list(pa.get_loopback_device_info_generator())
            speakers = []
            for index in range(pa.get_device_count()):
                device = pa.get_device_info_by_index(index)
                if device.get("isLoopbackDevice"):
                    continue
                if wasapi_idx is not None and device["hostApi"] != wasapi_idx:
                    continue
                if device["maxOutputChannels"] > 0 and device["maxInputChannels"] == 0:
                    name = device["name"].lower()
                    if "fxsound" in name or any(k in name for k in VIRTUAL_CABLE_KEYWORDS):
                        continue
                    speakers.append(device)
            self.finished.emit(pa, loopbacks, speakers, "")
        except Exception as exc:
            logger.exception("Device discovery failed")
            self.finished.emit(pa, [], [], str(exc))


class SpectrumWorker(QThread):
    spectrum_ready = Signal(object)

    def __init__(self, enhancer: Enhancer, parent=None) -> None:
        super().__init__(parent)
        self.enhancer = enhancer
        self.active = threading.Event()

    def set_active(self, active: bool) -> None:
        self.active.set() if active else self.active.clear()

    def run(self) -> None:
        while not self.isInterruptionRequested():
            if self.active.is_set() and self.enhancer.spectrum_enabled:
                try:
                    self.enhancer.compute_spectrum()
                    spec = self.enhancer.spectrum
                    self.spectrum_ready.emit(None if spec is None else [float(v) for v in spec])
                except Exception:
                    # Antes tragaba la excepción en silencio: un fallo repetido
                    # del analizador dejaba el espectro congelado sin rastro
                    # en el log. Se deja constancia (debug: es el hilo visual,
                    # no debe ensuciar el log de producción).
                    logger.debug("compute_spectrum falló", exc_info=True)
            self.msleep(33)

    def stop(self) -> None:
        self.requestInterruption()
        self.active.clear()
        self.wait(1500)


class NewMainWindow(QMainWindow):
    def __init__(self, startup_metrics=None) -> None:
        super().__init__()
        self.metrics = startup_metrics or StartupMetrics()
        self.metrics.mark("root_created")
        self.language = detect_system_language()
        # Idioma guardado por el usuario tiene prioridad sobre el del sistema.
        with contextlib.suppress(Exception):
            saved_lang = (load_config() or {}).get("language")
            if saved_lang in ("es", "en"):
                self.language = saved_lang
        self.enhancer = Enhancer()
        self.engine = AudioEngine(self.enhancer)
        self.state = AudioState(self)
        # Estado -> DSP: los controles de las paginas escriben en AudioState;
        # sin este puente los sliders no afectan al audio (solo a la UI).
        # En __init__ para que exista antes de cualquier interaccion.
        state = self.state
        state.bass_changed.connect(lambda v: setattr(self.enhancer, "bass", float(v)))
        state.treble_changed.connect(lambda v: setattr(self.enhancer, "treble", float(v)))
        state.eq_changed.connect(lambda g: setattr(self.enhancer, "eq_gains", [float(x) for x in g]))
        state.limiter_changed.connect(lambda on: setattr(self.enhancer, "limiter", bool(on)))
        state.compressor_changed.connect(lambda on: setattr(self.enhancer, "compressor", bool(on)))
        state.volume_changed.connect(lambda v: setattr(self.enhancer, "volume", float(v)))
        self.custom_presets = {}
        self.loopbacks = []
        self.speakers = []
        self.pa = None
        self.running = False
        self.go = False
        self._closing = False
        self._open_output_args = None
        self._prefill_deadline = 0.0
        self._active_names = ("", "")
        self._metrics_tick = 0  # refresco de métricas ~1 Hz (timer a 33 ms)
        self._keep_src = ""
        self._keep_out = ""
        self._latest_spectrum = None
        self._discovery_thread = None
        self._discovery_worker = None
        self._spectrum_worker = SpectrumWorker(self.enhancer, self)
        self.tray = None
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(Theme.DEFAULT_WIDTH, Theme.DEFAULT_HEIGHT)
        self.setMinimumSize(Theme.MIN_WIDTH, Theme.MIN_HEIGHT)
        self.setWindowIcon(QIcon(resource_path("app.ico")))
        self.setStyleSheet(Theme.stylesheet())
        self._build_shell()
        self._build_tray()

    def _t(self, text):
        return translate(text, self.language)

    def _build_shell(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._sidebar = Sidebar()
        self._sidebar.page_requested.connect(self._navigate_to)
        self._sidebar.setStyleSheet(f"background: {Theme.SIDEBAR_BG};")
        root_layout.addWidget(self._sidebar)

        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background: {Theme.BORDER};")
        root_layout.addWidget(sep)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(Theme.HEADER_HEIGHT)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(Theme.SPACING_LG, 0, Theme.SPACING_LG, 0)
        self._header_title = QLabel("AudioEnhancer")
        self._header_title.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_TITLE}px; "
            f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
        )
        header_layout.addWidget(self._header_title)
        header_layout.addStretch()
        self._header_status = QLabel("")
        self._header_status.setStyleSheet(
            f"color: {Theme.SUCCESS}; font-size: {Theme.FONT_SIZE_MD}px; "
            f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
        )
        header_layout.addWidget(self._header_status)
        right_layout.addWidget(header)

        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background: {Theme.BORDER};")
        right_layout.addWidget(sep2)

        self._stack = QStackedWidget()
        self._pages = {}
        self._pages["home"] = HomePage(self.state)
        self._pages["equalizer"] = EqualizerPage(self.state)
        self._pages["effects"] = EffectsPage(self.state)
        self._pages["audio"] = AudioPage(self.state)
        self._pages["presets"] = PresetsPage(self.state)
        self._pages["settings"] = SettingsPage(self.state)
        for page in self._pages.values():
            self._stack.addWidget(page)
        right_layout.addWidget(self._stack, 1)

        self._status_bar = AppStatusBar()
        sep3 = QFrame()
        sep3.setFixedHeight(1)
        sep3.setStyleSheet(f"background: {Theme.BORDER};")
        right_layout.addWidget(sep3)
        right_layout.addWidget(self._status_bar)

        root_layout.addWidget(right, 1)
        self.metrics.mark("shell")

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(QIcon(resource_path("app.ico")), self)
        menu = QMenu()
        show_act = menu.addAction(self._t("Mostrar / Ocultar"))
        show_act.triggered.connect(self._toggle_show)
        audio_act = menu.addAction(self._t("Iniciar / Detener"))
        audio_act.triggered.connect(self.toggle_audio)
        menu.addSeparator()
        quit_act = menu.addAction(self._t("Salir"))
        quit_act.triggered.connect(self._quit_from_tray)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip(WINDOW_TITLE)
        self.tray.activated.connect(self._on_tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def build_content(self) -> None:
        if hasattr(self, "_content_built"):
            return
        self._content_built = True
        self.metrics.mark("first_paint")
        self._wire_pages()
        self._apply_config()
        self.metrics.mark("ui_ready")
        self._status_bar.set_status_text(self._t("Detectando dispositivos..."), WARN)
        self._start_discovery()
        self._spectrum_worker.spectrum_ready.connect(self._receive_spectrum)
        self._spectrum_worker.start()
        self.visual_timer = QTimer(self)
        self.visual_timer.setInterval(33)
        self.visual_timer.timeout.connect(self._refresh_visuals)
        self.visual_timer.start()
        self._sidebar.set_active("home")
        logger.warning("New UI ready: %s", self.metrics.summary())

    def _wire_pages(self) -> None:
        home = self._pages["home"]
        home._start_button.clicked.connect(self.toggle_audio)
        home._preset_combo.currentTextChanged.connect(self._on_preset_selected)
        home._ab_button.clicked.connect(self.toggle_ab)
        home._volume_slider.valueChanged.connect(self._on_volume_slider)
        audio_page = self._pages["audio"]
        audio_page._input_combo.currentTextChanged.connect(self._route_guard)
        audio_page._output_combo.currentTextChanged.connect(self._route_guard)
        audio_page._refresh_btn.clicked.connect(self._start_discovery)
        audio_page._latency_combo.currentIndexChanged.connect(self._on_latency_pref_changed)
        self._pages["presets"]._save_btn.clicked.connect(self._save_custom_preset)
        self._pages["presets"].delete_requested.connect(self._delete_custom_preset)
        self._pages["settings"]._autostart_check.toggled.connect(self._toggle_autostart)
        self._pages["settings"]._theme_combo.currentTextChanged.connect(self._on_theme_changed)
        self._pages["settings"]._lang_combo.currentTextChanged.connect(self._on_language_changed)
        self._refresh_preset_list()

    def _on_language_changed(self, text: str) -> None:
        """Guarda la preferencia de idioma (se aplica al reiniciar: las
        paginas ya construidas no se retraducen en caliente)."""
        self.language = "en" if text.strip().lower().startswith("engl") else "es"
        self._save_config()
        self._status_bar.set_status_text(self._t("Idioma guardado. Se aplicara al reiniciar."), WARN)

    def _on_latency_pref_changed(self, index: int) -> None:
        """Guarda la preferencia de latencia (40/60/100 ms). Se aplica en el
        próximo arranque del audio: cambiarla en caliente requeriría vaciar el
        ring (glitch seguro)."""
        ms = int(self._pages["audio"]._latency_combo.itemData(index) or 60)
        self.state.latency_pref = ms
        self._save_config()
        if self.running:
            self._status_bar.set_status_text(self._t("Latencia %d ms: se aplicara al reiniciar el audio.") % ms, WARN)
        else:
            self._status_bar.set_status_text(self._t("Latencia objetivo: %d ms") % ms, OK)

    def _on_theme_changed(self, text: str) -> None:
        """Cambia dark/white y reaplica QSS + repaint de todos los widgets."""
        mode = "light" if text.lower().startswith(("blanc", "white", "clar")) else "dark"
        if mode == Theme.mode:
            return
        Theme.set_mode(mode)
        self.setStyleSheet(Theme.stylesheet())
        from PySide6.QtWidgets import QApplication

        for widget in QApplication.allWidgets():
            widget.update()

    def _navigate_to(self, page_id: str) -> None:
        page = self._pages.get(page_id)
        if not page:
            return
        if self._stack.currentWidget() is page:
            return
        self._stack.setCurrentWidget(page)
        # Transicion de entrada: fade corto ease-out. El efecto se retira al
        # terminar para no penalizar el repintado del spectrum/meters.
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", page)
        anim.setDuration(160)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: page.setGraphicsEffect(None))
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _start_discovery(self) -> None:
        if self._discovery_thread is not None and self._discovery_thread.isRunning():
            return
        audio_page = self._pages["audio"]
        if audio_page._input_combo.count():
            self._keep_src = audio_page._input_combo.currentText()
        if audio_page._output_combo.count():
            self._keep_out = audio_page._output_combo.currentText()
        audio_page._input_combo.setEnabled(False)
        audio_page._output_combo.setEnabled(False)
        audio_page._refresh_btn.setEnabled(False)
        self._status_bar.set_status_text(self._t("Detectando dispositivos..."), WARN)
        thread = QThread(self)
        worker = DeviceDiscoveryWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_devices_ready)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_discovery_refs)
        self._discovery_thread = thread
        self._discovery_worker = worker
        thread.start()

    @Slot(object, object, object, str)
    def _on_devices_ready(self, pa, loopbacks, speakers, error) -> None:
        self.pa = pa
        self.loopbacks = list(loopbacks)
        self.speakers = list(speakers)
        audio_page = self._pages["audio"]
        audio_page.set_loopbacks([d["name"] for d in self.loopbacks])
        audio_page.set_speakers([d["name"] for d in self.speakers])
        self._restore_device_selection()
        audio_page._input_combo.setEnabled(True)
        audio_page._output_combo.setEnabled(True)
        audio_page._refresh_btn.setEnabled(True)
        if error:
            self._status_bar.set_status_text(self._t("No se pudieron detectar dispositivos: %s") % error, DANGER)
        else:
            self._auto_select()
            self._route_guard()
            self._status_bar.set_status_text(self._t("Dispositivos listos."), OK)
            # Auto-arranque: el audio queda activo al abrir (como la UI
            # original). Solo si el ruteo es válido y no está ya corriendo.
            if self.go and not self.running:
                QTimer.singleShot(0, self.toggle_audio)
        self.metrics.mark("devices_ready")

    def _restore_device_selection(self) -> None:
        audio_page = self._pages["audio"]
        src_names = [d["name"] for d in self.loopbacks]
        out_names = [d["name"] for d in self.speakers]
        if self._keep_src in src_names:
            audio_page._input_combo.setCurrentText(self._keep_src)
        if self._keep_out in out_names:
            audio_page._output_combo.setCurrentText(self._keep_out)
        self._keep_src = ""
        self._keep_out = ""

    def _clear_discovery_refs(self) -> None:
        self._discovery_thread = None
        self._discovery_worker = None

    def _auto_select(self) -> None:
        audio_page = self._pages["audio"]
        if self.loopbacks and not audio_page._input_combo.currentText():
            idx = 0
            for i, d in enumerate(self.loopbacks):
                if any(k in d["name"].lower() for k in CABLE_KEYWORDS):
                    idx = i
                    break
            audio_page._input_combo.setCurrentIndex(idx)
        if self.speakers and not audio_page._output_combo.currentText():
            audio_page._output_combo.setCurrentIndex(0)

    @staticmethod
    def _norm(name):
        return "".join(name.lower().split())

    def _route_guard(self, *_args) -> None:
        audio_page = self._pages["audio"]
        src_name = audio_page._input_combo.currentText() or ""
        out_name = audio_page._output_combo.currentText() or ""
        if not src_name or not out_name:
            self.go = False
            audio_page.set_route_warning(self._t("Selecciona una fuente y una salida."), WARN)
            return
        same = self._norm(src_name) == self._norm(out_name)
        out_is_virtual = any(k in out_name.lower() for k in VIRTUAL_CABLE_KEYWORDS)
        src_is_virtual = any(k in src_name.lower() for k in VIRTUAL_CABLE_KEYWORDS)
        if same:
            self.go = False
            audio_page.set_route_warning(self._t("ECO: capturas y reproduces el mismo dispositivo."), DANGER)
            return
        if out_is_virtual:
            self.go = False
            audio_page.set_route_warning(self._t("La salida es virtual. Usa salida fisica."), DANGER)
            return
        self.go = True
        if src_is_virtual:
            audio_page.set_route_warning("Ruteo correcto: cable virtual -> salida fisica.", OK)
        else:
            audio_page.set_route_warning("Info: capturas un parlante fisico.", Theme.TEXT_DIM)

    def _all_presets(self):
        presets = dict(PRESETS)
        presets.update(self.custom_presets)
        return presets

    def _refresh_preset_list(self, keep=None) -> None:
        home = self._pages["home"]
        current = keep or home._preset_combo.currentText()
        names = list(self._all_presets().keys())
        home.set_preset_items(names)
        if current in self._all_presets():
            home.set_preset(current)
        # La pagina Presets muestra ambas listas (antes quedaba vacia).
        presets_page = self._pages["presets"]
        presets_page.set_included_presets(list(PRESETS.keys()))
        presets_page.set_custom_presets(list(self.custom_presets.keys()))

    def _delete_custom_preset(self, name: str) -> None:
        if name not in self.custom_presets:
            return
        self.custom_presets.pop(name)
        self._refresh_preset_list()
        self._save_config()
        self._status_bar.set_status_text(self._t("Preset eliminado: %s") % name, WARN)

    def _on_preset_selected(self, name: str) -> None:
        if not name or name not in self._all_presets():
            return
        vol, bass, treble, gains = self._all_presets()[name]
        # Cambio EN BLOQUE via instantánea inmutable: el DSP ve un conjunto
        # coherente de parámetros, nunca una mezcla a medias (H5/H6).
        self.enhancer.apply_params(
            EnhancerParams(
                volume=float(vol),
                bass=float(bass),
                treble=float(treble),
                eq_gains=tuple(float(g) for g in gains),
                limiter=bool(self.enhancer.limiter),
                compressor=bool(self.enhancer.compressor),
                blend=float(self.enhancer.blend),
            )
        )
        self._sync_ui_from_state()
        self.state.preset_name = name

    def _save_custom_preset(self) -> None:
        presets_page = self._pages["presets"]
        name = presets_page._name_entry.text().strip()
        if not name:
            self._status_bar.set_status_text(self._t("Escribe un nombre para el preset."), WARN)
            return
        self.custom_presets[name] = (
            float(self.enhancer.volume),
            float(self.enhancer.bass),
            float(self.enhancer.treble),
            [float(g) for g in self.enhancer.eq_gains],
        )
        presets_page._name_entry.clear()
        self._refresh_preset_list(name)
        self._save_config()

    def _sync_ui_from_state(self) -> None:
        home = self._pages["home"]
        home.set_volume(self.enhancer.volume)
        self._pages["equalizer"].set_gains(self.enhancer.eq_gains)
        self._pages["effects"].set_bass(self.enhancer.bass)
        self._pages["effects"].set_treble(self.enhancer.treble)
        self._pages["effects"].set_limiter(self.enhancer.limiter)
        self._pages["effects"].set_compressor(self.enhancer.compressor)
        home.set_ab(self.enhancer.blend > 0.5)
        # Consistencia: AudioState alineado con el DSP (los set_* de las
        # paginas usan blockSignals y no escriben en el estado).
        self.state.sync_from_enhancer(self.enhancer)

    def _on_volume_slider(self, raw: int) -> None:
        self.enhancer.volume = raw / 100.0
        self._pages["home"]._volume_label.setText(f"{raw / 100.0:.2f}x")

    def toggle_ab(self) -> None:
        self.enhancer.blend = 0.0 if self.enhancer.blend > 0.5 else 1.0
        enabled = self.enhancer.blend > 0.5
        self._pages["home"].set_ab(enabled)
        self.state.ab_enabled = enabled

    def toggle_audio(self) -> None:
        if self.running:
            self._stop_audio()
            return
        src_text = self._pages["audio"]._input_combo.currentText()
        out_text = self._pages["audio"]._output_combo.currentText()
        source = next((d for d in self.loopbacks if d["name"] == src_text), None)
        output = next((d for d in self.speakers if d["name"] == out_text), None)
        if not self.go or source is None or output is None:
            self._route_guard()
            self._status_bar.set_status_text(self._t("Revisa el ruteo."), DANGER)
            return
        try:
            self._start_audio(source, output)
        except Exception as exc:
            logger.exception("Failed to start")
            self._status_bar.set_status_text(self._t("No se pudo iniciar: %s") % exc, DANGER)

    def _start_audio(self, source, output) -> None:
        logger.warning("Auto/manual start: %s -> %s", source["name"], output["name"])
        pa_mod = _pa()
        if self.pa is None:
            self.pa = pa_mod.PyAudio()
        # La tasa la fija la FUENTE (loopback): la captura corre al ritmo del
        # dispositivo que reproduce; abrir la captura a la tasa del output
        # (codigo viejo, H5) desincronizaba relojes cuando ambas tasas
        # difieren. La salida física abre a la misma tasa (WASAPI compartido
        # remuestrea al rate nativo del dispositivo).
        rate = int(source.get("defaultSampleRate", 48000) or 48000)
        if rate < 8000 or rate > 384000:
            rate = 48000
        if rate != self.enhancer.sample_rate:
            # Cambio de tasa: los estados zi de biquads y compresor (y las
            # rampas) son historial de OTRA tasa; continuar con ellos inyecta
            # artefactos al arrancar (M3). reset_state también limpia caches
            # del analizador.
            self.enhancer.sample_rate = rate
            self.enhancer.reset_state()
        self.engine.configure_ring(rate, drift_target_ms=self.state.latency_pref)
        self.engine.start_capture(self.pa, source["index"], rate, device_info=source)
        self.running = True
        self._spectrum_worker.set_active(True)
        self._active_names = (source["name"], output["name"])
        self._prefill_deadline = time.time() + 0.5
        self._open_output_args = (output["index"], rate)
        self.state.processing = True
        self.state.input_device = source["name"]
        self.state.output_device = output["name"]
        self._status_bar.set_processing(True)
        self._status_bar.set_route(source["name"], output["name"])
        self._status_bar.set_sample_rate(rate)
        self._header_status.setText("ACTIVE")
        self._header_status.setStyleSheet(
            f"color: {Theme.SUCCESS}; font-size: {Theme.FONT_SIZE_MD}px; "
            f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
        )
        self._status_bar.set_status_text(self._t("Activando salida..."), WARN)
        QTimer.singleShot(10, self._poll_prefill)

    def _poll_prefill(self) -> None:
        if not self.running:
            return
        # Pre-cargar hasta la CONSIGNA de deriva (latencia objetivo), no a la
        # mitad del ring: la salida arranca ya en el punto de equilibrio.
        if self.engine.fill() >= self.engine.drift_target or time.time() > self._prefill_deadline:
            self._open_output()
        else:
            QTimer.singleShot(10, self._poll_prefill)

    def _open_output(self) -> None:
        if self._open_output_args is None:
            return
        out_index, rate = self._open_output_args
        self._open_output_args = None
        try:
            self.engine.open_output(out_index, rate)
            # Latencia REAL percibida: consigna del ring + un bloque de salida.
            # Reportar nframes/rate (codigo viejo) mostraba 200 ms cuando el
            # punto de operación real está en drift_target (~60 ms).
            latency = ((self.engine.drift_target + CHUNK) / rate) * 1000.0
            logger.warning(
                "Audio activo: ring a %d Hz, consigna %d frames, latencia %.1f ms",
                rate,
                self.engine.drift_target,
                latency,
            )
            self._status_bar.set_latency(latency)
            self._status_bar.set_status_text(self._t("Activo (ring buffer): %s -> %s") % self._active_names, OK)
            self._pages["audio"].set_info(rate=rate, buffer=1024, latency=latency, status="Processing")
        except Exception as exc:
            self.engine.stop()
            self.running = False
            self._spectrum_worker.set_active(False)
            self.state.processing = False
            self._status_bar.set_processing(False)
            self._header_status.setText("")
            self._status_bar.set_status_text(self._t("No se pudo iniciar: %s") % exc, DANGER)

    def _stop_audio(self) -> None:
        logger.warning("Audio detenido por el usuario")
        self.engine.stop()
        self.running = False
        self._spectrum_worker.set_active(False)
        self.state.processing = False
        self._status_bar.set_processing(False)
        self._header_status.setText("")
        self._status_bar.set_status_text(self._t("Procesamiento detenido"), WARN)
        self._pages["audio"].set_info(status="Detenido")

    def _receive_spectrum(self, values) -> None:
        self._latest_spectrum = values

    def _refresh_visuals(self) -> None:
        # Medidores honestos: la ENTRADA es RMS (energía percibida del material
        # capturado) y la SALIDA es pico post-DSP (lo que realmente puede
        # acercarse al techo). Antes ambos mostraban el mismo valor.
        self.state.input_level = float(self.enhancer.level_rms)
        self.state.output_level = float(self.enhancer.level_peak)
        self.state.peak_level = float(self.enhancer.level_peak)
        if self._latest_spectrum is not None:
            self.state.spectrum = self._latest_spectrum
            self._latest_spectrum = None
        # Métricas del motor ~1 Hz (el timer corre a 33 ms): contadores vivos
        # de underruns/huecos/deriva en la barra de estado.
        self._metrics_tick = (self._metrics_tick + 1) % 30
        if self._metrics_tick == 0:
            if self.running:
                s = self.engine.stats_snapshot()
                text = "unders %d | huecos %d | deriva %d fr" % (
                    s["output_underruns"],
                    s["gap_blocks"],
                    s["drift_adjust_frames"],
                )
            else:
                text = ""
            self._status_bar.set_metrics(text)

    def _apply_config(self) -> None:
        config = load_config()
        if not config:
            self._refresh_preset_list(DEFAULT_PRESET)
            return
        self._keep_src = str(config.get("source", "") or "")
        self._keep_out = str(config.get("output", "") or "")
        custom = config.get("custom_presets")
        if isinstance(custom, dict):
            self.custom_presets = {}
            for name, value in custom.items():
                if isinstance(value, (list, tuple)) and len(value) == 4:
                    self.custom_presets[str(name)] = tuple(value)
        gains = config.get("eq_gains")
        gains_ok = isinstance(gains, list) and len(gains) == len(self.enhancer.eq_gains)
        self.enhancer.apply_params(
            EnhancerParams(
                volume=float(config.get("volume", 1.0)),
                bass=float(config.get("bass", 0.0)),
                treble=float(config.get("treble", 0.0)),
                eq_gains=tuple(float(g) for g in gains) if gains_ok else tuple(self.enhancer.eq_gains),
                limiter=bool(config.get("limiter", True)),
                compressor=bool(config.get("compressor", True)),
                blend=float(self.enhancer.blend),
            )
        )
        # Preferencia de latencia persistida (40/60/100 ms).
        lat = int(config.get("latency_pref", 60) or 60)
        self.state.latency_pref = lat if lat in LATENCY_CHOICES_MS else 60
        self._pages["audio"].set_latency_pref(self.state.latency_pref)
        self._refresh_preset_list(config.get("preset", DEFAULT_PRESET))
        self._sync_ui_from_state()
        settings = self._pages["settings"]
        settings._autostart_check.blockSignals(True)
        settings._autostart_check.setChecked(self._autostart_enabled())
        settings._autostart_check.blockSignals(False)
        settings._lang_combo.blockSignals(True)
        settings._lang_combo.setCurrentText("English" if self.language == "en" else "Espanol")
        settings._lang_combo.blockSignals(False)

    def _save_config(self) -> None:
        audio_page = self._pages["audio"]
        home = self._pages["home"]
        config = {
            "source": audio_page._input_combo.currentText(),
            "output": audio_page._output_combo.currentText(),
            "preset": home._preset_combo.currentText() or DEFAULT_PRESET,
            "language": self.language,
            "volume": float(self.enhancer.volume),
            "bass": float(self.enhancer.bass),
            "treble": float(self.enhancer.treble),
            "eq_gains": [float(g) for g in self.enhancer.eq_gains],
            "limiter": bool(self.enhancer.limiter),
            "compressor": bool(self.enhancer.compressor),
            "latency_pref": int(self.state.latency_pref),
            "custom_presets": {n: list(v) for n, v in self.custom_presets.items()},
        }
        if not save_config(config):
            logger.warning("Failed to save config")

    def _autostart_enabled(self) -> bool:
        try:
            import winreg

            run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_READ)
            try:
                val, _ = winreg.QueryValueEx(key, AUTOSTART_KEY)
                return bool(val)
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False

    def _set_auto_start(self, enable: bool) -> bool:
        try:
            import winreg

            run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE)
            if enable:
                target = """ + sys.executable + "" "" + os.path.abspath(sys.argv[0]) + """
                winreg.SetValueEx(key, AUTOSTART_KEY, 0, winreg.REG_SZ, target)
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, AUTOSTART_KEY)
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    def _toggle_autostart(self, enabled: bool) -> None:
        if self._set_auto_start(enabled):
            self._status_bar.set_status_text(self._t("Inicio con Windows: activado"), OK)
        else:
            self._status_bar.set_status_text(self._t("Inicio con Windows: fallo"), DANGER)
            self._pages["settings"]._autostart_check.blockSignals(True)
            self._pages["settings"]._autostart_check.setChecked(not enabled)
            self._pages["settings"]._autostart_check.blockSignals(False)

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_show()

    def _toggle_show(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _quit_from_tray(self) -> None:
        self._shutdown()
        app = QApplication.instance()
        if app:
            app.quit()

    def _shutdown(self) -> None:
        self._closing = True
        if getattr(self, "visual_timer", None) is not None:
            self.visual_timer.stop()
        if self.running:
            with contextlib.suppress(Exception):
                self._stop_audio()
        self._spectrum_worker.stop()
        self._save_config()
        if self._discovery_thread and self._discovery_thread.isRunning():
            self._discovery_thread.quit()
            self._discovery_thread.wait(1500)
        with contextlib.suppress(Exception):
            if self.pa:
                self.pa.terminate()
        if self.tray:
            self.tray.hide()

    def closeEvent(self, event) -> None:
        if self.tray and self.tray.isVisible():
            self._save_config()
            self.hide()
            self._status_bar.set_status_text(self._t("Procesando en segundo plano."), WARN)
            event.ignore()
        else:
            self._shutdown()
            event.accept()
