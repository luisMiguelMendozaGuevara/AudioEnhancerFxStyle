from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..audio_state import AudioState
from ..theme.colors import Theme


class AudioPage(QWidget):
    """Pagina de dispositivos y ruteo de audio."""

    def __init__(self, state: AudioState, parent=None) -> None:
        super().__init__(parent)
        self._state = state
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_LG, Theme.SPACING_LG, Theme.SPACING_LG)
        layout.setSpacing(Theme.SPACING_LG)

        title = QLabel("Audio")
        title.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_XL}px; "
            f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
        )
        layout.addWidget(title)

        # Routing card
        route_card = self._card()
        rl = QVBoxLayout(route_card)
        rl.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_LG, Theme.SPACING_LG, Theme.SPACING_LG)
        rl.setSpacing(Theme.SPACING_MD)

        lbl = QLabel("ROUTING")
        lbl.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; "
            f"font-weight: {Theme.FONT_WEIGHT_MEDIUM}; background: transparent;"
        )
        rl.addWidget(lbl)

        # Input
        row_in = QHBoxLayout()
        lbl_in = QLabel("INPUT")
        lbl_in.setFixedWidth(84)
        lbl_in.setStyleSheet(
            f"color: {Theme.TEXT_SECONDARY}; font-size: {Theme.FONT_SIZE_LG}px; "
            f"font-weight: {Theme.FONT_WEIGHT_SEMIBOLD}; background: transparent;"
        )
        row_in.addWidget(lbl_in)
        self._input_combo = QComboBox()
        self._input_combo.setEnabled(False)
        row_in.addWidget(self._input_combo, 1)
        rl.addLayout(row_in)

        # Arrow
        arrow = QLabel("↓")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setStyleSheet(f"color: {Theme.TEXT_DIM}; font-size: 18px; background: transparent;")
        rl.addWidget(arrow)

        # App
        app_lbl = QLabel("AudioEnhancer")
        app_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        app_lbl.setStyleSheet(
            f"color: {Theme.ACCENT}; font-size: {Theme.FONT_SIZE_MD}px; "
            f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
        )
        rl.addWidget(app_lbl)

        # Arrow
        arrow2 = QLabel("↓")
        arrow2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow2.setStyleSheet(f"color: {Theme.TEXT_DIM}; font-size: 18px; background: transparent;")
        rl.addWidget(arrow2)

        # Output
        row_out = QHBoxLayout()
        lbl_out = QLabel("OUTPUT")
        lbl_out.setFixedWidth(84)
        lbl_out.setStyleSheet(
            f"color: {Theme.TEXT_SECONDARY}; font-size: {Theme.FONT_SIZE_LG}px; "
            f"font-weight: {Theme.FONT_WEIGHT_SEMIBOLD}; background: transparent;"
        )
        row_out.addWidget(lbl_out)
        self._output_combo = QComboBox()
        self._output_combo.setEnabled(False)
        row_out.addWidget(self._output_combo, 1)
        rl.addLayout(row_out)

        # Route status
        self._route_label = QLabel("")
        self._route_label.setWordWrap(True)
        self._route_label.setStyleSheet(
            f"color: {Theme.TEXT_SECONDARY}; font-size: {Theme.FONT_SIZE_MD}px; background: transparent;"
        )
        rl.addWidget(self._route_label)

        # Refresh button
        self._refresh_btn = QPushButton("Actualizar dispositivos")
        self._refresh_btn.setProperty("variant", "primary")
        self._refresh_btn.setMinimumHeight(38)
        rl.addWidget(self._refresh_btn)

        layout.addWidget(route_card)

        # Info card
        info_card = self._card()
        il = QVBoxLayout(info_card)
        il.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_MD, Theme.SPACING_LG, Theme.SPACING_MD)
        il.setSpacing(Theme.SPACING_SM)

        lbl2 = QLabel("DETAILS")
        lbl2.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; "
            f"font-weight: {Theme.FONT_WEIGHT_MEDIUM}; background: transparent;"
        )
        il.addWidget(lbl2)

        self._info_labels = {}
        for key, text in [("rate", "Sample Rate"), ("buffer", "Buffer"), ("latency", "Latency"), ("status", "Status")]:
            row = QHBoxLayout()
            k = QLabel(text)
            k.setFixedWidth(100)
            k.setStyleSheet(f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_MD}px; background: transparent;")
            v = QLabel("--")
            v.setStyleSheet(
                f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_MD}px; "
                f"font-weight: {Theme.FONT_WEIGHT_MEDIUM}; background: transparent;"
            )
            row.addWidget(k)
            row.addWidget(v, 1)
            il.addLayout(row)
            self._info_labels[key] = v

        layout.addWidget(info_card)
        layout.addStretch()

    def _card(self) -> QFrame:
        card = QFrame()
        # objectName "card": el QSS global lo estiliza y sigue el tema activo.
        card.setObjectName("card")
        return card

    def set_loopbacks(self, names: list[str]) -> None:
        self._input_combo.blockSignals(True)
        self._input_combo.clear()
        self._input_combo.addItems(names)
        self._input_combo.setEnabled(bool(names))
        self._input_combo.blockSignals(False)

    def set_speakers(self, names: list[str]) -> None:
        self._output_combo.blockSignals(True)
        self._output_combo.clear()
        self._output_combo.addItems(names)
        self._output_combo.setEnabled(bool(names))
        self._output_combo.blockSignals(False)

    def set_input(self, name: str) -> None:
        self._input_combo.setCurrentText(name)

    def set_output(self, name: str) -> None:
        self._output_combo.setCurrentText(name)

    def set_route_warning(self, text: str, color: str) -> None:
        self._route_label.setText(text)
        self._route_label.setStyleSheet(f"color: {color}; font-size: {Theme.FONT_SIZE_SM}px; background: transparent;")

    def set_info(self, rate: int = 0, buffer: int = 0, latency: float = 0.0, status: str = "--") -> None:
        if rate:
            self._info_labels["rate"].setText(f"{rate} Hz")
        if buffer:
            self._info_labels["buffer"].setText(f"{buffer} frames")
        if latency:
            self._info_labels["latency"].setText(f"{latency:.1f} ms")
        self._info_labels["status"].setText(status)
