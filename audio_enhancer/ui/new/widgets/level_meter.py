"""Medidor de nivel horizontal/vertical con QPainter.

Zonas de color (verde/amarillo/rojo), pico y etiqueta dB.
Recibe datos via set_level(). No accede a ningun dispositivo.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QWidget

from ..theme.colors import Theme

_DB_MIN = -60.0


class LevelMeterWidget(QWidget):
    """Medidor de nivel con escala dB, zonas de color y pico.

    Recibe datos via set_level(). No accede a ningun dispositivo.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        orientation: str = "horizontal",
        show_label: bool = True,
    ) -> None:
        super().__init__(parent)
        self._orientation = orientation
        self._show_label = show_label
        self._level: float = 0.0
        self._peak: float = 0.0
        self._peak_hold: int = 0

        if orientation == "horizontal":
            self.setMinimumSize(120, 18)
            self.setMaximumHeight(24)
        else:
            self.setMinimumSize(18, 120)
            self.setMaximumWidth(24)

        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def set_level(self, level: float, peak: float | None = None) -> None:
        self._level = max(0.0, min(1.0, level))
        if peak is not None:
            self._peak = max(self._peak * 0.995, max(0.0, min(1.0, peak)))
        else:
            if self._level > self._peak:
                self._peak = self._level
                self._peak_hold = 30
            elif self._peak_hold > 0:
                self._peak_hold -= 1
            else:
                self._peak *= 0.97
        self.update()

    def _db_from_level(self, level: float) -> float:
        if level < 1e-6:
            return _DB_MIN
        return 20.0 * math.log10(level)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()

        p.fillRect(self.rect(), QColor(Theme.METER_BG))

        green_end = 0.5
        yellow_end = 0.8

        if self._orientation == "horizontal":
            self._paint_h(p, w, h, green_end, yellow_end)
        else:
            self._paint_v(p, w, h, green_end, yellow_end)

        p.end()

    def _paint_h(self, p: QPainter, w: int, h: int, g: float, y: float) -> None:
        bx, bw, by, bh = 2, w - 4, 4, h - 8

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(Theme.SURFACE_ELEVATED))
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 2, 2)

        fill_w = self._level * bw
        if fill_w > 0:
            grad = QLinearGradient(bx, 0, bx + bw, 0)
            grad.setColorAt(0, QColor(Theme.METER_LOW))
            grad.setColorAt(g, QColor(Theme.METER_LOW))
            grad.setColorAt(g, QColor(Theme.METER_MID))
            grad.setColorAt(y, QColor(Theme.METER_MID))
            grad.setColorAt(y, QColor(Theme.METER_HIGH))
            grad.setColorAt(1.0, QColor(Theme.METER_PEAK))
            p.setBrush(QBrush(grad))
            p.setClipRect(QRectF(bx, by, fill_w, bh))
            p.drawRoundedRect(QRectF(bx, by, bw, bh), 2, 2)
            p.setClipping(False)

        if self._peak > 0.01:
            px = bx + self._peak * bw
            pc = (
                QColor(Theme.METER_CLIP)
                if self._peak > y
                else QColor(Theme.METER_PEAK)
                if self._peak > g
                else QColor(Theme.METER_MID)
            )
            p.setPen(QPen(pc, 2))
            p.drawLine(int(px), int(by), int(px), int(by + bh))

        if self._show_label:
            db = self._db_from_level(self._level)
            label = f"{db:+.1f} dB" if self._level > 1e-6 else "-inf dB"
            p.setPen(QColor(Theme.TEXT_MUTED))
            p.setFont(QFont(Theme.FONT_FAMILY, 8))
            p.drawText(QRectF(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter, label)

        p.setPen(QPen(QColor(Theme.BORDER_SOLID), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 2, 2)

    def _paint_v(self, p: QPainter, w: int, h: int, g: float, y: float) -> None:
        bx, bw, by, bh = 4, w - 8, 2, h - 4

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(Theme.SURFACE_ELEVATED))
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 2, 2)

        fill_h = self._level * bh
        if fill_h > 0:
            grad = QLinearGradient(0, by + bh, 0, by)
            grad.setColorAt(0, QColor(Theme.METER_LOW))
            grad.setColorAt(1.0 - g, QColor(Theme.METER_LOW))
            grad.setColorAt(1.0 - g, QColor(Theme.METER_MID))
            grad.setColorAt(1.0 - y, QColor(Theme.METER_MID))
            grad.setColorAt(1.0 - y, QColor(Theme.METER_HIGH))
            grad.setColorAt(0, QColor(Theme.METER_PEAK))
            p.setBrush(QBrush(grad))
            top_y = by + bh - fill_h
            p.setClipRect(QRectF(bx, top_y, bw, fill_h))
            p.drawRoundedRect(QRectF(bx, by, bw, bh), 2, 2)
            p.setClipping(False)

        if self._peak > 0.01:
            py = by + bh - self._peak * bh
            pc = (
                QColor(Theme.METER_CLIP)
                if self._peak > y
                else QColor(Theme.METER_PEAK)
                if self._peak > g
                else QColor(Theme.METER_MID)
            )
            p.setPen(QPen(pc, 2))
            p.drawLine(int(bx), int(py), int(bx + bw), int(py))

        p.setPen(QPen(QColor(Theme.BORDER_SOLID), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 2, 2)
