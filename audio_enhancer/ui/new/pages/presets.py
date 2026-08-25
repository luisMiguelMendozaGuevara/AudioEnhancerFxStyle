from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..audio_state import AudioState
from ..theme.colors import Theme


class PresetsPage(QWidget):
    """Pagina de presets: incluidos, personalizados, favoritos."""

    delete_requested = Signal(str)

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

        title = QLabel(self._t("Presets"))
        title.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_XL}px; "
            f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
        )
        layout.addWidget(title)

        # New preset card
        new_card = self._card()
        nl = QVBoxLayout(new_card)
        nl.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_MD, Theme.SPACING_LG, Theme.SPACING_MD)
        nl.setSpacing(Theme.SPACING_MD)

        lbl = QLabel(self._t("GUARDAR PRESET"))
        lbl.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; "
            f"font-weight: {Theme.FONT_WEIGHT_MEDIUM}; background: transparent;"
        )
        nl.addWidget(lbl)

        row = QHBoxLayout()
        self._name_entry = QLineEdit()
        self._name_entry.setPlaceholderText(self._t("nombre del preset"))
        row.addWidget(self._name_entry, 1)
        self._save_btn = QPushButton(self._t("Guardar"))
        self._save_btn.setProperty("variant", "primary")
        self._save_btn.setFixedWidth(100)
        row.addWidget(self._save_btn)
        nl.addLayout(row)
        layout.addWidget(new_card)

        # Import/Export
        io_row = QHBoxLayout()
        self._import_btn = QPushButton(self._t("Importar"))
        self._export_btn = QPushButton(self._t("Exportar"))
        io_row.addWidget(self._import_btn)
        io_row.addWidget(self._export_btn)
        io_row.addStretch()
        layout.addLayout(io_row)

        # Included presets card
        inc_card = self._card()
        il = QVBoxLayout(inc_card)
        il.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_MD, Theme.SPACING_LG, Theme.SPACING_MD)

        lbl2 = QLabel(self._t("INCLUIDOS"))
        lbl2.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; "
            f"font-weight: {Theme.FONT_WEIGHT_MEDIUM}; background: transparent;"
        )
        il.addWidget(lbl2)

        self._included_list = QWidget()
        self._included_layout = QVBoxLayout(self._included_list)
        self._included_layout.setContentsMargins(0, 0, 0, 0)
        self._included_layout.setSpacing(2)
        il.addWidget(self._included_list)
        layout.addWidget(inc_card)

        # Custom presets card
        self._custom_card = self._card()
        cl = QVBoxLayout(self._custom_card)
        cl.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_MD, Theme.SPACING_LG, Theme.SPACING_MD)

        lbl3 = QLabel(self._t("PERSONALIZADOS"))
        lbl3.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; "
            f"font-weight: {Theme.FONT_WEIGHT_MEDIUM}; background: transparent;"
        )
        cl.addWidget(lbl3)

        self._custom_list = QWidget()
        self._custom_layout = QVBoxLayout(self._custom_list)
        self._custom_layout.setContentsMargins(0, 0, 0, 0)
        self._custom_layout.setSpacing(2)
        cl.addWidget(self._custom_list)
        layout.addWidget(self._custom_card)

        layout.addStretch()
        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def set_included_presets(self, names: list[str]) -> None:
        while self._included_layout.count():
            item = self._included_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for name in names:
            row = QHBoxLayout()
            lbl = QLabel(name)
            lbl.setStyleSheet(f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_MD}px; background: transparent;")
            row.addWidget(lbl)
            row.addStretch()
            self._included_layout.addLayout(row)

    def set_custom_presets(self, names: list[str]) -> None:
        while self._custom_layout.count():
            item = self._custom_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            if item.layout():
                # Clean up sub-layout items
                sub = item.layout()
                while sub.count():
                    si = sub.takeAt(0)
                    if si.widget():
                        si.widget().deleteLater()
        for name in names:
            row = QHBoxLayout()
            lbl = QLabel(name)
            lbl.setStyleSheet(f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_MD}px; background: transparent;")
            row.addWidget(lbl)
            row.addStretch()
            btn = QPushButton(self._t("Eliminar"))
            btn.setFixedWidth(80)
            btn.clicked.connect(lambda _=False, n=name: self.delete_requested.emit(n))
            btn.setStyleSheet(
                f"QPushButton {{ background: {Theme.DANGER_BG}; color: {Theme.DANGER};"
                f" border: 1px solid {Theme.DANGER}; "
                f"border-radius: {Theme.RADIUS_SM}px; padding: 4px 8px; font-size: {Theme.FONT_SIZE_XS}px; }}"
                f"QPushButton:hover {{ background: {Theme.DANGER}; color: white; }}"
            )
            row.addWidget(btn)
            self._custom_layout.addLayout(row)
