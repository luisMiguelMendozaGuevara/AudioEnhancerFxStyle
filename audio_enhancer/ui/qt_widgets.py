"""Widgets Qt independientes de la ventana principal."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QWidget


class CardFrame(QFrame):
    """Panel visual común para mantener la jerarquía de la UI existente."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")


class SpectrumWidget(QWidget):
    """Dibuja todas las barras del spectrum en un único paintEvent."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spectrum: list[float] = []
        self.painted_frames = 0
        self.setMinimumHeight(80)
        self.setMaximumHeight(120)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    @property
    def bar_count(self) -> int:
        return len(self.spectrum)

    def set_spectrum(self, values: Iterable[float] | None) -> None:
        self.spectrum = [] if values is None else [float(value) for value in values]
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API de Qt
        self.painted_frames += 1
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#16181d"))
        if not self.spectrum:
            painter.end()
            return

        width = self.width()
        height = self.height()
        bar_width = width / len(self.spectrum)
        for index, db in enumerate(self.spectrum):
            normalized = max(0.0, min(1.0, (db + 60.0) / 60.0))
            bar_height = int(normalized * (height - 4))
            color = "#0078D4"
            if db > -12.0:
                color = "#d13438"
            elif db > -25.0:
                color = "#9d5d00"
            painter.fillRect(
                int(index * bar_width + 1),
                height - bar_height - 2,
                max(1, int(bar_width - 2)),
                bar_height,
                QColor(color),
            )
        painter.end()
