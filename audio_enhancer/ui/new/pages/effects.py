from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..audio_state import AudioState
from ..theme.colors import Theme


class EffectCard(QFrame):
    """Tarjeta de un efecto individual."""

    def __init__(self, title, min_val, max_val, default, unit="dB", has_toggle=True, parent=None) -> None:
        super().__init__(parent)
        self._unit = unit
        self._scale = 100
        # objectName "card": el QSS global lo estiliza y sigue el tema activo.
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_MD, Theme.SPACING_LG, Theme.SPACING_MD)
        layout.setSpacing(Theme.SPACING_SM)

        row_top = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_MD}px; "
            f"font-weight: {Theme.FONT_WEIGHT_MEDIUM}; background: transparent;"
        )
        row_top.addWidget(lbl)
        row_top.addStretch()
        self._toggle = None
        if has_toggle:
            self._toggle = QCheckBox("OFF")
            self._toggle.setStyleSheet(
                f"QCheckBox {{ color: {Theme.TEXT_MUTED};"
                f" font-size: {Theme.FONT_SIZE_SM}px; background: transparent; }}"
            )
            row_top.addWidget(self._toggle)
        layout.addLayout(row_top)

        row_slider = QHBoxLayout()
        row_slider.setSpacing(Theme.SPACING_MD)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(int(min_val * self._scale), int(max_val * self._scale))
        self._slider.setValue(int(default * self._scale))
        row_slider.addWidget(self._slider, 1)
        self._value_label = QLabel()
        self._value_label.setFixedWidth(56)
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._value_label.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_SM}px; background: transparent;"
        )
        self._update_label(default)
        row_slider.addWidget(self._value_label)
        layout.addLayout(row_slider)
        self._slider.valueChanged.connect(self._on_value)

    def _update_label(self, value) -> None:
        if self._unit == "dB":
            self._value_label.setText(f"{value:+.1f} {self._unit}")
        else:
            self._value_label.setText(f"{value:.2f} {self._unit}")

    def _on_value(self, raw) -> None:
        self._update_label(raw / self._scale)

    def value(self) -> float:
        return self._slider.value() / self._scale

    def set_value(self, v) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(int(v * self._scale))
        self._slider.blockSignals(False)
        self._update_label(v)

    def is_enabled(self) -> bool:
        return self._toggle is None or self._toggle.isChecked()

    def set_enabled(self, v) -> None:
        if self._toggle is not None:
            self._toggle.blockSignals(True)
            self._toggle.setChecked(v)
            self._toggle.blockSignals(False)
            self._toggle.setText("ON" if v else "OFF")


class EffectsPage(QWidget):
    """Pagina de efectos: Bass, Treble, Compressor, Limiter."""

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

        title = QLabel("Efectos")
        title.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_XL}px; "
            f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
        )
        layout.addWidget(title)

        self._bass_card = EffectCard("Bass Boost", 0.0, 12.0, 0.0, "dB")
        self._bass_card._slider.valueChanged.connect(lambda v: self._on_bass(v / 100))
        if self._bass_card._toggle:
            self._bass_card._toggle.toggled.connect(self._on_bass_toggle)
        layout.addWidget(self._bass_card)

        self._treble_card = EffectCard("Treble Boost", 0.0, 12.0, 0.0, "dB")
        self._treble_card._slider.valueChanged.connect(lambda v: self._on_treble(v / 100))
        if self._treble_card._toggle:
            self._treble_card._toggle.toggled.connect(self._on_treble_toggle)
        layout.addWidget(self._treble_card)

        self._limiter_card = EffectCard("Limiter", 0.0, 1.0, 1.0, "", has_toggle=True)
        self._limiter_card._slider.setEnabled(False)
        self._limiter_card._toggle.setChecked(True)
        if self._limiter_card._toggle:
            self._limiter_card._toggle.toggled.connect(self._on_limiter_toggle)
        layout.addWidget(self._limiter_card)

        self._compressor_card = EffectCard("Compressor", 0.0, 1.0, 1.0, "", has_toggle=True)
        self._compressor_card._slider.setEnabled(False)
        self._compressor_card._toggle.setChecked(True)
        if self._compressor_card._toggle:
            self._compressor_card._toggle.toggled.connect(self._on_compressor_toggle)
        layout.addWidget(self._compressor_card)

        layout.addStretch()
        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _on_bass(self, v) -> None:
        self._state.bass = v

    def _on_treble(self, v) -> None:
        self._state.treble = v

    def _on_bass_toggle(self, on) -> None:
        self._bass_card._toggle.setText("ON" if on else "OFF")
        self._state.bass = self._bass_card.value() if on else 0.0

    def _on_treble_toggle(self, on) -> None:
        self._treble_card._toggle.setText("ON" if on else "OFF")
        self._state.treble = self._treble_card.value() if on else 0.0

    def _on_limiter_toggle(self, on) -> None:
        self._limiter_card._toggle.setText("ON" if on else "OFF")
        self._state.limiter = on

    def _on_compressor_toggle(self, on) -> None:
        self._compressor_card._toggle.setText("ON" if on else "OFF")
        self._state.compressor = on

    def set_bass(self, v) -> None:
        self._bass_card.set_value(v)

    def set_treble(self, v) -> None:
        self._treble_card.set_value(v)

    def set_limiter(self, on) -> None:
        self._limiter_card.set_enabled(on)

    def set_compressor(self, on) -> None:
        self._compressor_card.set_enabled(on)
