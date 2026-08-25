from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..audio_state import AudioState
from ..theme.colors import Theme
from ..widgets.level_meter import LevelMeterWidget
from ..widgets.spectrum import SpectrumWidget


class HomePage(QWidget):
    """Pagina principal: estado, spectrum, meters, preset, volumen, A/B."""

    def __init__(self, state: AudioState, parent=None) -> None:
        super().__init__(parent)
        self._state = state
        self._build()

    def _build(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_LG, Theme.SPACING_LG, Theme.SPACING_LG)
        layout.setSpacing(Theme.SPACING_LG)

        self._build_status_card(layout)
        self._build_spectrum_card(layout)
        self._build_meters_card(layout)
        self._build_controls_card(layout)
        layout.addStretch()

        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _card(self) -> QFrame:
        card = QFrame()
        # objectName "card": el QSS global lo estiliza y sigue el tema activo.
        card.setObjectName("card")
        return card

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; "
            f"font-weight: {Theme.FONT_WEIGHT_MEDIUM}; background: transparent;"
        )
        return lbl

    def _build_status_card(self, parent: QVBoxLayout) -> None:
        card = self._card()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_MD, Theme.SPACING_LG, Theme.SPACING_MD)

        self._status_dot = QLabel("")
        self._status_dot.setFixedSize(10, 10)
        self._status_dot.setStyleSheet(f"background: {Theme.TEXT_DIM}; border-radius: 5px;")
        layout.addWidget(self._status_dot)

        self._status_text = QLabel("Detenido")
        self._status_text.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_LG}px; "
            f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
        )
        layout.addWidget(self._status_text)
        layout.addStretch()
        parent.addWidget(card)
        self._state.processing_changed.connect(self._on_processing_changed)

    def _build_spectrum_card(self, parent: QVBoxLayout) -> None:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_MD, Theme.SPACING_LG, Theme.SPACING_MD)
        lbl = self._section_label("SPECTRUM")
        layout.addWidget(lbl)
        self._spectrum = SpectrumWidget()
        self._spectrum.setMinimumHeight(280)
        layout.addWidget(self._spectrum)
        parent.addWidget(card)
        self._state.spectrum_changed.connect(self._on_spectrum_changed)

    def _build_meters_card(self, parent: QVBoxLayout) -> None:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_MD, Theme.SPACING_LG, Theme.SPACING_MD)
        layout.setSpacing(Theme.SPACING_MD)

        for tag, attr in [("INPUT", "_input_meter"), ("OUTPUT", "_output_meter")]:
            row = QHBoxLayout()
            lbl = QLabel(tag)
            lbl.setFixedWidth(56)
            lbl.setStyleSheet(
                f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; "
                f"font-weight: {Theme.FONT_WEIGHT_MEDIUM}; background: transparent;"
            )
            row.addWidget(lbl)
            meter = LevelMeterWidget(orientation="horizontal", show_label=False)
            setattr(self, attr, meter)
            row.addWidget(meter, 1)
            layout.addLayout(row)

        parent.addWidget(card)
        self._state.input_level_changed.connect(self._on_input_level)
        self._state.output_level_changed.connect(self._on_output_level)

    def _build_controls_card(self, parent: QVBoxLayout) -> None:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_MD, Theme.SPACING_LG, Theme.SPACING_MD)
        layout.setSpacing(Theme.SPACING_MD)

        row_top = QHBoxLayout()
        row_top.setSpacing(Theme.SPACING_MD)
        lbl_preset = QLabel("Preset")
        lbl_preset.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_SM}px; background: transparent;"
        )
        row_top.addWidget(lbl_preset)
        self._preset_combo = QComboBox()
        row_top.addWidget(self._preset_combo, 1)
        self._ab_button = QPushButton("A / B")
        self._ab_button.setFixedWidth(64)
        self._ab_button.setStyleSheet(
            f"QPushButton {{ background: {Theme.ACCENT}; border: none; border-radius: {Theme.RADIUS_MD}px; "
            f"padding: 6px 12px; color: {Theme.TEXT_ON_ACCENT}; font-weight: {Theme.FONT_WEIGHT_BOLD}; }}"
            f"QPushButton:hover {{ background: {Theme.ACCENT_HOVER}; }}"
        )
        row_top.addWidget(self._ab_button)
        layout.addLayout(row_top)

        row_vol = QHBoxLayout()
        row_vol.setSpacing(Theme.SPACING_MD)
        lbl_vol = QLabel("Volume")
        lbl_vol.setFixedWidth(56)
        lbl_vol.setStyleSheet(f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_SM}px; background: transparent;")
        row_vol.addWidget(lbl_vol)
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 200)
        self._volume_slider.setValue(100)
        row_vol.addWidget(self._volume_slider, 1)
        self._volume_label = QLabel("1.00x")
        self._volume_label.setFixedWidth(48)
        self._volume_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._volume_label.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_SM}px; background: transparent;"
        )
        row_vol.addWidget(self._volume_label)
        layout.addLayout(row_vol)
        parent.addWidget(card)

    def _on_processing_changed(self, active: bool) -> None:
        if active:
            self._status_dot.setStyleSheet(f"background: {Theme.SUCCESS}; border-radius: 5px;")
            self._status_text.setText("ACTIVE")
            self._status_text.setStyleSheet(
                f"color: {Theme.SUCCESS}; font-size: {Theme.FONT_SIZE_LG}px; "
                f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
            )
        else:
            self._status_dot.setStyleSheet(f"background: {Theme.TEXT_DIM}; border-radius: 5px;")
            self._status_text.setText("Detenido")
            self._status_text.setStyleSheet(
                f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_LG}px; "
                f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
            )

    def _on_spectrum_changed(self, data) -> None:
        self._spectrum.set_spectrum(data)

    def _on_input_level(self, level: float) -> None:
        self._input_meter.set_level(level)

    def _on_output_level(self, level: float) -> None:
        self._output_meter.set_level(level)

    def set_preset_items(self, names: list[str]) -> None:
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItems(names)
        self._preset_combo.blockSignals(False)

    def set_preset(self, name: str) -> None:
        self._preset_combo.setCurrentText(name)

    def set_volume(self, value: float) -> None:
        self._volume_slider.blockSignals(True)
        self._volume_slider.setValue(int(value * 100))
        self._volume_slider.blockSignals(False)
        self._volume_label.setText(f"{value:.2f}x")

    def set_ab(self, enabled: bool) -> None:
        if enabled:
            self._ab_button.setText("A / B")
            self._ab_button.setStyleSheet(
                f"QPushButton {{ background: {Theme.ACCENT}; border: none; border-radius: {Theme.RADIUS_MD}px; "
                f"padding: 6px 12px; color: {Theme.TEXT_ON_ACCENT}; font-weight: {Theme.FONT_WEIGHT_BOLD}; }}"
                f"QPushButton:hover {{ background: {Theme.ACCENT_HOVER}; }}"
            )
        else:
            self._ab_button.setText("B / A")
            self._ab_button.setStyleSheet(
                f"QPushButton {{ background: {Theme.SURFACE_ELEVATED}; border: 1px solid {Theme.BORDER}; "
                f"border-radius: {Theme.RADIUS_MD}px; padding: 6px 12px; "
                f"color: {Theme.TEXT_MUTED}; font-weight: {Theme.FONT_WEIGHT_BOLD}; }}"
                f"QPushButton:hover {{ background: {Theme.SURFACE_HOVER}; }}"
            )
