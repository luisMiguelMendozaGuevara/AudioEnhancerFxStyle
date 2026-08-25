from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..audio_state import AudioState
from ..theme.colors import Theme


class SettingsPage(QWidget):
    """Pagina de configuracion."""

    def __init__(self, state: AudioState, t=None, parent=None) -> None:
        super().__init__(parent)
        self._state = state
        self._t = t or (lambda text: text)
        self._build()

    def _card(self) -> QFrame:
        card = QFrame()
        # objectName "card": el QSS global lo estiliza y sigue el tema activo.
        card.setObjectName("card")
        return card

    def _build(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_LG, Theme.SPACING_LG, Theme.SPACING_LG)
        layout.setSpacing(Theme.SPACING_LG)

        title = QLabel(self._t("Config"))
        title.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_XL}px; "
            f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
        )
        layout.addWidget(title)

        # Language
        lang_card = self._card()
        ll = QVBoxLayout(lang_card)
        ll.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_MD, Theme.SPACING_LG, Theme.SPACING_MD)
        ll.setSpacing(Theme.SPACING_SM)
        section = QLabel(self._t("IDIOMA"))
        section.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; "
            f"font-weight: {Theme.FONT_WEIGHT_MEDIUM}; background: transparent;"
        )
        ll.addWidget(section)
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(["Espanol", "English"])
        ll.addWidget(self._lang_combo)
        layout.addWidget(lang_card)

        # Appearance
        app_card = self._card()
        al = QVBoxLayout(app_card)
        al.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_MD, Theme.SPACING_LG, Theme.SPACING_MD)
        al.setSpacing(Theme.SPACING_SM)
        section2 = QLabel(self._t("APARIENCIA"))
        section2.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; "
            f"font-weight: {Theme.FONT_WEIGHT_MEDIUM}; background: transparent;"
        )
        al.addWidget(section2)
        self._theme_combo = QComboBox()
        self._theme_combo.addItems([self._t("Oscuro"), self._t("Blanco")])
        al.addWidget(self._theme_combo)
        layout.addWidget(app_card)

        # Behavior
        beh_card = self._card()
        bl = QVBoxLayout(beh_card)
        bl.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_MD, Theme.SPACING_LG, Theme.SPACING_MD)
        bl.setSpacing(Theme.SPACING_MD)
        section3 = QLabel(self._t("COMPORTAMIENTO"))
        section3.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; "
            f"font-weight: {Theme.FONT_WEIGHT_MEDIUM}; background: transparent;"
        )
        bl.addWidget(section3)

        self._autostart_check = QCheckBox(self._t("Iniciar con Windows"))
        self._autostart_check.setStyleSheet(
            f"QCheckBox {{ color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_MD}px; background: transparent; }}"
        )
        bl.addWidget(self._autostart_check)

        self._tray_check = QCheckBox(self._t("Minimizar a bandeja al cerrar"))
        self._tray_check.setChecked(True)
        self._tray_check.setStyleSheet(
            f"QCheckBox {{ color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_MD}px; background: transparent; }}"
        )
        bl.addWidget(self._tray_check)

        self._autostart_audio_check = QCheckBox(self._t("Auto-iniciar audio al abrir"))
        self._autostart_audio_check.setStyleSheet(
            f"QCheckBox {{ color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_MD}px; background: transparent; }}"
        )
        bl.addWidget(self._autostart_audio_check)

        self._notifications_check = QCheckBox(self._t("Notificaciones"))
        self._notifications_check.setChecked(True)
        self._notifications_check.setStyleSheet(
            f"QCheckBox {{ color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_MD}px; background: transparent; }}"
        )
        bl.addWidget(self._notifications_check)
        layout.addWidget(beh_card)

        layout.addStretch()
        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
