"""Ventana experimental PySide6 conectada al motor de audio existente."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import threading
import time

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..config import load_config, save_config
from ..constants import (
    CABLE_KEYWORDS,
    CONFIG_PATH,
    DANGER,
    DEFAULT_PRESET,
    OK,
    VIRTUAL_CABLE_KEYWORDS,
    WARN,
    WINDOW_TITLE,
    resource_path,
)
from ..dsp import Enhancer
from ..engine import AudioEngine, _pa
from ..i18n import PRESETS, detect_system_language, translate
from ..startup_metrics import StartupMetrics
from .qt_widgets import CardFrame, SpectrumWidget

logger = logging.getLogger("audio_enhancer.qt")


class DeviceDiscoveryWorker(QObject):
    """Enumera WASAPI fuera del hilo de Qt que pinta la ventana."""

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
                    if "fxsound" in name or any(key in name for key in VIRTUAL_CABLE_KEYWORDS):
                        continue
                    speakers.append(device)
            self.finished.emit(pa, loopbacks, speakers, "")
        except Exception as exc:
            logger.exception("Fallo al descubrir dispositivos en Qt")
            self.finished.emit(pa, [], [], str(exc))


class SpectrumWorker(QThread):
    """Calcula el spectrum existente fuera del hilo principal de Qt."""

    spectrum_ready = Signal(object)

    def __init__(self, enhancer: Enhancer, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.enhancer = enhancer
        self.active = threading.Event()

    def set_active(self, active: bool) -> None:
        if active:
            self.active.set()
        else:
            self.active.clear()

    def run(self) -> None:
        while not self.isInterruptionRequested():
            if self.active.is_set() and self.enhancer.spectrum_enabled:
                try:
                    self.enhancer.compute_spectrum()
                    spectrum = self.enhancer.spectrum
                    self.spectrum_ready.emit(None if spectrum is None else [float(value) for value in spectrum])
                except Exception:
                    logger.debug("Fallo al calcular spectrum Qt", exc_info=True)
            self.msleep(33)

    def stop(self) -> None:
        self.requestInterruption()
        self.active.clear()
        self.wait(1500)


class QtMainWindow(QMainWindow):
    """Ventana Qt paralela; no sustituye ``audio_enhancer.app.App``."""

    def __init__(self, startup_metrics: StartupMetrics | None = None) -> None:
        super().__init__()
        self.metrics = startup_metrics or StartupMetrics()
        self.metrics.mark("root_created")
        self.language = detect_system_language()
        self.enhancer = Enhancer()
        self.engine = AudioEngine(self.enhancer)
        self.custom_presets: dict[str, tuple[float, float, float, list[float]]] = {}
        self.loopbacks: list[dict] = []
        self.speakers: list[dict] = []
        self.pa = None
        self.running = False
        self.go = False
        self._closing = False
        self._open_output_args: tuple[int, int] | None = None
        self._prefill_deadline = 0.0
        self._latest_spectrum: list[float] | None = None
        self._discovery_thread: QThread | None = None
        self._discovery_worker: DeviceDiscoveryWorker | None = None
        self._spectrum_worker = SpectrumWorker(self.enhancer, self)

        self.setWindowTitle(WINDOW_TITLE + " [Qt experimental]")
        self.resize(860, 740)
        self.setMinimumSize(760, 560)
        self.setWindowIcon(QIcon(resource_path("app.ico")))
        self.setStyleSheet(self._stylesheet())
        self._build_shell()

    def _t(self, text: str) -> str:
        return translate(text, self.language)

    @staticmethod
    def _stylesheet() -> str:
        return """
        QMainWindow, QWidget { background: #202124; color: #e8eaed; font-family: 'Segoe UI'; font-size: 12px; }
        QFrame#card { background: #292a2d; border: 1px solid #3c4043; border-radius: 12px; }
        QLabel#title { color: #49a6e9; font-size: 26px; font-weight: 700; }
        QLabel#subtitle { color: #aeb4bb; font-size: 12px; }
        QLabel#section { font-size: 16px; font-weight: 700; }
        QLabel#status { font-weight: 700; padding: 4px; }
        QComboBox, QLineEdit { background: #303134; border: 1px solid #5f6368;
        border-radius: 6px; padding: 6px; }
        QPushButton { background: #0078d4; border: 0; border-radius: 7px; padding: 8px 12px; font-weight: 600; }
        QPushButton:hover { background: #1689e5; }
        QPushButton:disabled { background: #55585c; color: #a0a0a0; }
        QSlider::groove:horizontal { height: 5px; background: #55585c; border-radius: 2px; }
        QSlider::handle:horizontal { width: 14px; margin: -5px 0; background: #49a6e9; border-radius: 7px; }
        QSlider::groove:vertical { width: 5px; background: #55585c; border-radius: 2px; }
        QSlider::handle:vertical { height: 14px; margin: 0 -5px; background: #49a6e9; border-radius: 7px; }
        QCheckBox::indicator { width: 16px; height: 16px; }
        QScrollBar:vertical { background: #202124; width: 12px; margin: 2px; }
        QScrollBar::handle:vertical { background: #656a70; min-height: 28px; border-radius: 6px; }
        """

    def _build_shell(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        root_layout = QGridLayout(root)
        root_layout.setContentsMargins(20, 12, 20, 12)
        root_layout.setVerticalSpacing(8)
        root_layout.setRowStretch(1, 1)

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel(self._t("Audio Enhancer"))
        title.setObjectName("title")
        subtitle = QLabel(self._t("Procesamiento del audio del sistema vía WASAPI loopback"))
        subtitle.setObjectName("subtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        root_layout.addWidget(header, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.placeholder = QLabel(self._t("Preparando interfaz…"))
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.placeholder)
        root_layout.addWidget(self.scroll, 1, 0)

        self.status = QLabel(self._t("Preparando interfaz…"))
        self.status.setObjectName("status")
        self.status.setStyleSheet(f"color: {WARN};")
        root_layout.addWidget(self.status, 2, 0)
        self.metrics.mark("shell")

    def build_content(self) -> None:
        """Construye todos los controles en una sola transición visual."""
        if hasattr(self, "content"):
            return
        self.metrics.mark("first_paint")
        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 4, 0)
        content_layout.setSpacing(8)
        self._build_devices(content_layout)
        self._build_effects(content_layout)
        self._build_equalizer(content_layout)
        self._build_spectrum(content_layout)
        self._build_actions(content_layout)
        content_layout.addStretch(1)
        self.scroll.setWidget(self.content)
        self._apply_config()
        self.metrics.mark("ui_ready")
        self.status.setText(self._t("Detectando dispositivos…"))
        self._start_discovery()
        self._spectrum_worker.spectrum_ready.connect(self._receive_spectrum)
        self._spectrum_worker.start()
        self.visual_timer = QTimer(self)
        self.visual_timer.setInterval(33)
        self.visual_timer.timeout.connect(self._refresh_visuals)
        self.visual_timer.start()
        logger.warning("Qt interfaz lista: %s", self.metrics.summary())

    def _section_title(self, parent: QVBoxLayout, text: str) -> None:
        label = QLabel(self._t(text))
        label.setObjectName("section")
        parent.addWidget(label)

    def _build_devices(self, parent: QVBoxLayout) -> None:
        card = CardFrame()
        layout = QGridLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        self._section_title(layout, "Dispositivos y ruteo")
        layout.addWidget(QLabel(self._t("Captura (loopback):")), 1, 0)
        self.source_combo = QComboBox()
        self.source_combo.setPlaceholderText(self._t("Detectando dispositivos…"))
        self.source_combo.setEnabled(False)
        self.source_combo.currentTextChanged.connect(self._route_guard)
        layout.addWidget(self.source_combo, 1, 1)
        layout.addWidget(QLabel(self._t("Salida (física):")), 2, 0)
        self.output_combo = QComboBox()
        self.output_combo.setPlaceholderText(self._t("Detectando dispositivos…"))
        self.output_combo.setEnabled(False)
        self.output_combo.currentTextChanged.connect(self._route_guard)
        layout.addWidget(self.output_combo, 2, 1)
        self.route_label = QLabel()
        self.route_label.setWordWrap(True)
        layout.addWidget(self.route_label, 3, 0, 1, 2)
        self.refresh_button = QPushButton(self._t("Actualizar dispositivos"))
        self.refresh_button.clicked.connect(self._start_discovery)
        layout.addWidget(self.refresh_button, 4, 0, 1, 2)
        layout.setColumnStretch(1, 1)
        parent.addWidget(card)

    def _build_effects(self, parent: QVBoxLayout) -> None:
        card = CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        self._section_title(layout, "Efectos")
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel(self._t("Configuración:")))
        self.preset_combo = QComboBox()
        self.preset_combo.currentTextChanged.connect(self.apply_preset)
        preset_row.addWidget(self.preset_combo)
        self.ab_button = QPushButton(self._t("A: Efectos ON"))
        self.ab_button.clicked.connect(self.toggle_ab)
        preset_row.addWidget(self.ab_button)
        layout.addLayout(preset_row)

        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel(self._t("Nuevo preset:")))
        self.preset_entry = QLineEdit()
        self.preset_entry.setPlaceholderText(self._t("nombre del preset"))
        custom_row.addWidget(self.preset_entry)
        save_button = QPushButton(self._t("Guardar"))
        save_button.clicked.connect(self._save_custom_preset)
        custom_row.addWidget(save_button)
        delete_button = QPushButton(self._t("Borrar"))
        delete_button.clicked.connect(self._delete_custom_preset)
        custom_row.addWidget(delete_button)
        layout.addLayout(custom_row)

        for label, minimum, maximum, value, callback, fmt in (
            ("Volumen", 0.0, 2.0, 1.0, self.set_volume, "mult"),
            ("Bass Boost (dB)", 0.0, 12.0, 0.0, self.set_bass, "db"),
            ("Treble Boost (dB)", 0.0, 12.0, 0.0, self.set_treble, "db"),
        ):
            self._add_horizontal_slider(layout, label, minimum, maximum, value, callback, fmt)

        toggles = QHBoxLayout()
        self.limiter_check = QCheckBox(self._t("Limitador suave"))
        self.limiter_check.setChecked(True)
        self.limiter_check.toggled.connect(self.toggle_limiter)
        self.compressor_check = QCheckBox(self._t("Compresor RMS"))
        self.compressor_check.setChecked(True)
        self.compressor_check.toggled.connect(self.toggle_compressor)
        toggles.addWidget(self.limiter_check)
        toggles.addWidget(self.compressor_check)
        toggles.addWidget(QLabel(self._t("Nivel:")))
        self.meter_bar = QSlider(Qt.Orientation.Horizontal)
        self.meter_bar.setRange(0, 100)
        self.meter_bar.setEnabled(False)
        toggles.addWidget(self.meter_bar)
        self.meter_label = QLabel("0 dB")
        toggles.addWidget(self.meter_label)
        layout.addLayout(toggles)
        parent.addWidget(card)

    def _add_horizontal_slider(
        self,
        parent: QVBoxLayout,
        label: str,
        minimum: float,
        maximum: float,
        value: float,
        callback,
        fmt: str,
    ) -> None:
        row = QHBoxLayout()
        row.addWidget(QLabel(self._t(label)))
        slider = QSlider(Qt.Orientation.Horizontal)
        scale = 100
        slider.setRange(int(minimum * scale), int(maximum * scale))
        slider.setValue(int(value * scale))
        label_widget = QLabel()

        def update_value(raw: int) -> None:
            value = raw / scale
            callback(value)
            label_widget.setText(self._format_value(value, fmt))

        slider.valueChanged.connect(update_value)
        row.addWidget(slider, 1)
        row.addWidget(label_widget)
        parent.addLayout(row)
        attr = {
            "Volumen": "volume_slider",
            "Bass Boost (dB)": "bass_slider",
            "Treble Boost (dB)": "treble_slider",
        }[label]
        setattr(self, attr, slider)
        setattr(self, attr.replace("_slider", "_label"), label_widget)
        label_widget.setText(self._format_value(value, fmt))

    @staticmethod
    def _format_value(value: float, fmt: str) -> str:
        return ("+%.1f dB" % value) if fmt == "db" else ("%.2fx" % value)

    def _build_equalizer(self, parent: QVBoxLayout) -> None:
        card = CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        self._section_title(layout, "Ecualizador (9 bandas)")
        grid = QGridLayout()
        self.eq_sliders: list[QSlider] = []
        for index, frequency in enumerate(self.enhancer.eq_bands):
            column = QVBoxLayout()
            label = QLabel("%d Hz" % frequency if frequency < 1000 else "%d kHz" % (frequency // 1000))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            slider = QSlider(Qt.Orientation.Vertical)
            slider.setRange(-12, 12)
            slider.setValue(0)
            slider.setMinimumHeight(120)
            slider.valueChanged.connect(lambda value, idx=index: self.set_eq(idx, value))
            column.addWidget(label)
            column.addWidget(slider, 1, Qt.AlignmentFlag.AlignHCenter)
            grid.addLayout(column, 0, index)
            self.eq_sliders.append(slider)
        layout.addLayout(grid)
        parent.addWidget(card)

    def _build_spectrum(self, parent: QVBoxLayout) -> None:
        card = CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        self._section_title(layout, "Analizador de espectro")
        self.spectrum_widget = SpectrumWidget()
        layout.addWidget(self.spectrum_widget)
        parent.addWidget(card)

    def _build_actions(self, parent: QVBoxLayout) -> None:
        card = QWidget()
        layout = QVBoxLayout(card)
        self.start_button = QPushButton("▶  " + self._t("Iniciar audio del sistema"))
        self.start_button.clicked.connect(self.toggle_audio)
        layout.addWidget(self.start_button)
        row = QHBoxLayout()
        cable = QPushButton(self._t("Instalar loopback propio (VB-CABLE)"))
        cable.clicked.connect(self._show_cable_guide)
        row.addWidget(cable)
        reset = QPushButton(self._t("Restablecer"))
        reset.clicked.connect(self.reset)
        row.addWidget(reset)
        self.autostart_check = QCheckBox(self._t("Iniciar con Windows"))
        self.autostart_check.toggled.connect(self._toggle_autostart)
        row.addWidget(self.autostart_check)
        layout.addLayout(row)
        parent.addWidget(card)

    def _start_discovery(self) -> None:
        if self._discovery_thread is not None and self._discovery_thread.isRunning():
            return
        self.source_combo.clear()
        self.output_combo.clear()
        self.source_combo.setEnabled(False)
        self.output_combo.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.status.setText(self._t("Detectando dispositivos…"))
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
        self.source_combo.addItems([device["name"] for device in self.loopbacks])
        self.output_combo.addItems([device["name"] for device in self.speakers])
        self.source_combo.setEnabled(True)
        self.output_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)
        if error:
            self.status.setText(self._t("No se pudieron detectar dispositivos: %s") % error)
            self.status.setStyleSheet(f"color: {DANGER};")
        else:
            self._auto_select()
            self._route_guard()
            self.status.setText(self._t("Dispositivos listos."))
            self.status.setStyleSheet(f"color: {OK};")
        self.metrics.mark("devices_ready")
        logger.warning("Qt dispositivos listos: %s", self.metrics.summary())

    def _clear_discovery_refs(self) -> None:
        self._discovery_thread = None
        self._discovery_worker = None

    def _auto_select(self) -> None:
        if self.loopbacks and not self.source_combo.currentText():
            index = 0
            for candidate, device in enumerate(self.loopbacks):
                if any(key in device["name"].lower() for key in CABLE_KEYWORDS):
                    index = candidate
                    break
            self.source_combo.setCurrentIndex(index)
        if self.speakers and not self.output_combo.currentText():
            self.output_combo.setCurrentIndex(0)

    def _route_guard(self, *_args) -> None:
        source = self.source_combo.currentText()
        output = self.output_combo.currentText()
        if not source or not output:
            self.go = False
            self.route_label.setText(self._t("Selecciona una fuente de captura y una salida física."))
            self.route_label.setStyleSheet(f"color: {WARN};")
            return
        source_lower = source.lower()
        output_lower = output.lower()
        same = "".join(source_lower.split()) == "".join(output_lower.split())
        output_virtual = any(key in output_lower for key in VIRTUAL_CABLE_KEYWORDS)
        if same or output_virtual:
            self.go = False
            text = self._t("Revisa el ruteo: la salida debe ser física y distinta de la captura.")
            self.route_label.setText(text)
            self.route_label.setStyleSheet(f"color: {DANGER};")
            return
        self.go = True
        self.route_label.setText(self._t("Ruteo listo: captura virtual → salida física."))
        self.route_label.setStyleSheet(f"color: {OK};")

    def set_volume(self, value: float) -> None:
        self.enhancer.volume = float(value)

    def set_bass(self, value: float) -> None:
        self.enhancer.bass = float(value)

    def set_treble(self, value: float) -> None:
        self.enhancer.treble = float(value)

    def set_eq(self, index: int, value: int) -> None:
        self.enhancer.eq_gains[index] = float(value)

    def toggle_ab(self) -> None:
        self.enhancer.blend = 0.0 if self.enhancer.blend > 0.5 else 1.0
        enabled = self.enhancer.blend > 0.5
        self.ab_button.setText(self._t("A: Efectos ON") if enabled else self._t("B: Directo (OFF)"))
        self._set_status(self._t("A/B actualizado."), OK if enabled else WARN)

    def toggle_limiter(self, enabled: bool) -> None:
        self.enhancer.limiter = bool(enabled)

    def toggle_compressor(self, enabled: bool) -> None:
        self.enhancer.compressor = bool(enabled)

    def _refresh_visuals(self) -> None:
        peak = max(0.0, float(self.enhancer.level_peak))
        self.meter_bar.setValue(min(100, int(peak * 100)))
        self.meter_label.setText("%.1f dB" % (20.0 * __import__("math").log10(peak) if peak > 1e-6 else -60.0))
        if self._latest_spectrum is not None:
            self.spectrum_widget.set_spectrum(self._latest_spectrum)
            self._latest_spectrum = None

    @Slot(object)
    def _receive_spectrum(self, values) -> None:
        self._latest_spectrum = values

    def _all_presets(self):
        presets = dict(PRESETS)
        presets.update(self.custom_presets)
        return presets

    def apply_preset(self, name: str) -> None:
        if not name or name not in self._all_presets():
            return
        volume, bass, treble, gains = self._all_presets()[name]
        self.enhancer.volume = float(volume)
        self.enhancer.bass = float(bass)
        self.enhancer.treble = float(treble)
        self.enhancer.eq_gains = [float(gain) for gain in gains]
        self._sync_ui_from_state()

    def _refresh_preset_list(self, keep: str | None = None) -> None:
        current = keep or self.preset_combo.currentText()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItems(list(self._all_presets()))
        if current in self._all_presets():
            self.preset_combo.setCurrentText(current)
        self.preset_combo.blockSignals(False)

    def _save_custom_preset(self) -> None:
        name = self.preset_entry.text().strip()
        if not name:
            self._set_status(self._t("Escribe un nombre para el preset personalizado."), WARN)
            return
        self.custom_presets[name] = (
            float(self.enhancer.volume),
            float(self.enhancer.bass),
            float(self.enhancer.treble),
            [float(gain) for gain in self.enhancer.eq_gains],
        )
        self.preset_entry.clear()
        self._refresh_preset_list(name)
        self._save_config()

    def _delete_custom_preset(self) -> None:
        name = self.preset_combo.currentText()
        if name not in self.custom_presets:
            self._set_status(self._t("Selecciona un preset personalizado para borrarlo."), WARN)
            return
        del self.custom_presets[name]
        self._refresh_preset_list(DEFAULT_PRESET)
        self._save_config()

    def reset(self) -> None:
        self.apply_preset(DEFAULT_PRESET)
        self._set_status(self._t("Controles restablecidos a plano"), OK)

    def _sync_ui_from_state(self) -> None:
        for slider, value in (
            (self.volume_slider, self.enhancer.volume * 100),
            (self.bass_slider, self.enhancer.bass * 100),
            (self.treble_slider, self.enhancer.treble * 100),
        ):
            slider.blockSignals(True)
            slider.setValue(int(value))
            slider.blockSignals(False)
        self.volume_label.setText(self._format_value(self.enhancer.volume, "mult"))
        self.bass_label.setText(self._format_value(self.enhancer.bass, "db"))
        self.treble_label.setText(self._format_value(self.enhancer.treble, "db"))
        for slider, gain in zip(self.eq_sliders, self.enhancer.eq_gains, strict=False):
            slider.blockSignals(True)
            slider.setValue(int(gain))
            slider.blockSignals(False)
        self.limiter_check.setChecked(self.enhancer.limiter)
        self.compressor_check.setChecked(self.enhancer.compressor)

    def _apply_config(self) -> None:
        config = load_config()
        if not config:
            self._refresh_preset_list(DEFAULT_PRESET)
            return
        custom = config.get("custom_presets")
        if isinstance(custom, dict):
            self.custom_presets = {}
            for name, value in custom.items():
                if isinstance(value, (list, tuple)) and len(value) == 4:
                    self.custom_presets[str(name)] = tuple(value)
        self.enhancer.volume = float(config.get("volume", 1.0))
        self.enhancer.bass = float(config.get("bass", 0.0))
        self.enhancer.treble = float(config.get("treble", 0.0))
        gains = config.get("eq_gains")
        if isinstance(gains, list) and len(gains) == len(self.enhancer.eq_gains):
            self.enhancer.eq_gains = [float(gain) for gain in gains]
        self.enhancer.limiter = bool(config.get("limiter", True))
        self.enhancer.compressor = bool(config.get("compressor", True))
        self._refresh_preset_list(config.get("preset", DEFAULT_PRESET))
        self._sync_ui_from_state()

    def _save_config(self) -> None:
        config = {
            "source": self.source_combo.currentText(),
            "output": self.output_combo.currentText(),
            "preset": self.preset_combo.currentText(),
            "volume": float(self.enhancer.volume),
            "bass": float(self.enhancer.bass),
            "treble": float(self.enhancer.treble),
            "eq_gains": [float(gain) for gain in self.enhancer.eq_gains],
            "limiter": bool(self.enhancer.limiter),
            "compressor": bool(self.enhancer.compressor),
            "custom_presets": {name: list(value) for name, value in self.custom_presets.items()},
        }
        if not save_config(config):
            logger.warning("No se pudo guardar config Qt en %s", CONFIG_PATH)

    def _selected(self):
        source = next((item for item in self.loopbacks if item["name"] == self.source_combo.currentText()), None)
        output = next((item for item in self.speakers if item["name"] == self.output_combo.currentText()), None)
        return source, output

    def toggle_audio(self) -> None:
        if self.running:
            self._stop_audio()
            return
        source, output = self._selected()
        if not self.go or source is None or output is None:
            self._route_guard()
            self._set_status(self._t("Revisa el ruteo antes de iniciar."), DANGER)
            return
        try:
            self._start_audio(source, output)
        except Exception as exc:
            logger.exception("No se pudo iniciar el loopback Qt")
            self._set_status(self._t("No se pudo iniciar el loopback: %s") % exc, DANGER)

    def _start_audio(self, source: dict, output: dict) -> None:
        pa_mod = _pa()
        if self.pa is None:
            self.pa = pa_mod.PyAudio()
        rate = int(output.get("defaultSampleRate", 48000))
        if rate < 8000 or rate > 384000:
            rate = 48000
        if rate != self.enhancer.sample_rate:
            self.enhancer.sample_rate = rate
            self.enhancer._spec_meta = None
        self.engine.configure_ring(rate)
        self.engine.start_capture(self.pa, source["index"], rate)
        self.running = True
        self._spectrum_worker.set_active(True)
        self.start_button.setText("⏹  " + self._t("Detener audio del sistema"))
        self._prefill_deadline = time.time() + 0.5
        self._open_output_args = (output["index"], rate)
        QTimer.singleShot(10, self._poll_prefill)

    def _poll_prefill(self) -> None:
        if not self.running:
            return
        if self.engine.fill() >= self.engine.nframes // 2 or time.time() > self._prefill_deadline:
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
            self._set_status(self._t("Audio activo."), OK)
        except Exception as exc:
            self.engine.stop()
            self.running = False
            self._spectrum_worker.set_active(False)
            self.start_button.setText("▶  " + self._t("Iniciar audio del sistema"))
            self._set_status(self._t("No se pudo iniciar el loopback: %s") % exc, DANGER)

    def _stop_audio(self) -> None:
        self.engine.stop()
        self.running = False
        self._spectrum_worker.set_active(False)
        self.start_button.setText("▶  " + self._t("Iniciar audio del sistema"))
        self._set_status(self._t("Procesamiento detenido"), WARN)

    def _set_status(self, text: str, color: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color};")

    def _toggle_autostart(self, enabled: bool) -> None:
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            if enabled:
                target = '"%s" "%s"' % (sys.executable, os.path.abspath(sys.argv[0]))
                winreg.SetValueEx(key, "AudioEnhancerFxStyleQt", 0, winreg.REG_SZ, target)
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, "AudioEnhancerFxStyleQt")
            winreg.CloseKey(key)
        except Exception:
            logger.exception("Fallo al configurar el inicio Qt con Windows")
            self.autostart_check.blockSignals(True)
            self.autostart_check.setChecked(not enabled)
            self.autostart_check.blockSignals(False)

    def _show_cable_guide(self) -> None:
        QMessageBox.information(
            self,
            self._t("Loopback propio (VB-CABLE)"),
            self._t("Instala VB-CABLE y selecciona CABLE Input como captura y una salida física como destino."),
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - API de Qt
        self._closing = True
        if getattr(self, "visual_timer", None) is not None:
            self.visual_timer.stop()
        if self.running:
            self._stop_audio()
        self._spectrum_worker.stop()
        self._save_config()
        if self._discovery_thread is not None and self._discovery_thread.isRunning():
            self._discovery_thread.quit()
            self._discovery_thread.wait(1500)
        with contextlib.suppress(Exception):
            if self.pa is not None:
                self.pa.terminate()
        event.accept()
