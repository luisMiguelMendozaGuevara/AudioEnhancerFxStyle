"""Sidebar de navegacion lateral con items clickeables.

Soporta modo completo y compacto. Emite page_requested.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..theme.colors import Theme, accent_subtle_color


class SidebarItem(QFrame):
    """Un item individual del sidebar."""

    clicked = Signal(str)

    def __init__(
        self,
        page_id: str,
        label: str,
        icon_char: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.page_id = page_id
        self._label_text = label
        self._icon_char = icon_char
        self._active = False
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(Theme.SIDEBAR_ITEM_HEIGHT)
        self._build()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(10)

        self._icon_label = QLabel(self._icon_char)
        self._icon_label.setFixedWidth(20)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setStyleSheet(f"font-size: 14px; color: {Theme.TEXT_MUTED}; background: transparent;")
        layout.addWidget(self._icon_label)

        self._text_label = QLabel(self._label_text)
        self._text_label.setStyleSheet(
            f"font-size: {Theme.FONT_SIZE_MD}px; color: {Theme.TEXT_MUTED}; background: transparent;"
        )
        layout.addWidget(self._text_label)
        layout.addStretch()

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def set_compact(self, compact: bool) -> None:
        self._text_label.setVisible(not compact)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.page_id)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Base del tema siempre: los tintes translúcidos necesitan fondo claro/oscuro
        # debajo (alfa sobre superficie no inicializada sale negra).
        p.fillRect(self.rect(), QColor(Theme.SIDEBAR_BG))

        if self._active:
            p.fillRect(self.rect(), accent_subtle_color())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(Theme.ACCENT))
            p.drawRoundedRect(QRectF(0, 6, 3, self.height() - 12), 1.5, 1.5)
            ic = Theme.ACCENT
            tc = Theme.TEXT
            tw = Theme.FONT_WEIGHT_SEMIBOLD
        elif self._hover:
            p.fillRect(self.rect(), QColor(Theme.SURFACE_HOVER))
            ic = Theme.TEXT
            tc = Theme.TEXT
            tw = Theme.FONT_WEIGHT_MEDIUM
        else:
            ic = Theme.TEXT_MUTED
            tc = Theme.TEXT_SECONDARY
            tw = Theme.FONT_WEIGHT_NORMAL

        self._icon_label.setStyleSheet(f"font-size: 14px; color: {ic}; background: transparent;")
        self._text_label.setStyleSheet(
            f"font-size: {Theme.FONT_SIZE_MD}px; color: {tc}; font-weight: {tw}; background: transparent;"
        )
        p.end()


class Sidebar(QWidget):
    """Sidebar de navegacion lateral."""

    page_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[SidebarItem] = []
        self._current_id: str = ""
        self._compact: bool = False
        self.setFixedWidth(Theme.SIDEBAR_WIDTH)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, Theme.SPACING_LG, 0, Theme.SPACING_LG)
        layout.setSpacing(2)
        layout.addSpacing(Theme.SPACING_SM)

        pages = [
            ("home", "Inicio", "⌂"),
            ("equalizer", "Ecualizador", "∿"),
            ("effects", "Efectos", "✦"),
            ("audio", "Audio", "⇄"),
            ("presets", "Presets", "☰"),
            ("settings", "Config", "⚙"),
        ]
        for pid, label, icon in pages:
            item = SidebarItem(pid, label, icon)
            item.clicked.connect(self._on_item_clicked)
            layout.addWidget(item)
            self._items.append(item)

        layout.addStretch()

    def _on_item_clicked(self, page_id: str) -> None:
        if page_id != self._current_id:
            self.set_active(page_id)
            self.page_requested.emit(page_id)

    def set_active(self, page_id: str) -> None:
        self._current_id = page_id
        for item in self._items:
            item.set_active(item.page_id == page_id)

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        w = Theme.SIDEBAR_COMPACT_WIDTH if compact else Theme.SIDEBAR_WIDTH
        self.setFixedWidth(w)
        for item in self._items:
            item.set_compact(compact)

    def resizeEvent(self, event) -> None:  # noqa: N802
        if self.width() < Theme.SIDEBAR_WIDTH - 20:
            self.set_compact(True)
        elif self.width() >= Theme.SIDEBAR_WIDTH - 20 and self._compact:
            self.set_compact(False)
        super().resizeEvent(event)
