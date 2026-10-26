"""Barra de estado persistente en la parte inferior.

Muestra solo el estado (Activo / Inactivo) con su punto de color. Las métricas
del motor (underruns, huecos, deriva, latencia, DSP, LUFS, frecuencia) viven en
la página Ecualizador y la ruta de audio en la página Audio: aquí solo cabía una
línea y cualquier nombre de dispositivo la cortaba.

El texto del label sirve además para avisos puntuales ("No se pudo iniciar:
..."), que si no desaparecerían sin dejar rastro visible. Un aviso es temporal:
pasados unos segundos la barra vuelve al estado, para que nunca se quede
congelada diciendo "Dispositivos listos." mientras el audio ya está activo.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..theme.colors import Theme


class AppStatusBar(QWidget):
    """Barra de estado inferior persistente (solo el estado)."""

    #: Cuánto dura un aviso antes de volver al estado (ms).
    AVISO_MS = 6000

    def __init__(self, t=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._t = t or (lambda text: text)
        self._estado: tuple[str, str] = ("Inactivo", Theme.TEXT_MUTED)
        self._aviso: tuple[str, str] | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.AVISO_MS)
        self._timer.timeout.connect(self._volver_al_estado)
        self.setFixedHeight(Theme.STATUS_BAR_HEIGHT)
        self._build()

    def retranslate(self, t) -> None:
        """Actualiza el traductor y repinta el estado (el aviso caduca solo)."""
        self._t = t
        self._estado = (self._t(self._estado[0]), self._estado[1])
        self._aviso = None
        self._timer.stop()
        self._pintar(*self._estado)

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
        """Fija el ESTADO (permanente hasta el siguiente cambio)."""
        if active:
            self._status_dot.setStyleSheet(f"background: {Theme.SUCCESS}; border-radius: 4px;")
            self._estado = (self._t("Activo"), Theme.SUCCESS)
        else:
            self._status_dot.setStyleSheet(f"background: {Theme.TEXT_DIM}; border-radius: 4px;")
            self._estado = (self._t("Inactivo"), Theme.TEXT_MUTED)
        self._aviso = None
        self._timer.stop()
        self._pintar(*self._estado)

    def set_status_text(self, text: str, color: str = "") -> None:
        """Muestra un AVISO temporal; después la barra vuelve al estado."""
        self._aviso = (text, color)
        self._pintar(text, color)
        self._timer.start()

    def _volver_al_estado(self) -> None:
        """Fin del aviso: repinta el estado real del motor."""
        self._aviso = None
        self._timer.stop()
        self._pintar(*self._estado)

    def _pintar(self, text: str, color: str) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(
            f"color: {color or Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;"
        )

    @property
    def estado_visible(self) -> str:
        """Texto visible ahora mismo (estado o aviso), para tests e informes."""
        return self._status_label.text()
