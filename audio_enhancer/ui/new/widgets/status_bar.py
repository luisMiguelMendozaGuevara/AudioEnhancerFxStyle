"""Barra de estado persistente en la parte inferior.

Muestra solo el estado (Activo / Inactivo) con su punto de color. Las métricas
del motor (underruns, huecos, deriva, latencia, DSP, LUFS, frecuencia) viven en
la página Ecualizador y la ruta de audio en la página Audio: aquí solo cabía una
línea y cualquier nombre de dispositivo la cortaba.

El texto del label sigue sirviendo para avisos puntuales ("No se pudo iniciar:
..."), que si no desaparecerían sin dejar rastro visible.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..theme.colors import Theme


class AppStatusBar(QWidget):
    """Barra de estado inferior persistente (solo el estado)."""

    def __init__(self, t=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._t = t or (lambda text: text)
        self._last_status: tuple[str, str] = ("Inactivo", "")
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

        self._status_label = QLabel(self._t("Inactivo"))
        self._status_label.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;"
        )
        layout.addWidget(self._status_label)

        layout.addStretch()

    def set_processing(self, active: bool) -> None:
        if active:
            self._status_dot.setStyleSheet(f"background: {Theme.SUCCESS}; border-radius: 4px;")
            self.set_status_text(self._t("Activo"), Theme.SUCCESS)
        else:
            self._status_dot.setStyleSheet(f"background: {Theme.TEXT_DIM}; border-radius: 4px;")
            self.set_status_text(self._t("Inactivo"), Theme.TEXT_MUTED)

    def set_status_text(self, text: str, color: str = "") -> None:
        self._last_status = (text, color)
        self._status_label.setText(text)
        if color:
            self._status_label.setStyleSheet(
                f"color: {color}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;"
            )
