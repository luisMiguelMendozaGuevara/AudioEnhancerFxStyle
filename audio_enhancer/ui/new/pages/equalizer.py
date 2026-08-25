from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..audio_state import AudioState
from ..theme.colors import Theme, accent_subtle_color


class EQCurveWidget(QWidget):
    """Curva de ecualizador interactiva con 9 bandas arrastrables.

    Detras de la curva pinta barras de espectro (agregadas por banda) para
    ver la energia real de la musica bajo cada control."""

    band_changed = Signal(int, float)
    EQ_BANDS = [60, 150, 250, 500, 1000, 2000, 4000, 8000, 12000]
    DB_MIN = -12.0
    DB_MAX = 12.0
    SPECTRUM_BINS = 64

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._gains = [0.0] * 9
        self._dragging = -1
        self._hover = -1
        self._bars = [0.0] * 9  # nivel suavizado 0..1 por banda
        self.setMinimumHeight(200)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

    def set_spectrum(self, values) -> None:
        """Recibe el espectro (dB, bins log 20Hz-20kHz) y lo agrega por banda."""
        if not values:
            # Sin audio: las barras decaen hacia cero
            self._bars = [b * 0.85 for b in self._bars]
            self.update()
            return
        n = min(len(values), self.SPECTRUM_BINS)
        targets = [-120.0] * 9  # piso bajo silencio: -60 dB debe mapear a 0
        for i in range(n):
            freq = 20.0 * (1000.0 ** (i / (self.SPECTRUM_BINS - 1)))
            band = self._freq_to_band(freq)
            targets[band] = max(targets[band], float(values[i]))
        for b in range(9):
            norm = max(0.0, min(1.0, (targets[b] + 60.0) / 60.0))
            # Amplificacion visual: gamma < 1 levanta los niveles medios y la
            # ganancia 1.25 lleva los picos tipicos (-15..-10 dB) cerca del tope.
            self._bars[b] += (min(1.0, norm**0.65 * 1.25) - self._bars[b]) * 0.35
        self.update()

    def _freq_to_band(self, freq: float) -> int:
        """Banda EQ cuya frecuencia central es mas cercana en escala log."""
        import math

        best, best_d = 0, float("inf")
        log_f = math.log10(max(freq, 1.0))
        for i, center in enumerate(self.EQ_BANDS):
            d = abs(math.log10(center) - log_f)
            if d < best_d:
                best_d, best = d, i
        return best

    def set_gains(self, gains: list[float]) -> None:
        self._gains = list(gains)
        self.update()

    def reset(self) -> None:
        self._gains = [0.0] * 9
        self.update()

    def _gain_to_y(self, gain, h, mt, mb):
        ratio = (gain - self.DB_MIN) / (self.DB_MAX - self.DB_MIN)
        return mt + (1.0 - ratio) * (h - mt - mb)

    def _y_to_gain(self, y, h, mt, mb):
        ratio = 1.0 - (y - mt) / (h - mt - mb)
        return self.DB_MIN + ratio * (self.DB_MAX - self.DB_MIN)

    def _band_to_x(self, i, w, ml, mr):
        if i == 0:
            return ml + 20
        if i == 8:
            return w - mr - 20
        return ml + 20 + (w - ml - mr - 40) * (i / 8.0)

    def _x_to_nearest_band(self, x, w, ml, mr):
        best, best_d = -1, 25
        for i in range(9):
            d = abs(x - self._band_to_x(i, w, ml, mr))
            if d < best_d:
                best_d = d
                best = i
        return best

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        ml, mr, mt, mb = 44, 12, 16, 24

        p.fillRect(self.rect(), QColor(Theme.SPECTRUM_BG))

        # --- Barras de espectro por banda (detras de todo lo demas) ---
        bar_area_h = (h - mt - mb) * 0.94
        baseline = h - mb
        spacing = (w - ml - mr - 40) / 8.0
        bar_w = max(22.0, min(76.0, spacing * 0.62))
        ramp = [Theme.SPECTRUM_BAR_LOW, Theme.SPECTRUM_BAR_MID, Theme.SPECTRUM_BAR_HIGH]
        for i in range(9):
            level = self._bars[i]
            if level <= 0.01:
                continue
            x_center = self._band_to_x(i, w, ml, mr)
            bar_h = level * bar_area_h
            top = baseline - bar_h
            color = QColor(ramp[0] if level < 0.4 else ramp[1] if level < 0.75 else ramp[2])
            color.setAlpha(115)
            grad = QLinearGradient(x_center, top, x_center, baseline)
            grad.setColorAt(0.0, color)
            faded = QColor(color)
            faded.setAlpha(28)
            grad.setColorAt(1.0, faded)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(x_center - bar_w / 2, top, bar_w, bar_h), 3, 3)
            # Tapa brillante en el tope de la barra
            cap = QColor(Theme.SPECTRUM_BAR_PEAK)
            cap.setAlpha(190)
            p.setBrush(cap)
            p.drawRoundedRect(QRectF(x_center - bar_w / 2, top, bar_w, 3), 1.5, 1.5)

        zero_y = self._gain_to_y(0, h, mt, mb)
        p.setPen(QPen(QColor(Theme.EQ_GRID), 1, Qt.PenStyle.SolidLine))
        p.drawLine(int(ml), int(zero_y), int(w - mr), int(zero_y))

        p.setPen(QPen(QColor(Theme.EQ_GRID), 1, Qt.PenStyle.DotLine))
        for db in [-12, -6, 6, 12]:
            y = self._gain_to_y(db, h, mt, mb)
            p.drawLine(int(ml), int(y), int(w - mr), int(y))

        p.setPen(QColor(Theme.SPECTRUM_LABEL))
        p.setFont(QFont(Theme.FONT_FAMILY, 8))
        for db in [-12, -6, 0, 6, 12]:
            y = self._gain_to_y(db, h, mt, mb)
            label = f"+{db}" if db > 0 else str(db)
            p.drawText(2, int(y - 6), ml - 6, 12, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

        p.setPen(QColor(Theme.SPECTRUM_LABEL))
        for i, freq in enumerate(self.EQ_BANDS):
            x = self._band_to_x(i, w, ml, mr)
            label = f"{freq // 1000}k" if freq >= 1000 else str(freq)
            p.drawText(int(x) - 16, int(h - mb + 4), 32, 14, Qt.AlignmentFlag.AlignCenter, label)

        points = []
        for i, gain in enumerate(self._gains):
            x = self._band_to_x(i, w, ml, mr)
            y = self._gain_to_y(gain, h, mt, mb)
            points.append(QPointF(x, y))

        if len(points) >= 2:
            path = QPainterPath()
            path.moveTo(points[0].x(), zero_y)
            for pt in points:
                path.lineTo(pt.x(), pt.y())
            path.lineTo(points[-1].x(), zero_y)
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(accent_subtle_color(13)))
            p.drawPath(path)

            line_path = QPainterPath()
            line_path.moveTo(points[0])
            for i in range(1, len(points)):
                prev, curr = points[i - 1], points[i]
                cx = (prev.x() + curr.x()) / 2
                line_path.cubicTo(cx, prev.y(), cx, curr.y(), curr.x(), curr.y())
            p.setPen(QPen(QColor(Theme.EQ_CURVE), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(line_path)

        for i, pt in enumerate(points):
            is_active = i == self._hover or i == self._dragging
            radius = 7 if is_active else 5
            color = QColor(Theme.EQ_BAND_DOT_HOVER) if is_active else QColor(Theme.EQ_BAND_DOT)
            p.setPen(QPen(color, 2))
            p.setBrush(QColor(Theme.SPECTRUM_BG))
            p.drawEllipse(pt, radius, radius)
            if is_active:
                db_text = f"{self._gains[i]:+.1f} dB"
                p.setPen(QColor(Theme.TEXT))
                p.setFont(QFont(Theme.FONT_FAMILY, 9))
                p.drawText(int(pt.x() + 12), int(pt.y() - 8), db_text)

        p.setPen(QPen(QColor(Theme.BORDER_SOLID), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(ml, mt, w - ml - mr, h - mt - mb), 2, 2)
        p.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            ml, mr, mt, mb = 44, 12, 16, 24
            band = self._x_to_nearest_band(event.position().x(), self.width(), ml, mr)
            if band >= 0:
                self._dragging = band
                gain = self._y_to_gain(event.position().y(), self.height(), mt, mb)
                gain = max(self.DB_MIN, min(self.DB_MAX, gain))
                self._gains[band] = gain
                self.band_changed.emit(band, gain)
                self.update()

    def mouseMoveEvent(self, event) -> None:
        ml, mr, mt, mb = 44, 12, 16, 24
        if self._dragging >= 0:
            gain = self._y_to_gain(event.position().y(), self.height(), mt, mb)
            gain = max(self.DB_MIN, min(self.DB_MAX, gain))
            self._gains[self._dragging] = gain
            self.band_changed.emit(self._dragging, gain)
            self.update()
        else:
            band = self._x_to_nearest_band(event.position().x(), self.width(), ml, mr)
            if band != self._hover:
                self._hover = band
                self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = -1

    def leaveEvent(self, event) -> None:
        self._hover = -1
        self._dragging = -1
        self.update()


class EqualizerPage(QWidget):
    """Pagina de ecualizador con curva interactiva."""

    def __init__(self, state: AudioState, t=None, parent=None) -> None:
        super().__init__(parent)
        self._state = state
        self._t = t or (lambda text: text)
        self._build()
        # Barras de espectro en el EQ: mismo flujo de datos que la pagina Inicio
        self._state.spectrum_changed.connect(self._on_spectrum)

    def _on_spectrum(self, values) -> None:
        self._eq_curve.set_spectrum(values)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_LG, Theme.SPACING_LG, Theme.SPACING_LG)
        layout.setSpacing(Theme.SPACING_LG)

        title = QLabel(self._t("Ecualizador"))
        title.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_XL}px; "
            f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
        )
        layout.addWidget(title)

        self._eq_curve = EQCurveWidget()
        self._eq_curve.band_changed.connect(self._on_band_changed)
        layout.addWidget(self._eq_curve, 1)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton(self._t("Restablecer todo"))
        reset_btn.setFixedWidth(100)
        reset_btn.clicked.connect(self._reset_all)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _on_band_changed(self, index: int, gain: float) -> None:
        # Asignar la lista COMPLETA (no eq_gains[i] = gain): la mutacion in
        # place se salta el property setter y eq_changed nunca se emite, por
        # lo que el DSP no se enteraria del cambio.
        gains = list(self._state.eq_gains)
        gains[index] = gain
        self._state.eq_gains = gains

    def _reset_all(self) -> None:
        self._eq_curve.reset()
        self._state.eq_gains = [0.0] * 9

    def set_gains(self, gains: list[float]) -> None:
        self._eq_curve.set_gains(gains)
