from __future__ import annotations

import math

import numpy as np
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
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ....dsp import EQ_BAND_SETS as _EQ_BAND_SETS
from ....dsp import EQ_BANDS as _DSP_EQ_BANDS
from ..audio_state import AudioState
from ..theme.colors import Theme, accent_subtle_color


class EQCurveWidget(QWidget):
    """Curva de ecualizador interactiva con N bandas arrastrables.

    Fase 5 (EQ avanzado): el número de bandas es configurable (5/9/10/15/31),
    la posición X es logarítmica en frecuencia y cada banda se puede mover en
    ganancia (vertical) y en frecuencia central (horizontal). Detrás de la curva
    se pintan barras de espectro agregadas por banda."""

    band_changed = Signal(int, float)  # índice, ganancia (dB)
    freq_changed = Signal(int, float)  # índice, frecuencia (Hz)
    DB_MIN = -12.0
    DB_MAX = 12.0
    SPECTRUM_BINS = 64
    FREQ_MIN = 20.0
    FREQ_MAX = 20000.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Única fuente de verdad: las bandas viven en dsp.py (C3).
        self._bands: list[float] = list(_DSP_EQ_BANDS)
        self._gains: list[float] = [0.0] * len(self._bands)
        self._dragging = -1
        self._hover = -1
        self._press_x = 0.0
        self._bars = np.zeros(len(self._bands), dtype=np.float32)
        self._bin_band = np.zeros(self.SPECTRUM_BINS, dtype=np.intp)
        self._recompute_bin_map()
        self.setMinimumHeight(200)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

    # ---------- datos ----------

    def _recompute_bin_map(self) -> None:
        """Mapa bin de espectro -> banda más cercana en escala log (cacheado)."""
        log_bands = np.log10(np.maximum(np.asarray(self._bands, dtype=np.float64), 1.0))
        bins = self.FREQ_MIN * (1000.0 ** (np.arange(self.SPECTRUM_BINS) / (self.SPECTRUM_BINS - 1)))
        log_bins = np.log10(bins)
        idx = np.abs(log_bins[:, None] - log_bands[None, :]).argmin(axis=1)
        self._bin_band = idx.astype(np.intp)

    def set_bands(self, freqs) -> None:
        f = sorted(float(x) for x in freqs)
        if len(f) < 2:
            return
        n = len(f)
        self._bands = f
        self._gains = (list(self._gains) + [0.0] * n)[:n]
        self._bars = np.zeros(n, dtype=np.float32)
        self._recompute_bin_map()
        self.update()

    def set_gains(self, gains) -> None:
        g = [float(x) for x in gains]
        if len(g) != len(self._bands):
            g = (g + [0.0] * len(self._bands))[: len(self._bands)]
        self._gains = g
        self.update()

    def reset(self) -> None:
        self._gains = [0.0] * len(self._bands)
        self.update()

    def set_spectrum(self, values) -> None:
        """Recibe el espectro (dB, bins log 20Hz-20kHz) y lo agrega por banda."""
        if not values:
            self._bars *= np.float32(0.85)
            self.update()
            return
        arr = np.asarray(values, dtype=np.float32)
        n = min(arr.size, self.SPECTRUM_BINS)
        targets = np.full(len(self._bands), -120.0, dtype=np.float32)
        np.maximum.at(targets, self._bin_band[:n], arr[:n])
        norm = np.clip((targets + 60.0) / 60.0, 0.0, 1.0)
        # Amplificación visual: gamma < 1 levanta los niveles medios y la
        # ganancia 1.25 lleva los picos típicos (-15..-10 dB) cerca del tope.
        target_bars = np.minimum(1.0, np.power(norm, 0.65) * 1.25)
        self._bars += (target_bars - self._bars) * np.float32(0.35)
        self.update()

    # ---------- geometría ----------

    def _gain_to_y(self, gain, h, mt, mb):
        ratio = (gain - self.DB_MIN) / (self.DB_MAX - self.DB_MIN)
        return mt + (1.0 - ratio) * (h - mt - mb)

    def _y_to_gain(self, y, h, mt, mb):
        ratio = 1.0 - (y - mt) / (h - mt - mb)
        return self.DB_MIN + ratio * (self.DB_MAX - self.DB_MIN)

    def _freq_to_x(self, freq, w, ml, mr):
        lo, hi = math.log10(self.FREQ_MIN), math.log10(self.FREQ_MAX)
        f = min(max(float(freq), self.FREQ_MIN), self.FREQ_MAX)
        t = (math.log10(f) - lo) / (hi - lo)
        return ml + 20 + (w - ml - mr - 40) * t

    def _x_to_freq(self, x, w, ml, mr):
        lo, hi = math.log10(self.FREQ_MIN), math.log10(self.FREQ_MAX)
        t = (x - ml - 20) / max(1.0, (w - ml - mr - 40))
        t = min(max(t, 0.0), 1.0)
        return 10.0 ** (lo + t * (hi - lo))

    def _band_to_x(self, i, w, ml, mr):
        return self._freq_to_x(self._bands[i], w, ml, mr)

    def _x_to_nearest_band(self, x, w, ml, mr):
        """Banda más cercana a ``x`` (SIEMPRE devuelve una banda).

        Antes había un umbral de 25 px que dejaba zonas muertas: con el eje X
        logarítmico los huecos entre puntos son irregulares (hasta ~150 px con
        5 bandas), así que muchos clics/arrastres no hacían nada ("algunas
        bandas no funcionan"). El hover ya resalta qué banda se agarraría."""
        best, best_d = 0, float("inf")
        for i in range(len(self._bands)):
            d = abs(x - self._band_to_x(i, w, ml, mr))
            if d < best_d:
                best_d, best = d, i
        return best

    @staticmethod
    def _freq_label(freq: float) -> str:
        if freq >= 1000:
            return f"{freq / 1000:.1f}k".replace(".0k", "k")
        return str(int(freq))

    # ---------- pintura ----------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        ml, mr, mt, mb = 44, 12, 16, 24
        n = len(self._bands)

        p.fillRect(self.rect(), QColor(Theme.SPECTRUM_BG))

        # --- Barras de espectro por banda (detrás de todo lo demás) ---
        bar_area_h = (h - mt - mb) * 0.94
        baseline = h - mb
        ramp = [Theme.SPECTRUM_BAR_LOW, Theme.SPECTRUM_BAR_MID, Theme.SPECTRUM_BAR_HIGH]
        span = max(1.0, (w - ml - mr - 40))
        bar_w = max(4.0, min(76.0, span / n * 0.62))
        for i in range(n):
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

        # Etiquetas de frecuencia: todas si hay pocas bandas; si no, cada 5ª y
        # siempre la que está bajo el cursor.
        p.setPen(QColor(Theme.SPECTRUM_LABEL))
        step = 1 if n <= 10 else (5 if n <= 15 else 5)
        for i, freq in enumerate(self._bands):
            if n > 10 and i % step != 0 and i not in (n - 1, self._hover, self._dragging):
                continue
            x = self._band_to_x(i, w, ml, mr)
            p.drawText(int(x) - 20, int(h - mb + 4), 40, 14, Qt.AlignmentFlag.AlignCenter, self._freq_label(freq))

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
                text = f"{self._freq_label(self._bands[i])} · {self._gains[i]:+.1f} dB"
                p.setPen(QColor(Theme.TEXT))
                p.setFont(QFont(Theme.FONT_FAMILY, 9))
                p.drawText(int(pt.x() + 12), int(pt.y() - 8), text)

        p.setPen(QPen(QColor(Theme.BORDER_SOLID), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(ml, mt, w - ml - mr, h - mt - mb), 2, 2)
        p.end()

    # ---------- interacción ----------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            ml, mr, mt, mb = 44, 12, 16, 24
            band = self._x_to_nearest_band(event.position().x(), self.width(), ml, mr)
            if band >= 0:
                self._dragging = band
                self._press_x = event.position().x()
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
            # Frecuencia: solo si el movimiento horizontal es claro (>4 px),
            # para que un arrastre vertical no mueva la banda por accidente.
            if abs(event.position().x() - self._press_x) > 4:
                freq = self._x_to_freq(event.position().x(), self.width(), ml, mr)
                self.freq_changed.emit(self._dragging, freq)
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
    """Página de ecualizador con curva interactiva y bandas configurables."""

    band_count_selected = Signal(int)
    freq_selected = Signal(int, float)
    q_selected = Signal(float)

    def __init__(self, state: AudioState, t=None, parent=None) -> None:
        super().__init__(parent)
        self._state = state
        self._t = t or (lambda text: text)
        self._build()
        # Barras de espectro en el EQ: mismo flujo de datos que la página Inicio
        self._state.spectrum_changed.connect(self._on_spectrum)

    def _on_spectrum(self, values) -> None:
        # Sólo cuando la página está a la vista (ahorro de GIL en el hilo de UI).
        if self.isVisible():
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

        # Controles (Fase 5): número de bandas y ancho de campana (Q global).
        ctrl = QHBoxLayout()
        ctrl.setSpacing(Theme.SPACING_MD)
        lbl_bands = QLabel(self._t("Bandas"))
        lbl_bands.setStyleSheet(f"color: {Theme.TEXT_MUTED}; background: transparent;")
        ctrl.addWidget(lbl_bands)
        self._bands_combo = QComboBox()
        for count in sorted(_EQ_BAND_SETS):
            self._bands_combo.addItem(str(count), count)
        self._bands_combo.setCurrentIndex(sorted(_EQ_BAND_SETS).index(9))
        self._bands_combo.currentIndexChanged.connect(self._on_band_count)
        ctrl.addWidget(self._bands_combo)
        ctrl.addSpacing(Theme.SPACING_LG)
        lbl_q = QLabel(self._t("Ancho (Q)"))
        lbl_q.setStyleSheet(f"color: {Theme.TEXT_MUTED}; background: transparent;")
        ctrl.addWidget(lbl_q)
        self._q_slider = QSlider(Qt.Orientation.Horizontal)
        self._q_slider.setRange(20, 1000)  # 0.20 .. 10.00
        self._q_slider.setValue(140)
        self._q_slider.valueChanged.connect(self._on_q)
        ctrl.addWidget(self._q_slider, 1)
        self._q_label = QLabel("1.40")
        self._q_label.setFixedWidth(44)
        self._q_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._q_label.setStyleSheet(f"color: {Theme.TEXT}; background: transparent;")
        ctrl.addWidget(self._q_label)
        layout.addLayout(ctrl)

        self._eq_curve = EQCurveWidget()
        self._eq_curve.band_changed.connect(self._on_band_changed)
        self._eq_curve.freq_changed.connect(self._on_freq_changed)
        layout.addWidget(self._eq_curve, 1)

        btn_row = QHBoxLayout()
        self._reset_btn = QPushButton(self._t("Restablecer todo"))
        # Anchura MÍNIMA, no fija: "Restablecer todo" no cabe en 100 px y el
        # botón debe crecer con el texto (en inglés "Reset all" es más corto).
        self._reset_btn.setMinimumWidth(100)
        self._reset_btn.clicked.connect(self._reset_all)
        btn_row.addWidget(self._reset_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _on_band_count(self, index: int) -> None:
        self.band_count_selected.emit(int(self._bands_combo.itemData(index) or 9))

    def _on_q(self, raw: int) -> None:
        q = raw / 100.0
        self._q_label.setText(f"{q:.2f}")
        self.q_selected.emit(q)

    def _on_band_changed(self, index: int, gain: float) -> None:
        # Asignar la lista COMPLETA (no eq_gains[i] = gain): la mutación in
        # place se salta el property setter y eq_changed nunca se emite, por lo
        # que el DSP no se enteraría del cambio.
        gains = list(self._state.eq_gains)
        if index >= len(gains):
            gains = (gains + [0.0] * (index + 1))[: index + 1]
        gains[index] = gain
        self._state.eq_gains = gains

    def _on_freq_changed(self, index: int, freq: float) -> None:
        self.freq_selected.emit(index, freq)

    def _reset_all(self) -> None:
        self._eq_curve.reset()
        self._state.eq_gains = [0.0] * len(self._state.eq_gains)

    def set_gains(self, gains: list[float]) -> None:
        self._eq_curve.set_gains(gains)

    def set_bands(self, freqs: list[float]) -> None:
        self._eq_curve.set_bands(freqs)
        idx = self._bands_combo.findData(len(freqs))
        if idx >= 0:
            self._bands_combo.blockSignals(True)
            self._bands_combo.setCurrentIndex(idx)
            self._bands_combo.blockSignals(False)

    def set_q(self, q: float) -> None:
        self._q_slider.blockSignals(True)
        self._q_slider.setValue(int(round(float(q) * 100)))
        self._q_slider.blockSignals(False)
        self._q_label.setText(f"{float(q):.2f}")
