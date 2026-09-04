from __future__ import annotations

import contextlib
import logging
import threading

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

from ...audio_controller import AudioController
from ...autostart import is_enabled as _autostart_enabled
from ...autostart import set_enabled as _set_auto_start
from ...config import load_config, save_config
from ...constants import (
    CABLE_KEYWORDS,
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
from ...engine import _pa
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


class DeviceDiscoveryWorker(QObject):
    # (loopbacks, speakers, error) — sin PyAudio: cada worker usa su propia
    # instancia temporal y la termina al acabar (PortAudio NO es thread-safe
    # para enumerar en un hilo mientras otro hilo tiene streams abiertos en
    # la misma instancia).
    finished = Signal(object, object, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

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
            self.finished.emit(loopbacks, speakers, "")
        except Exception as exc:
            logger.exception("Device discovery failed")
            self.finished.emit([], [], str(exc))
        finally:
            # La instancia de enumeracion es efimera y privada de este worker.
            if pa is not None:
                with contextlib.suppress(Exception):
                    pa.terminate()


class SpectrumWorker(QThread):
    spectrum_ready = Signal(object)

    def __init__(self, enhancer: Enhancer, parent=None) -> None:
        super().__init__(parent)
        self.enhancer = enhancer
        # "active": hay audio que analizar (motor corriendo).
        # "needed" (R3-B2): alguien está MIRANDO el espectro (ventana
        # visible + página Home en primer plano). Van separados a propósito:
        # con audio activo y configuración abierta, la FFT a 30 Hz era trabajo
        # y allocations puras — nadie ve el canvas de Home desde otra página
        # ni con la app minimizada a bandeja.
        self.active = threading.Event()
        self.needed = threading.Event()
        self.needed.set()  # sin cablear: comportamiento clásico (siempre activo)

    def set_active(self, active: bool) -> None:
        self.active.set() if active else self.active.clear()

    def set_needed(self, needed: bool) -> None:
        self.needed.set() if needed else self.needed.clear()

    def run(self) -> None:
        while not self.isInterruptionRequested():
            if self.active.is_set() and self.needed.is_set() and self.enhancer.spectrum_enabled:
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
                self.msleep(33)  # ~30 Hz: cadencia de refresco visual
            else:
                # Inactivo (sin audio / sin espectro visible): sondeo perezoso
                # del flag a 4 Hz, sin FFT ni allocations. La reactivación al
                # volver a Home sigue siendo imperceptible (<250 ms).
                self.msleep(250)

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
            saved_cfg = load_config() or {}
            saved_lang = saved_cfg.get("language")
            if saved_lang in ("es", "en"):
                self.language = saved_lang
            saved_theme = saved_cfg.get("theme")
            if saved_theme in ("dark", "light"):
                Theme.set_mode(saved_theme)
        self.enhancer = Enhancer()
        # (R3-C1) El ciclo de vida del audio vive en AudioController: la
        # ventana solo reacciona a sus señales para pintar la interfaz.
        self.controller = AudioController(self.enhancer)
        self.controller.started.connect(self._on_audio_started)
        self.controller.output_ready.connect(self._on_output_ready)
        self.controller.start_failed.connect(self._on_start_failed)
        self.controller.stopped.connect(self._on_audio_stopped)
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
        # Preferencias de comportamiento (página Config): antes los tres
        # checkboxes eran decorativos — se pintaban y no afectaban a nada (C1).
        self.minimize_to_tray = True
        self.autostart_audio = True
        self.notifications_enabled = True
        self._closing = False
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

    # ---------- puentes de compatibilidad hacia el controlador ----------

    @property
    def engine(self):
        """Motor de audio (dueño: AudioController)."""
        return self.controller.engine

    @property
    def running(self) -> bool:
        return self.controller.running

    @property
    def go(self) -> bool:
        return self.controller.go

    @go.setter
    def go(self, value: bool) -> None:
        self.controller.go = bool(value)

    def _t(self, text):
        return translate(text, self.language)

    def _build_shell(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._sidebar = Sidebar(self._t)
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
        self._create_pages()
        for page in self._pages.values():
            self._stack.addWidget(page)
        right_layout.addWidget(self._stack, 1)

        self._status_bar = AppStatusBar(self._t)
        sep3 = QFrame()
        sep3.setFixedHeight(1)
        sep3.setStyleSheet(f"background: {Theme.BORDER};")
        right_layout.addWidget(sep3)
        right_layout.addWidget(self._status_bar)

        root_layout.addWidget(right, 1)
        self.metrics.mark("shell")

    def _create_pages(self) -> None:
        """Crea las 6 paginas con el traductor actual."""
        self._pages["home"] = HomePage(self.state, self._t)
        self._pages["equalizer"] = EqualizerPage(self.state, self._t)
        self._pages["effects"] = EffectsPage(self.state, self._t)
        self._pages["audio"] = AudioPage(self.state, self._t)
        self._pages["presets"] = PresetsPage(self.state, self._t)
        self._pages["settings"] = SettingsPage(self.state, self._t)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(QIcon(resource_path("app.ico")), self)
        self._rebuild_tray_menu()
        self.tray.setToolTip(WINDOW_TITLE)
        self.tray.activated.connect(self._on_tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _rebuild_tray_menu(self) -> None:
        """(Re)crea el menu de bandeja con los textos del idioma actual."""
        menu = QMenu()
        show_act = menu.addAction(self._t("Mostrar / Ocultar"))
        show_act.triggered.connect(self._toggle_show)
        audio_act = menu.addAction(self._t("Iniciar / Detener"))
        audio_act.triggered.connect(self.toggle_audio)
        menu.addSeparator()
        quit_act = menu.addAction(self._t("Salir"))
        quit_act.triggered.connect(self._quit_from_tray)
        self.tray.setContextMenu(menu)

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
        self._update_spectrum_needed()
        logger.info("New UI ready: %s", self.metrics.summary())

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
        self._pages["settings"]._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self._pages["settings"]._lang_combo.currentTextChanged.connect(self._on_language_changed)
        # (C1) los tres checkboxes de comportamiento ya SÍ gobiernan la app.
        self._pages["settings"]._tray_check.toggled.connect(self._on_tray_pref_changed)
        self._pages["settings"]._autostart_audio_check.toggled.connect(self._on_autostart_audio_changed)
        self._pages["settings"]._notifications_check.toggled.connect(self._on_notifications_changed)
        self._refresh_preset_list()

    def _on_language_changed(self, text: str) -> None:
        """Aplica el idioma EN CALIENTE recargando toda la interfaz."""
        code = "en" if text.strip().lower().startswith("engl") else "es"
        self._apply_language(code)

    # ---------- preferencias de comportamiento (C1) ----------

    def _on_tray_pref_changed(self, on: bool) -> None:
        self.minimize_to_tray = bool(on)
        self._save_config()

    def _on_autostart_audio_changed(self, on: bool) -> None:
        self.autostart_audio = bool(on)
        self._save_config()

    def _on_notifications_changed(self, on: bool) -> None:
        self.notifications_enabled = bool(on)
        self._save_config()

    def _sync_behavior_checks(self) -> None:
        """Refleja las preferencias de comportamiento en la página Config.

        Se llama tras aplicar config y tras reconstruir páginas: los widgets
        nuevos nacen con los defaults de la clase, no con los del usuario."""
        settings = self._pages.get("settings")
        if settings is None:
            return
        for attr, value in (
            ("_tray_check", self.minimize_to_tray),
            ("_autostart_audio_check", self.autostart_audio),
            ("_notifications_check", self.notifications_enabled),
        ):
            widget = getattr(settings, attr, None)
            if widget is None:
                continue
            widget.blockSignals(True)
            widget.setChecked(value)
            widget.blockSignals(False)

    def _notify_tray(self, body: str) -> None:
        """Notificación de bandeja respetando la preferencia del usuario."""
        if self.tray is None or not self.notifications_enabled:
            return
        with contextlib.suppress(Exception):
            self.tray.showMessage(
                WINDOW_TITLE,
                body,
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )

    def _apply_language(self, code: str) -> None:
        """Aplica el idioma EN CALIENTE reconstruyendo solo las paginas.

        El motor, PyAudio y los workers no se tocan: tocar nativo con audio
        activo provoca access violations. Las paginas se recrean y el chrome
        (sidebar, tray, barra de estado) se retraduce en su sitio."""
        if code == self.language:
            return
        self.language = code
        self._save_config()
        self._rebuild_pages()
        self._sidebar.retranslate(self._t)
        self._rebuild_tray_menu()
        self._status_bar.retranslate(self._t)

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

    def _on_theme_changed(self, index: int) -> None:
        """Cambia dark/white por indice reconstruyendo SOLO las paginas.

        Los estilos inline hornean colores al construirse, asi que las paginas
        deben recrearse; pero el motor, PyAudio y los workers no se tocan:
        recargarlos provoco access violations con audio activo."""
        mode = "light" if index == 1 else "dark"
        if mode == Theme.mode:
            return
        Theme.set_mode(mode)
        self._save_config()
        self.setStyleSheet(Theme.stylesheet())
        self._rebuild_pages()
        # (B4) aquí había un SEGUNDO bloque idéntico aplicado tras el guard:
        # código muerto desde la fusión gml5-v150, nunca llegaba a ejecutar.

    def _rebuild_pages(self) -> None:
        """Recrea las 6 paginas preservando motor, audio y seleccion."""
        current_page = next(
            (pid for pid, page in self._pages.items() if self._stack.currentWidget() is page),
            "home",
        )
        audio_old = self._pages.get("audio")
        cur_src = audio_old._input_combo.currentText() if audio_old else ""
        cur_out = audio_old._output_combo.currentText() if audio_old else ""
        cur_preset = self._pages["home"]._preset_combo.currentText()
        for page in self._pages.values():
            self._stack.removeWidget(page)
            page.deleteLater()
        self._pages = {}
        self._create_pages()
        for page in self._pages.values():
            self._stack.addWidget(page)
        audio_page = self._pages["audio"]
        if self.loopbacks:
            audio_page.set_loopbacks([d["name"] for d in self.loopbacks])
        if self.speakers:
            audio_page.set_speakers([d["name"] for d in self.speakers])
        if cur_src:
            audio_page.set_input(cur_src)
        if cur_out:
            audio_page.set_output(cur_out)
        self._wire_pages()
        # Repoblar el combo de preset sin disparar _on_preset_selected
        # (machacaria los valores manuales del usuario).
        home = self._pages["home"]
        home._preset_combo.blockSignals(True)
        self._refresh_preset_list(cur_preset or None)
        home._preset_combo.blockSignals(False)
        self._sync_ui_from_state()
        self._route_guard()
        home._set_running(self.running)
        settings = self._pages["settings"]
        settings._autostart_check.blockSignals(True)
        settings._autostart_check.setChecked(_autostart_enabled())
        settings._autostart_check.blockSignals(False)
        settings._lang_combo.blockSignals(True)
        settings._lang_combo.setCurrentText("English" if self.language == "en" else "Espanol")
        settings._lang_combo.blockSignals(False)
        settings._theme_combo.blockSignals(True)
        settings._theme_combo.setCurrentIndex(1 if Theme.mode == "light" else 0)
        settings._theme_combo.blockSignals(False)
        self._sync_behavior_checks()
        self._navigate_to(current_page)
        self._update_spectrum_needed()

    def _update_spectrum_needed(self) -> None:
        """(R3-B2) FFT del spectrum solo cuando alguien la mira.

        Visible = ventana al frente Y página Home en primer plano Y app no
        cerrándose. Se llama en cada cambio de visibilidad o de página."""
        home = self._pages.get("home")
        needed = not self._closing and self.isVisible() and home is not None and self._stack.currentWidget() is home
        self._spectrum_worker.set_needed(needed)

    def _navigate_to(self, page_id: str) -> None:
        page = self._pages.get(page_id)
        if not page:
            return
        if self._stack.currentWidget() is page:
            return
        self._stack.setCurrentWidget(page)
        self._update_spectrum_needed()
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
        audio_page = self._pages.get("audio")
        if audio_page is None:
            return
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

    @Slot(object, object, str)
    def _on_devices_ready(self, loopbacks, speakers, error) -> None:
        if "audio" not in self._pages:
            # Llegada tardia: la interfaz se recargo mientras se descubria.
            return
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
            # original) SOLO si el usuario no lo desactivó en Config (C1).
            if self.autostart_audio and self.go and not self.running:
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

    def _route_guard(self, *_args) -> None:
        audio_page = self._pages["audio"]
        src_name = audio_page._input_combo.currentText() or ""
        out_name = audio_page._output_combo.currentText() or ""
        # (R3-C1) La VALIDACIÓN vive en AudioController.evaluate_route (pura,
        # testeable sin Qt); aquí solo se traduce la clave y se colorea.
        go, key = AudioController.evaluate_route(src_name, out_name)
        self.controller.go = go
        if key == "ROUTE_MISSING":
            text, color = self._t("Selecciona una fuente y una salida."), WARN
        elif key == "ROUTE_ECO":
            text, color = self._t("ECO: capturas y reproduces el mismo dispositivo."), DANGER
        elif key == "ROUTE_VIRTUAL_OUT":
            text, color = self._t("La salida es virtual. Usa salida fisica."), DANGER
        elif key == "ROUTE_OK_VIRTUAL":
            # _t(): las claves ya estaban en el diccionario pero no se
            # traducían (B2) — en inglés se veía el texto español.
            text, color = self._t("Ruteo correcto: cable virtual -> salida fisica."), OK
        else:
            text, color = self._t("Info: capturas un parlante fisico."), Theme.TEXT_DIM
        audio_page.set_route_warning(text, color)

    def _all_presets(self):
        presets = dict(PRESETS)
        presets.update(self.custom_presets)
        return presets

    def _refresh_preset_list(self, keep=None) -> None:
        home = self._pages["home"]
        all_presets = self._all_presets()  # (D2) una sola construcción del dict
        current = keep or home._preset_combo.currentText()
        home.set_preset_items(list(all_presets))
        if current in all_presets:
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

    @staticmethod
    def _cfg_float(value, default: float) -> float:
        """float con red de seguridad (C2): config editado a mano no tumba el arranque."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _cfg_bool(value, default: bool) -> bool:
        """bool estricto (C2): solo JSON true/false; cualquier otra cosa -> default."""
        return value if isinstance(value, bool) else default

    def _sanitize_preset_value(self, value):
        """Valida (vol, bass, treble, gains) con coerción segura; None si inválido.

        Un preset con ganancias de otra longitud desalinearía _c_eq del DSP
        ( broadcasting roto dentro del callback); mejor fuera en la puerta."""
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 4
            or not isinstance(value[3], (list, tuple))
            or len(value[3]) != len(self.enhancer.eq_gains)
        ):
            return None
        try:
            return (float(value[0]), float(value[1]), float(value[2]), [float(g) for g in value[3]])
        except (TypeError, ValueError):
            return None

    def _on_preset_selected(self, name: str) -> None:
        raw = self._all_presets().get(name)
        preset = self._sanitize_preset_value(raw) if raw is not None else None
        if preset is None:
            return
        vol, bass, treble, gains = preset
        # Cambio EN BLOQUE via instantánea inmutable: el DSP ve un conjunto
        # coherente de parámetros, nunca una mezcla a medias (H5/H6).
        self.enhancer.apply_params(
            EnhancerParams(
                volume=vol,
                bass=bass,
                treble=treble,
                eq_gains=tuple(gains),
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
        audio_page = self._pages.get("audio")
        if audio_page is None:
            return  # recarga de interfaz en curso
        src_text = audio_page._input_combo.currentText()
        out_text = audio_page._output_combo.currentText()
        source = next((d for d in self.loopbacks if d["name"] == src_text), None)
        output = next((d for d in self.speakers if d["name"] == out_text), None)
        if not self.go or source is None or output is None:
            self._route_guard()
            self._status_bar.set_status_text(self._t("Revisa el ruteo."), DANGER)
            return
        try:
            # (R3-C1) Negociación de tasa, reset de estado DSP y prefill viven
            # en el controlador; esta ventana solo pinta su señal 'started'.
            self.controller.start(source, output, drift_target_ms=self.state.latency_pref)
        except Exception as exc:
            logger.exception("Failed to start")
            self._status_bar.set_status_text(self._t("No se pudo iniciar: %s") % exc, DANGER)

    # ---------- slots de señales del AudioController ----------

    def _on_audio_started(self, src_name: str, out_name: str, rate: int) -> None:
        """Captura abierta: reflejar el estado ACTIVO en la interfaz."""
        self._spectrum_worker.set_active(True)
        self._active_names = (src_name, out_name)
        self.state.processing = True
        self.state.input_device = src_name
        self.state.output_device = out_name
        self._status_bar.set_processing(True)
        self._status_bar.set_route(src_name, out_name)
        self._status_bar.set_sample_rate(rate)
        self._header_status.setText("ACTIVE")
        self._header_status.setStyleSheet(
            f"color: {Theme.SUCCESS}; font-size: {Theme.FONT_SIZE_MD}px; "
            f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
        )
        self._status_bar.set_status_text(self._t("Activando salida..."), WARN)

    def _on_output_ready(self, latency: float) -> None:
        """Salida abierta tras el prefill: punto de operación estable."""
        self._status_bar.set_latency(latency)
        self._status_bar.set_status_text(self._t("Activo (ring buffer): %s -> %s") % self._active_names, OK)
        self._pages["audio"].set_info(rate=self.enhancer.sample_rate, buffer=1024, latency=latency, status="Processing")
        self._notify_tray(self._t("Activo (ring buffer): %s -> %s") % self._active_names)

    def _on_start_failed(self, message: str) -> None:
        """La salida falló (el controlador ya liberó la captura)."""
        self._spectrum_worker.set_active(False)
        self.state.processing = False
        self._status_bar.set_processing(False)
        self._header_status.setText("")
        self._status_bar.set_status_text(self._t("No se pudo iniciar: %s") % message, DANGER)

    def _on_audio_stopped(self) -> None:
        """Parada limpia solicitada por el usuario."""
        self._spectrum_worker.set_active(False)
        self.state.processing = False
        self._status_bar.set_processing(False)
        self._header_status.setText("")
        self._status_bar.set_status_text(self._t("Procesamiento detenido"), WARN)
        self._pages["audio"].set_info(status="Detenido")
        self._notify_tray(self._t("Procesamiento detenido"))

    def _stop_audio(self) -> None:
        """Parada limpia: el controlador emite 'stopped' y la UI reacciona."""
        self.controller.stop()

    def _receive_spectrum(self, values) -> None:
        self._latest_spectrum = values

    def _refresh_visuals(self) -> None:
        # Medidores honestos: la ENTRADA es RMS (energía percibida del material
        # capturado) y la SALIDA es pico post-DSP (lo que realmente puede
        # acercarse al techo). Antes ambos mostraban el mismo valor.
        self.state.input_level = float(self.enhancer.level_rms)
        self.state.output_level = float(self.enhancer.level_peak)
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
            self._sync_behavior_checks()
            return
        self._keep_src = str(config.get("source", "") or "")
        self._keep_out = str(config.get("output", "") or "")
        # (C2) el config.json es editable a mano: un tipo raro no debe tumbar
        # el arranque ni colar presets malformados al DSP.
        custom = config.get("custom_presets")
        if isinstance(custom, dict):
            self.custom_presets = {}
            for name, value in custom.items():
                preset = self._sanitize_preset_value(value)
                if preset is not None:
                    self.custom_presets[str(name)] = preset
        gains = config.get("eq_gains")
        gains_ok = isinstance(gains, list) and len(gains) == len(self.enhancer.eq_gains)
        if gains_ok:
            try:
                gains_tuple = tuple(float(g) for g in gains)
            except (TypeError, ValueError):
                gains_ok = False
        self.enhancer.apply_params(
            EnhancerParams(
                volume=self._cfg_float(config.get("volume"), 1.0),
                bass=self._cfg_float(config.get("bass"), 0.0),
                treble=self._cfg_float(config.get("treble"), 0.0),
                eq_gains=gains_tuple if gains_ok else tuple(self.enhancer.eq_gains),
                limiter=self._cfg_bool(config.get("limiter"), True),
                compressor=self._cfg_bool(config.get("compressor"), True),
                blend=float(self.enhancer.blend),
            )
        )
        # Preferencia de latencia persistida (40/60/100 ms).
        try:
            lat = int(config.get("latency_pref", 60) or 60)
        except (TypeError, ValueError):
            lat = 60
        self.state.latency_pref = lat if lat in LATENCY_CHOICES_MS else 60
        self._pages["audio"].set_latency_pref(self.state.latency_pref)
        # (C1) preferencias de comportamiento, antes checkboxes decorativos.
        self.minimize_to_tray = self._cfg_bool(config.get("minimize_to_tray"), True)
        self.autostart_audio = self._cfg_bool(config.get("autostart_audio"), True)
        self.notifications_enabled = self._cfg_bool(config.get("notifications"), True)
        self._refresh_preset_list(config.get("preset", DEFAULT_PRESET))
        self._sync_ui_from_state()
        self._sync_behavior_checks()
        settings = self._pages["settings"]
        settings._autostart_check.blockSignals(True)
        settings._autostart_check.setChecked(_autostart_enabled())
        settings._autostart_check.blockSignals(False)
        settings._lang_combo.blockSignals(True)
        settings._lang_combo.setCurrentText("English" if self.language == "en" else "Espanol")
        settings._lang_combo.blockSignals(False)
        settings._theme_combo.blockSignals(True)
        settings._theme_combo.setCurrentIndex(1 if Theme.mode == "light" else 0)
        settings._theme_combo.blockSignals(False)

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
            "theme": Theme.mode,
            "latency_pref": int(self.state.latency_pref),
            "minimize_to_tray": bool(self.minimize_to_tray),
            "autostart_audio": bool(self.autostart_audio),
            "notifications": bool(self.notifications_enabled),
            "custom_presets": {n: list(v) for n, v in self.custom_presets.items()},
        }
        if not save_config(config):
            logger.warning("Failed to save config")

    def _toggle_autostart(self, enabled: bool) -> None:
        # La lógica winreg vive en audio_enhancer.autostart (SRP): antes el
        # comando se escribía corrupto al registro (comillas perdidas).
        if _set_auto_start(enabled):
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
        self._update_spectrum_needed()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._update_spectrum_needed()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._update_spectrum_needed()

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
        self.controller.terminate_pa()
        if self.tray:
            self.tray.hide()

    def closeEvent(self, event) -> None:
        # (C1) el checkbox "Minimizar a bandeja al cerrar" ahora decide:
        # desmarcado, cerrar la ventana sale de verdad.
        if self.tray and self.tray.isVisible() and self.minimize_to_tray:
            self._save_config()
            self.hide()
            self._status_bar.set_status_text(self._t("Procesando en segundo plano."), WARN)
            event.ignore()
        else:
            self._shutdown()
            event.accept()
