from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from typing import Any

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
    QFileDialog,
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
from ...config_manager import ConfigManager
from ...constants import (
    CABLE_KEYWORDS,
    DANGER,
    DEFAULT_PRESET,
    OK,
    VIRTUAL_CABLE_KEYWORDS,
    WARN,
    WINDOW_TITLE,
    resource_path,
)
from ...device_utils import is_bluetooth_name, pick_default_output
from ...dsp import EQ_BANDS, Enhancer, EnhancerParams
from ...engine import _pa
from ...i18n import PRESETS, detect_system_language, explain, translate
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
        # (R3-C2) Esquema/coerción/defaults del config.json en ConfigManager.
        self._config = ConfigManager(eq_band_count=len(EQ_BANDS))
        self.language = detect_system_language()
        # Idioma guardado por el usuario tiene prioridad sobre el del sistema.
        with contextlib.suppress(Exception):
            saved_cfg = self._config.load()
            if saved_cfg["language"]:
                self.language = saved_cfg["language"]
            if saved_cfg["theme"]:
                Theme.set_mode(saved_cfg["theme"])
        self.enhancer = Enhancer()
        # (R3-C1) El ciclo de vida del audio vive en AudioController: la
        # ventana solo reacciona a sus señales para pintar la interfaz.
        self.controller = AudioController(self.enhancer)
        self.controller.started.connect(self._on_audio_started)
        self.controller.output_ready.connect(self._on_output_ready)
        self.controller.start_failed.connect(self._on_start_failed)
        self.controller.stopped.connect(self._on_audio_stopped)
        self.controller.stream_lost.connect(self._on_stream_lost)
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
        state.true_peak_changed.connect(lambda on: setattr(self.enhancer, "true_peak", bool(on)))
        state.safety_ceiling_changed.connect(lambda on: setattr(self.enhancer, "safety_ceiling_enabled", bool(on)))
        state.final_clip_changed.connect(lambda on: setattr(self.enhancer, "final_clip_enabled", bool(on)))

        def _apply_crossfeed(cfg: dict) -> None:
            self.enhancer.crossfeed = bool(cfg["enabled"])
            self.enhancer.crossfeed_preset = str(cfg["preset"])
            self.enhancer.crossfeed_cut_hz = int(cfg["cut_hz"])
            self.enhancer.crossfeed_feed_db = float(cfg["feed_db"])

        state.crossfeed_changed.connect(_apply_crossfeed)
        state.volume_changed.connect(lambda v: setattr(self.enhancer, "volume", float(v)))
        self.custom_presets: dict[str, Any] = {}
        self.loopbacks: list[dict[str, Any]] = []
        self.speakers: list[dict[str, Any]] = []
        # Preferencias de comportamiento (página Config): antes los tres
        # checkboxes eran decorativos — se pintaban y no afectaban a nada (C1).
        self.minimize_to_tray = True
        self.autostart_audio = True
        self.notifications_enabled = True
        self.watchdog_enabled = True
        self._closing = False
        self._active_names = ("", "")
        self._metrics_tick = 0  # refresco de métricas ~1 Hz (timer a 33 ms)
        self._stats_log_tick = 0  # log de diagnóstico ~10 s
        self._last_captured = 0
        self._last_stats: dict[str, int] = {}
        self._keep_src = ""
        self._keep_out = ""
        self._latest_spectrum = None
        self._discovery_thread: Any = None
        self._discovery_worker: Any = None
        self._spectrum_worker = SpectrumWorker(self.enhancer, self)
        self.tray: Any = None
        self._tray_menu: Any = None  # menú de bandeja (Qt no lo posee; ver _rebuild_tray_menu)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(Theme.DEFAULT_WIDTH, Theme.DEFAULT_HEIGHT)
        self._apply_adaptive_min_size()
        self.setWindowIcon(QIcon(resource_path("app.ico")))
        self.setStyleSheet(Theme.stylesheet())
        self._build_shell()
        # La bandeja se crea DIFERIDA (QTimer.singleShot en build_content), ya
        # con el bucle de eventos corriendo. Crear QSystemTrayIcon dentro del
        # __init__ (antes de app.exec) crasheaba de forma intermitente en
        # pyside6.abi3.dll (0xc0000005) cuando coincidían dos arranques: es un
        # problema conocido de QSystemTrayIcon en Windows.

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

    def _explain(self, key):
        """Descripción de un control para tooltips, en el idioma activo."""
        return explain(key, self.language)

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
        self._pages: dict[str, Any] = {}
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
        self._pages["effects"] = EffectsPage(self.state, self._t, self._explain)
        self._pages["audio"] = AudioPage(self.state, self._t)
        self._pages["presets"] = PresetsPage(self.state, self._t)
        self._pages["settings"] = SettingsPage(self.state, self._t)

    def _apply_adaptive_min_size(self) -> None:
        """Tamaño mínimo según la pantalla: 900x600 va justo en 1280x720.

        Se acota al área disponible menos un margen (barras de tareas y
        decoración) sin bajar de un suelo usable. Si no hay pantalla
        disponible (tests offscreen), se usa el mínimo nominal."""
        min_w, min_h = Theme.MIN_WIDTH, Theme.MIN_HEIGHT
        try:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                min_w = min(min_w, max(760, avail.width() - 80))
                min_h = min(min_h, max(500, avail.height() - 80))
        except Exception:
            logger.debug("No se pudo calcular el tamaño mínimo según pantalla", exc_info=True)
        self.setMinimumSize(min_w, min_h)

    def _build_tray(self) -> None:
        """Crea la bandeja. Tolerante a fallos: si Qt falla, la app sigue.

        Se invoca DIFERIDA (ver _create_tray_deferred) para que el
        QSystemTrayIcon nazca con el bucle de eventos ya activo; crearlo antes
        de app.exec() crasheaba intermitentemente en Windows."""
        if self.tray is not None:
            return
        try:
            self.tray = QSystemTrayIcon(QIcon(resource_path("app.ico")), self)
            self._rebuild_tray_menu()
            self.tray.setToolTip(WINDOW_TITLE)
            self.tray.activated.connect(self._on_tray_activated)
            if QSystemTrayIcon.isSystemTrayAvailable():
                self.tray.show()
        except Exception:
            logger.exception("No se pudo crear la bandeja del sistema")
            self.tray = None

    def _create_tray_deferred(self) -> None:
        """Crea la bandeja tras el primer ciclo del bucle de eventos."""
        self._build_tray()

    def _rebuild_tray_menu(self) -> None:
        """(Re)crea el menu de bandeja con los textos del idioma actual.

        IMPORTANTE (crash c0000005 en pyside6.abi3.dll): QSystemTrayIcon NO toma
        ownership del menu (lo dice la doc oficial de Qt). Si el QMenu queda en
        una variable local, Python lo destruye al salir de la función y el icono
        usa un puntero liberado → access violation intermitente. Por eso el menú
        se crea CON parent (self) y se guarda en self._tray_menu."""
        if self.tray is None:
            return  # aún no hay bandeja (creación diferida)
        menu = QMenu(self)  # parent: Qt lo gestiona y no se libera antes de tiempo
        self._tray_menu = menu  # referencia viva extra (defensa en profundidad)
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
        # Bandeja diferida: se crea con el bucle de eventos ya activo (evita el
        # crash intermitente de QSystemTrayIcon al arrancar en Windows).
        QTimer.singleShot(0, self._create_tray_deferred)
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
        # (R3-C3) La ventana SOLO usa las señales y métodos públicos de las
        # páginas: cero accesos a widgets internos ajenos.
        home = self._pages["home"]
        home.start_requested.connect(self.toggle_audio)
        home.preset_selected.connect(self._on_preset_selected)
        home.ab_toggled.connect(self.toggle_ab)
        home.volume_edited.connect(self._on_volume_slider)
        audio_page = self._pages["audio"]
        audio_page.input_selected.connect(self._route_guard)
        audio_page.output_selected.connect(self._route_guard)
        audio_page.refresh_requested.connect(self._start_discovery)
        audio_page.latency_selected.connect(self._on_latency_pref_changed)
        self._pages["presets"].save_requested.connect(self._save_custom_preset)
        self._pages["presets"].delete_requested.connect(self._delete_custom_preset)
        self._pages["presets"].import_requested.connect(self._import_presets)
        self._pages["presets"].export_requested.connect(self._export_presets)
        settings = self._pages["settings"]
        settings.autostart_toggled.connect(self._toggle_autostart)
        settings.theme_selected.connect(self._on_theme_changed)
        settings.language_selected.connect(self._on_language_changed)
        # (C1) los tres checkboxes de comportamiento ya SÍ gobiernan la app.
        settings.tray_pref_changed.connect(self._on_tray_pref_changed)
        settings.autostart_audio_pref_changed.connect(self._on_autostart_audio_changed)
        settings.notifications_pref_changed.connect(self._on_notifications_changed)
        settings.watchdog_pref_changed.connect(self._on_watchdog_changed)
        settings.diagnostics_requested.connect(self._export_diagnostics)
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

    def _on_watchdog_changed(self, on: bool) -> None:
        """Activa/desactiva el watchdog en caliente (sin reiniciar el audio)."""
        self.watchdog_enabled = bool(on)
        self.controller.set_watchdog_enabled(self.watchdog_enabled)
        self._save_config()

    def _export_diagnostics(self) -> None:
        """Escribe un informe (versión, SO, config, dispositivos, métricas y
        cola del log) para soporte. No toca audio."""
        import platform

        from ...constants import APP_VERSION
        from ...single_instance import LOG_FILE

        so = f"{platform.system()} {platform.release()} ({platform.machine()})"
        fx = f"volume={self.enhancer.volume} bass={self.enhancer.bass} treble={self.enhancer.treble}"
        sw = (
            f"limiter={self.enhancer.limiter} compressor={self.enhancer.compressor} true_peak={self.enhancer.true_peak}"
        )
        lines = [
            f"Audio Enhancer FxStyle {APP_VERSION}",
            f"Python {platform.python_version()} | {so}",
            "",
            "== Config ==",
            f"source={self._keep_src!r} output={self._keep_out!r}",
            fx,
            sw,
            f"latency_pref={self.state.latency_pref} theme={Theme.mode} language={self.language}",
            f"eq_gains={[round(float(g), 2) for g in self.enhancer.eq_gains]}",
            "",
            "== Dispositivos (loopbacks) ==",
        ]
        lines += [f"- {d.get('name')} @ {d.get('defaultSampleRate')} Hz" for d in self.loopbacks]
        lines.append("== Dispositivos (salidas) ==")
        lines += [f"- {d.get('name')} @ {d.get('defaultSampleRate')} Hz" for d in self.speakers]
        lines += ["", "== Motor ==", f"running={self.running}"]
        if self.running:
            lines.append("stats=" + str(self.engine.stats_snapshot()))
        lines += ["", "== Log (cola) =="]
        try:
            with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
                lines += [ln.rstrip(chr(10)) for ln in f.readlines()[-120:]]
        except Exception:
            lines.append("(no se pudo leer el log)")
        dest = os.path.join(os.path.dirname(LOG_FILE), "diagnostico.txt")
        try:
            with open(dest, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            logger.exception("No se pudo exportar el diagnostico")
            self._status_bar.set_status_text(self._t("No se pudo guardar el diagnóstico."), DANGER)
            return
        self._status_bar.set_status_text(self._t("Diagnóstico guardado en %s") % dest, OK)

    def _sync_behavior_checks(self) -> None:
        """Refleja las preferencias de comportamiento en la página Config.

        Se llama tras aplicar config y tras reconstruir páginas: los widgets
        nuevos nacen con los defaults de la clase, no con los del usuario."""
        settings = self._pages.get("settings")
        if settings is None:
            return
        settings.set_behavior(
            self.minimize_to_tray, self.autostart_audio, self.notifications_enabled, self.watchdog_enabled
        )

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

    def _on_latency_pref_changed(self, ms: int) -> None:
        """Cambia la latencia objetivo (40/60/100 ms).

        ``ms`` es el VALOR (la señal latency_selected emite los ms, no el
        índice). Antes el handler lo trataba como índice: ``itemData(100)``
        devolvía None y caía a 60, así que la latencia NUNCA cambiaba.
        Con el audio activo se aplica en caliente: solo se mueve la consigna de
        llenado del ring y el control de deriva converge en ~1 s (sin glitch)."""
        ms = int(ms or 60)
        self.state.latency_pref = ms
        self._save_config()
        if self.running:
            latency = self.controller.set_latency(ms)
            self._status_bar.set_latency(latency)
            self._pages["audio"].set_info(
                rate=self.enhancer.sample_rate, buffer=1024, latency=latency, status="Processing"
            )
            self._status_bar.set_status_text(self._t("Latencia objetivo: %d ms") % ms, OK)
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
        cur_src = audio_old.selected_source() if audio_old else ""
        cur_out = audio_old.selected_output() if audio_old else ""
        cur_preset = self._pages["home"].preset_text()
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
        home.set_preset_items(list(self._all_presets()))  # repoblar sin disparar _on_preset_selected
        if cur_preset:
            home.set_preset(cur_preset)
        self._sync_ui_from_state()
        self._route_guard()
        home.set_running(self.running)
        settings = self._pages["settings"]
        settings.set_autostart_checked(_autostart_enabled())
        settings.set_language_text("English" if self.language == "en" else "Espanol")
        settings.set_theme_index(1 if Theme.mode == "light" else 0)
        self._sync_behavior_checks()
        self._navigate_to(current_page)
        self._update_spectrum_needed()

    def _update_spectrum_needed(self) -> None:
        """(R3-B2) FFT del spectrum solo cuando alguien la mira.

        Visible = ventana al frente Y una pagina que DIBUJA espectro en primer
        plano (Inicio y Ecualizador, ambas pintan las barras) Y app no
        cerrándose. Se llama en cada cambio de visibilidad o de pagina."""
        current = self._stack.currentWidget()
        watched = {p for p in (self._pages.get("home"), self._pages.get("equalizer")) if p is not None}
        needed = not self._closing and self.isVisible() and current in watched
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
        if audio_page.has_source_items():
            self._keep_src = audio_page.selected_source()
        if audio_page.has_output_items():
            self._keep_out = audio_page.selected_output()
        audio_page.set_devices_enabled(False)
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
        audio_page.set_devices_enabled(True)
        if error:
            self._status_bar.set_status_text(self._t("No se pudieron detectar dispositivos: %s") % error, DANGER)
        else:
            self._auto_select()
            self._route_guard()
            self._status_bar.set_status_text(self._t("Dispositivos listos."), OK)
            self._maybe_warn_bluetooth()
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
            audio_page.set_input(self._keep_src)
        if self._keep_out in out_names:
            audio_page.set_output(self._keep_out)
        self._keep_src = ""
        self._keep_out = ""

    def _clear_discovery_refs(self) -> None:
        self._discovery_thread = None
        self._discovery_worker = None

    def _auto_select(self) -> None:
        audio_page = self._pages["audio"]
        if self.loopbacks and not audio_page.selected_source():
            idx = 0
            for i, d in enumerate(self.loopbacks):
                if any(k in d["name"].lower() for k in CABLE_KEYWORDS):
                    idx = i
                    break
            audio_page.select_source_index(idx)
        if self.speakers and not audio_page.selected_output():
            # Elige la salida más probable como principal (parlantes/auriculares)
            # en vez de ciegamente la primera (podía ser HDMI/SPDIF).
            audio_page.select_output_index(pick_default_output([d["name"] for d in self.speakers]))

    def _maybe_warn_bluetooth(self) -> None:
        """Aviso si la salida elegida es Bluetooth: su reloj es inestable y
        añade latencia; 100 ms suele ser más estable que 60 ms."""
        out = self._pages["audio"].selected_output() or ""
        if is_bluetooth_name(out) and self.state.latency_pref < 100:
            self._status_bar.set_status_text(self._t("Salida Bluetooth: usa 100 ms si oyes cortes."), WARN)

    def _route_guard(self, *_args) -> None:
        audio_page = self._pages["audio"]
        src_name = audio_page.selected_source() or ""
        out_name = audio_page.selected_output() or ""
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
        current = keep or home.preset_text()
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

    def _on_preset_selected(self, name: str) -> None:
        raw = self._all_presets().get(name)
        # (R3-C2) La validación de presets vive en ConfigManager.
        preset = self._config.sanitize_preset(raw) if raw is not None else None
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
                true_peak=bool(self.enhancer.true_peak),
                blend=float(self.enhancer.blend),
            )
        )
        self._sync_ui_from_state()
        self.state.preset_name = name

    def _save_custom_preset(self) -> None:
        presets_page = self._pages["presets"]
        name = presets_page.name_text()
        if not name:
            self._status_bar.set_status_text(self._t("Escribe un nombre para el preset."), WARN)
            return
        self.custom_presets[name] = (
            float(self.enhancer.volume),
            float(self.enhancer.bass),
            float(self.enhancer.treble),
            [float(g) for g in self.enhancer.eq_gains],
        )
        presets_page.clear_name_entry()
        self._refresh_preset_list(name)
        self._save_config()

    def _export_presets(self) -> None:
        """Guarda los presets personalizados en un JSON portable."""
        if not self.custom_presets:
            self._status_bar.set_status_text(self._t("No hay presets personalizados para exportar."), WARN)
            return
        path, _ = QFileDialog.getSaveFileName(self, self._t("Exportar presets"), "presets.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({n: list(v) for n, v in self.custom_presets.items()}, f, ensure_ascii=False, indent=2)
        except OSError:
            logger.exception("No se pudieron exportar los presets")
            self._status_bar.set_status_text(self._t("No se pudieron exportar los presets."), DANGER)
            return
        self._status_bar.set_status_text(self._t("Presets exportados: %s") % path, OK)

    def _import_presets(self) -> None:
        """Carga presets desde un JSON externo, los sanea y los añade."""
        path, _ = QFileDialog.getOpenFileName(self, self._t("Importar presets"), "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            logger.exception("No se pudieron importar los presets")
            self._status_bar.set_status_text(self._t("No se pudieron importar los presets."), DANGER)
            return
        # (R3-C2) El saneo vive en ConfigManager: un JSON editado a mano no
        # puede colar ganancias de longitud incorrecta ni valores fuera de rango.
        imported = self._config.sanitize_presets(raw)
        if not imported:
            self._status_bar.set_status_text(self._t("El archivo no contiene presets válidos."), WARN)
            return
        self.custom_presets.update(imported)
        self._refresh_preset_list()
        self._save_config()
        self._status_bar.set_status_text(self._t("Presets importados: %d") % len(imported), OK)

    def _sync_ui_from_state(self) -> None:
        home = self._pages["home"]
        home.set_volume(self.enhancer.volume)
        self._pages["equalizer"].set_gains(self.enhancer.eq_gains)
        self._pages["effects"].set_bass(self.enhancer.bass)
        self._pages["effects"].set_treble(self.enhancer.treble)
        self._pages["effects"].set_limiter(self.enhancer.limiter)
        self._pages["effects"].set_compressor(self.enhancer.compressor)
        self._pages["effects"].set_true_peak(self.enhancer.true_peak)
        self._pages["effects"].set_safety_ceiling(self.enhancer.safety_ceiling_enabled)
        self._pages["effects"].set_final_clip(self.enhancer.final_clip_enabled)
        self._pages["effects"].set_crossfeed(
            self.enhancer.crossfeed,
            self.enhancer.crossfeed_preset,
            self.enhancer.crossfeed_cut_hz,
            self.enhancer.crossfeed_feed_db,
        )
        home.set_ab(self.enhancer.blend > 0.5)
        # Consistencia: AudioState alineado con el DSP (los set_* de las
        # paginas usan blockSignals y no escriben en el estado).
        self.state.sync_from_enhancer(self.enhancer)

    def _on_volume_slider(self, raw: int) -> None:
        # El label del slider es responsabilidad de HomePage (_on_volume_moved,
        # que emite volume_edited): tocar el privado aquí era redundante y
        # rompía el contrato R3-C3.
        self.enhancer.volume = raw / 100.0

    def toggle_ab(self) -> None:
        self.enhancer.blend = 0.0 if self.enhancer.blend > 0.5 else 1.0
        enabled = self.enhancer.blend > 0.5
        self._pages["home"].set_ab(enabled)
        self.state.ab_enabled = enabled

    def toggle_audio(self) -> None:
        if self.running:
            self._stop_audio()
            return
        # Guarda de idempotencia: si el controlador ya tiene una captura en
        # marcha (p. ej. auto-arranque + clic casi simultáneos), NO se abre una
        # segunda: dos callbacks de captura inflaban `capturados` y desbordaban
        # el ring (saltos/cortes de audio).
        if self.controller.running:
            return
        audio_page = self._pages.get("audio")
        if audio_page is None:
            return  # recarga de interfaz en curso
        src_text = audio_page.selected_source()
        out_text = audio_page.selected_output()
        source = next((d for d in self.loopbacks if d["name"] == src_text), None)
        output = next((d for d in self.speakers if d["name"] == out_text), None)
        if not self.go or source is None or output is None:
            self._route_guard()
            self._status_bar.set_status_text(self._t("Revisa el ruteo."), DANGER)
            return
        # (C5) Bloquear el botón hasta que la salida esté lista (prefill): un
        # segundo clic durante ese instante detenía el audio recién iniciado.
        self._pages["home"].set_start_enabled(False)
        try:
            # (R3-C1) Negociación de tasa, reset de estado DSP y prefill viven
            # en el controlador; esta ventana solo pinta su señal 'started'.
            self.controller.start(source, output, drift_target_ms=self.state.latency_pref)
        except Exception as exc:
            logger.exception("Failed to start")
            self._pages["home"].set_start_enabled(True)
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
        self._pages["home"].set_start_enabled(True)  # fin del prefill
        self._status_bar.set_latency(latency)
        self._status_bar.set_status_text(self._t("Activo (ring buffer): %s -> %s") % self._active_names, OK)
        self._pages["audio"].set_info(rate=self.enhancer.sample_rate, buffer=1024, latency=latency, status="Processing")
        self._notify_tray(self._t("Activo (ring buffer): %s -> %s") % self._active_names)

    def _on_start_failed(self, message: str) -> None:
        """La salida falló (el controlador ya liberó la captura)."""
        self._pages["home"].set_start_enabled(True)
        self._spectrum_worker.set_active(False)
        self.state.processing = False
        self._status_bar.set_processing(False)
        self._header_status.setText("")
        self._status_bar.set_status_text(self._t("No se pudo iniciar: %s") % message, DANGER)

    def _on_audio_stopped(self) -> None:
        """Parada limpia solicitada por el usuario."""
        self._pages["home"].set_start_enabled(True)
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

    def _on_stream_lost(self, reason: str) -> None:
        """Un dispositivo se desconectó o el driver falló en caliente.

        El controlador ya detuvo el audio (emitió 'stopped' antes). Aquí se
        avisa al usuario y se refresca la lista de dispositivos para que el
        ruteo apunte a uno válido."""
        self._status_bar.set_status_text(
            self._t("Se perdió el dispositivo de %s. Revisa el ruteo.") % self._t(reason), DANGER
        )
        self._notify_tray(self._t("Se perdió el dispositivo de %s.") % self._t(reason))
        self._start_discovery()

    def _receive_spectrum(self, values) -> None:
        self._latest_spectrum = values

    def _refresh_visuals(self) -> None:
        # Medidores honestos: la ENTRADA es RMS (energía percibida del material
        # capturado) y la SALIDA es pico post-DSP (lo que realmente puede
        # acercarse al techo). Antes ambos mostraban el mismo valor.
        self.state.input_level = float(self.enhancer.level_rms)
        self.state.output_level = float(self.enhancer.level_peak)
        # Niveles por canal para los medidores estéreo (L/R).
        self.state.input_levels = (self.enhancer.level_rms_l, self.enhancer.level_rms_r)
        self.state.output_levels = (self.enhancer.level_peak_l, self.enhancer.level_peak_r)
        self.state.output_gr = float(self.enhancer.level_gr)
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
        # Diagnóstico de cortes: cada ~10 s (300 ticks de 33 ms) se registra el
        # avance de la captura y los contadores. Si `capturados` no avanza, el
        # loopback (p. ej. el cable virtual) dejó de entregar audio; si suben
        # `huecos`/`unders`, el ring se quedó sin datos (corte audible).
        if self.running:
            self._stats_log_tick += 1
            if self._stats_log_tick >= 300:
                self._stats_log_tick = 0
                s = self.engine.stats_snapshot()
                prev = self._last_stats

                def delta(key: str) -> int:
                    return int(s[key]) - int(prev.get(key, s[key]))

                logger.info(
                    "metricas(10s): unders=%d huecos=%d frames_hueco=%d deriva=%d "
                    "descartes=%d overflows=%d capturados=%d fill=%d/%d",
                    delta("output_underruns"),
                    delta("gap_blocks"),
                    delta("gap_frames"),
                    delta("drift_adjust_frames"),
                    delta("dropped_frames"),
                    delta("input_overflows"),
                    delta("captured_frames"),
                    self.engine.fill(),
                    self.engine.drift_target,
                )
                self._last_stats = dict(s)
        else:
            self._stats_log_tick = 0
            self._last_captured = 0
            self._last_stats = {}

    def _apply_config(self) -> None:
        # (R3-C2) ConfigManager devuelve SIEMPRE el diccionario completo y
        # saneado: coerción, defaults y validación de presets viven allí.
        cfg = self._config.load()
        self._keep_src = cfg["source"]
        self._keep_out = cfg["output"]
        self.custom_presets = dict(cfg["custom_presets"])
        self.enhancer.apply_params(
            EnhancerParams(
                volume=cfg["volume"],
                bass=cfg["bass"],
                treble=cfg["treble"],
                eq_gains=tuple(cfg["eq_gains"]) if cfg["eq_gains"] is not None else tuple(self.enhancer.eq_gains),
                limiter=cfg["limiter"],
                compressor=cfg["compressor"],
                true_peak=cfg["true_peak"],
                blend=float(self.enhancer.blend),
            )
        )
        # Techo de seguridad y watchdog: se aplican directo (no van en
        # EnhancerParams ni en el estado de audio).
        self.enhancer.safety_ceiling_enabled = bool(cfg["safety_ceiling"])
        self.enhancer.final_clip_enabled = bool(cfg["final_clip"])
        self.enhancer.crossfeed = bool(cfg["crossfeed"])
        self.enhancer.crossfeed_preset = str(cfg["crossfeed_preset"])
        self.enhancer.crossfeed_cut_hz = int(cfg["crossfeed_cut_hz"])
        self.enhancer.crossfeed_feed_db = float(cfg["crossfeed_feed_db"])
        self.watchdog_enabled = bool(cfg["watchdog"])
        self.controller.set_watchdog_enabled(self.watchdog_enabled)
        # Preferencia de latencia persistida (40/60/100 ms), ya validada.
        self.state.latency_pref = cfg["latency_pref"]
        self._pages["audio"].set_latency_pref(self.state.latency_pref)
        # (C1) preferencias de comportamiento, antes checkboxes decorativos.
        self.minimize_to_tray = cfg["minimize_to_tray"]
        self.autostart_audio = cfg["autostart_audio"]
        self.notifications_enabled = cfg["notifications"]
        self._refresh_preset_list(cfg["preset"])
        self._sync_ui_from_state()
        self._sync_behavior_checks()
        settings = self._pages["settings"]
        settings.set_autostart_checked(_autostart_enabled())
        settings.set_language_text("English" if self.language == "en" else "Espanol")
        settings.set_theme_index(1 if Theme.mode == "light" else 0)

    def _save_config(self) -> None:
        audio_page = self._pages["audio"]
        home = self._pages["home"]
        config = {
            "source": audio_page.selected_source(),
            "output": audio_page.selected_output(),
            "preset": home.preset_text() or DEFAULT_PRESET,
            "language": self.language,
            "volume": float(self.enhancer.volume),
            "bass": float(self.enhancer.bass),
            "treble": float(self.enhancer.treble),
            "eq_gains": [float(g) for g in self.enhancer.eq_gains],
            "limiter": bool(self.enhancer.limiter),
            "compressor": bool(self.enhancer.compressor),
            "true_peak": bool(self.enhancer.true_peak),
            "safety_ceiling": bool(self.enhancer.safety_ceiling_enabled),
            "final_clip": bool(self.enhancer.final_clip_enabled),
            "crossfeed": bool(self.enhancer.crossfeed),
            "crossfeed_preset": str(self.enhancer.crossfeed_preset),
            "crossfeed_cut_hz": int(self.enhancer.crossfeed_cut_hz),
            "crossfeed_feed_db": float(self.enhancer.crossfeed_feed_db),
            "theme": Theme.mode,
            "latency_pref": int(self.state.latency_pref),
            "minimize_to_tray": bool(self.minimize_to_tray),
            "autostart_audio": bool(self.autostart_audio),
            "notifications": bool(self.notifications_enabled),
            "watchdog": bool(self.watchdog_enabled),
            "custom_presets": {n: list(v) for n, v in self.custom_presets.items()},
        }
        if not self._config.save(config):
            logger.warning("Failed to save config")

    def _toggle_autostart(self, enabled: bool) -> None:
        # La lógica winreg vive en audio_enhancer.autostart (SRP): antes el
        # comando se escribía corrupto al registro (comillas perdidas).
        if _set_auto_start(enabled):
            self._status_bar.set_status_text(self._t("Inicio con Windows: activado"), OK)
        else:
            self._status_bar.set_status_text(self._t("Inicio con Windows: fallo"), DANGER)
            # Revertir el checkbox por su API pública (R3-C3).
            self._pages["settings"].set_autostart_checked(not enabled)

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
