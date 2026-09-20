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
        stereo: bool = False,
    ) -> None:
        super().__init__(parent)
        self._orientation = orientation
        self._show_label = show_label
        self._stereo = stereo
        self._level: float = 0.0
        self._peak: float = 0.0
        self._peak_hold: int = 0
        # Canales (solo en modo estéreo): dos carriles L/R.
        self._level_l: float = 0.0
        self._level_r: float = 0.0
        self._peak_l: float = 0.0
        self._peak_r: float = 0.0
        self._peak_hold_l: int = 0
        self._peak_hold_r: int = 0

        if orientation == "horizontal":
            self.setMinimumSize(120, 26 if stereo else 18)
            self.setMaximumHeight(30 if stereo else 24)
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

    def set_stereo(
        self,
        level_l: float,
        level_r: float,
        peak_l: float | None = None,
        peak_r: float | None = None,
    ) -> None:
        """Actualiza los dos canales (L/R) del medidor estéreo.

        El pico hace hold por canal con la misma lógica que el modo mono."""
        self._level_l = max(0.0, min(1.0, level_l))
        self._level_r = max(0.0, min(1.0, level_r))
        self._peak_l = self._advance_peak(self._peak_l, self._level_l, "l", peak_l)
        self._peak_r = self._advance_peak(self._peak_r, self._level_r, "r", peak_r)
        self.update()

    def _advance_peak(self, peak: float, level: float, channel: str, measured: float | None) -> float:
        if measured is not None:
            return max(peak * 0.995, max(0.0, min(1.0, measured)))
        if level > peak:
            setattr(self, f"_peak_hold_{channel}", 30)
            return level
        hold = getattr(self, f"_peak_hold_{channel}")
        if hold > 0:
            setattr(self, f"_peak_hold_{channel}", hold - 1)
            return peak
        return peak * 0.97

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

        if self._stereo and self._orientation == "horizontal":
            self._paint_stereo_h(p, w, h, green_end, yellow_end)
        elif self._orientation == "horizontal":
            self._paint_h(p, w, h, green_end, yellow_end)
        else:
            self._paint_v(p, w, h, green_end, yellow_end)

        p.end()

    def _paint_h(self, p: QPainter, w: int, h: int, g: float, y: float) -> None:
        bx, bw, by, bh = 2, w - 4, 4, h - 8
        self._paint_lane_h(p, bx, bw, by, bh, self._level, self._peak, g, y)

        if self._show_label:
            db = self._db_from_level(self._level)
            label = f"{db:+.1f} dB" if self._level > 1e-6 else "-inf dB"
            p.setPen(QColor(Theme.TEXT_MUTED))
            p.setFont(QFont(Theme.FONT_FAMILY, 8))
            p.drawText(QRectF(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter, label)

    def _paint_lane_h(
        self, p: QPainter, bx: float, bw: float, by: float, bh: float, level: float, peak: float, g: float, y: float
    ) -> None:
        """Un carril horizontal (fondo, relleno con gradiente, pico y borde)."""
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(Theme.SURFACE_ELEVATED))
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 2, 2)

        fill_w = level * bw
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

        if peak > 0.01:
            px = bx + peak * bw
            pc = (
                QColor(Theme.METER_CLIP)
                if peak > y
                else QColor(Theme.METER_PEAK)
                if peak > g
                else QColor(Theme.METER_MID)
            )
            p.setPen(QPen(pc, 2))
            p.drawLine(int(px), int(by), int(px), int(by + bh))

        p.setPen(QPen(QColor(Theme.BORDER_SOLID), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 2, 2)

    def _paint_stereo_h(self, p: QPainter, w: int, h: int, g: float, y: float) -> None:
        """Dos carriles: L arriba, R abajo (misma escala y zonas de color)."""
        bx, bw = 2, w - 4
        gap = 2
        lane_h = (h - 4 - gap) / 2.0
        self._paint_lane_h(p, bx, bw, 2, lane_h, self._level_l, self._peak_l, g, y)
        self._paint_lane_h(p, bx, bw, 2 + lane_h + gap, lane_h, self._level_r, self._peak_r, g, y)

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
