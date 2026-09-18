"""Barra de estado persistente en la parte inferior.

Muestra: estado de procesamiento, ruta de audio, sample rate, latencia.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..theme.colors import Theme


class AppStatusBar(QWidget):
    """Barra de estado inferior persistente."""

    def __init__(self, t=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._t = t or (lambda text: text)
        self._last_status: tuple[str, str] = ("Detenido", "")
        self.setFixedHeight(Theme.STATUS_BAR_HEIGHT)
        self._build()

    def retranslate(self, t) -> None:
        """Actualiza el traductor y repinta el ultimo estado."""
        self._t = t
        self.set_status_text(*self._last_status)

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Theme.MARGIN, 0, Theme.MARGIN, 0)

        self._status_dot = QLabel("")
        self._status_dot.setFixedSize(8, 8)
        self._status_dot.setStyleSheet(f"background: {Theme.TEXT_DIM}; border-radius: 4px;")
        layout.addWidget(self._status_dot)

        self._status_label = QLabel(self._t("Detenido"))
        self._status_label.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;"
        )
        layout.addWidget(self._status_label)

        layout.addStretch()

        self._route_label = QLabel("")
        self._route_label.setStyleSheet(
            f"color: {Theme.TEXT_DIM}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;"
        )
        layout.addWidget(self._route_label)

        self._sep1 = QLabel("|")
        self._sep1.setStyleSheet(f"color: {Theme.BORDER}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;")
        layout.addWidget(self._sep1)

        self._sr_label = QLabel("48 kHz")
        self._sr_label.setStyleSheet(
            f"color: {Theme.TEXT_DIM}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;"
        )
        layout.addWidget(self._sr_label)

        self._sep2 = QLabel("|")
        self._sep2.setStyleSheet(f"color: {Theme.BORDER}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;")
        layout.addWidget(self._sep2)

        self._latency_label = QLabel("")
        self._latency_label.setStyleSheet(
            f"color: {Theme.TEXT_DIM}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;"
        )
        layout.addWidget(self._latency_label)

        self._sep3 = QLabel("|")
        self._sep3.setStyleSheet(f"color: {Theme.BORDER}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;")
        layout.addWidget(self._sep3)

        # Métricas en vivo del motor (underruns / huecos / ajustes de deriva).
        self._metrics_label = QLabel("")
        self._metrics_label.setStyleSheet(
            f"color: {Theme.TEXT_DIM}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;"
        )
        layout.addWidget(self._metrics_label)

    def set_processing(self, active: bool) -> None:
        if active:
            self._status_dot.setStyleSheet(f"background: {Theme.SUCCESS}; border-radius: 4px;")
            self.set_status_text(self._t("Procesando"), Theme.SUCCESS)
        else:
            self._status_dot.setStyleSheet(f"background: {Theme.TEXT_DIM}; border-radius: 4px;")
            self.set_status_text(self._t("Detenido"), Theme.TEXT_MUTED)

    def set_route(self, input_name: str, output_name: str) -> None:
        if input_name and output_name:
            self._route_label.setText(f"{input_name}  ->  {output_name}")
        else:
            self._route_label.setText("")

    def set_sample_rate(self, rate: int) -> None:
        if rate >= 1000:
            self._sr_label.setText(f"{rate // 1000} kHz")
        else:
            self._sr_label.setText(f"{rate} Hz")

    def set_latency(self, ms: float) -> None:
        self._latency_label.setText(f"{ms:.0f} ms")

    def set_metrics(self, text: str) -> None:
        """Métricas en vivo del motor: vacío oculta el separador."""
        self._sep3.setVisible(bool(text))
        self._metrics_label.setText(text)
        self._metrics_label.setVisible(bool(text))

    def set_status_text(self, text: str, color: str = "") -> None:
        self._last_status = (text, color)
        self._status_label.setText(text)
        if color:
            self._status_label.setStyleSheet(
                f"color: {color}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;"
            )
