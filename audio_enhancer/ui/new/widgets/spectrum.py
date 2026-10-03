"""Analizador de espectro profesional con QPainter.

Recibe datos de espectro y los dibuja. No hace FFT ni DSP.
Incluye escala dB, etiquetas de frecuencia, indicador de pico y grilla.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QWidget

from ..theme.colors import Theme, numeric_font

# Frecuencias de las etiquetas del eje X (el texto se deriva: >=1000 -> "1k")
_FREQ_LABELS = [31, 60, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]

# Rango dB para la visualizacion
_DB_MIN = -60.0
_DB_MAX = 0.0

# Tiempo de caida del pico (en actualizaciones)
_PEAK_DECAY = 0.92
_PEAK_HOLD = 12  # frames antes de empezar a caer


def _freq_to_x(freq: float, width: float, margin_left: float, margin_right: float) -> float:
    """Convierte frecuencia a posicion X logaritmica."""
    f_min = math.log10(20)
    f_max = math.log10(20000)
    if freq <= 20:
        return margin_left
    if freq >= 20000:
        return width - margin_right
    ratio = (math.log10(freq) - f_min) / (f_max - f_min)
    return margin_left + ratio * (width - margin_left - margin_right)


def _db_to_y(db: float, height: float, margin_top: float, margin_bottom: float) -> float:
    """Convierte dB a posicion Y."""
    ratio = (db - _DB_MIN) / (_DB_MAX - _DB_MIN)
    ratio = max(0.0, min(1.0, ratio))
    return margin_top + (1.0 - ratio) * (height - margin_top - margin_bottom)


class SpectrumWidget(QWidget):
    """Visualizador de espectro con barras, escala dB, frecuencias y picos.

    Recibe datos via set_spectrum(). Pinta con QPainter unicamente.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Arrays numpy: set_spectrum es vectorizado (antes un bucle Python de 64
        # iteraciones a 30 Hz que, en el hilo de UI, competía por el GIL con el
        # callback de audio y dañaba el sonido mientras el espectro estaba a la
        # vista).
        self._spectrum = np.zeros(0, dtype=np.float32)
        self._peaks = np.zeros(0, dtype=np.float32)
        self._peak_hold = np.zeros(0, dtype=np.int32)
        self._smooth = np.zeros(0, dtype=np.float32)
        self.setMinimumHeight(140)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def set_spectrum(self, values: list[float] | None) -> None:
        """Recibe nuevos datos de espectro y dispara repintado (vectorizado)."""
        if values is None:
            self._spectrum = np.zeros(0, dtype=np.float32)
            self._peaks = np.zeros(0, dtype=np.float32)
            self._peak_hold = np.zeros(0, dtype=np.int32)
            self._smooth = np.zeros(0, dtype=np.float32)
            self.update()
            return
        arr = np.asarray(values, dtype=np.float32)
        n = arr.size
        if self._peaks.size != n:
            self._peaks = np.full(n, _DB_MIN, dtype=np.float32)
            self._peak_hold = np.zeros(n, dtype=np.int32)
            self._smooth = np.full(n, _DB_MIN, dtype=np.float32)
        self._spectrum = arr
        # Picos (hold/decay) y suavizado, todo vectorizado.
        up = arr > self._peaks
        self._peaks[up] = arr[up]
        self._peak_hold[up] = _PEAK_HOLD
        down = ~up
        self._peak_hold[down] = np.maximum(self._peak_hold[down] - 1, 0)
        decay = down & (self._peak_hold <= 0)
        self._peaks[decay] *= _PEAK_DECAY
        np.maximum(self._peaks, arr, out=self._peaks)
        self._smooth += (arr - self._smooth) * np.float32(0.4)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        # Sin antialiasing: las barras son rectángulos y el AA de 64 barras por
        # frame a 30 Hz era coste puro. Tampoco se crea un QLinearGradient por
        # barra (64 gradientes/frame): relleno sólido reutilizando 3 QColor.
        p = QPainter(self)
        w = self.width()
        h = self.height()

        # Margenes
        ml = 36  # izquierda para dB
        mr = 8
        mt = 8
        mb = 20  # abajo para frecuencias

        # Fondo
        p.fillRect(self.rect(), QColor(Theme.SPECTRUM_BG))

        # Grilla horizontal (lineas dB)
        grid_pen = QPen(QColor(Theme.SPECTRUM_GRID), 1, Qt.PenStyle.DotLine)
        p.setPen(grid_pen)
        for db in (-48, -36, -24, -12, 0):
            y = _db_to_y(db, h, mt, mb)
            p.drawLine(int(ml), int(y), int(w - mr), int(y))

        # Etiquetas dB
        label_color = QColor(Theme.SPECTRUM_LABEL)
        p.setPen(label_color)
        p.setFont(numeric_font(8))
        for db in (-48, -36, -24, -12, 0):
            y = _db_to_y(db, h, mt, mb)
            label = f"{db}" if db < 0 else "0"
            p.drawText(2, int(y - 6), ml - 6, 12, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

        # Etiquetas de frecuencia + lineas verticales
        for freq in _FREQ_LABELS:
            x = _freq_to_x(freq, w, ml, mr)
            p.setPen(grid_pen)
            p.drawLine(int(x), int(mt), int(x), int(h - mb))
            p.setPen(label_color)
            label = f"{freq // 1000}k" if freq >= 1000 else str(freq)
            p.drawText(int(x) - 12, int(h - mb + 4), 24, 14, Qt.AlignmentFlag.AlignCenter, label)

        # Barras de espectro
        n = len(self._smooth)
        if not n:
            p.setPen(QPen(QColor(Theme.BORDER_SOLID), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(ml, mt, w - ml - mr, h - mt - mb), 2, 2)
            p.end()
            return

        bar_area_w = w - ml - mr
        bar_w = max(1.0, bar_area_w / n - 1)
        gap = 1
        y_floor = _db_to_y(_DB_MIN, h, mt, mb)
        c_low = QColor(Theme.SPECTRUM_BAR_LOW)
        c_mid = QColor(Theme.SPECTRUM_BAR_MID)
        c_high = QColor(Theme.SPECTRUM_BAR_HIGH)
        c_peak = QColor(Theme.SPECTRUM_BAR_PEAK)
        smooth = self._smooth
        peaks = self._peaks
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(n):
            x = ml + i * (bar_w + gap)
            if x + bar_w > w - mr:
                break
            db_val = smooth[i]
            color = c_high if db_val > -8 else c_mid if db_val > -25 else c_low
            bar_top = _db_to_y(db_val, h, mt, mb)
            bar_h = y_floor - bar_top
            if bar_h > 0:
                p.setBrush(color)
                p.drawRect(QRectF(x, bar_top, bar_w, bar_h))
            if i < len(peaks):
                peak_db = peaks[i]
                if peak_db > _DB_MIN + 2:
                    peak_y = _db_to_y(peak_db, h, mt, mb)
                    p.setPen(QPen(c_peak if peak_db > -8 else color, 2))
                    p.drawLine(int(x), int(peak_y), int(x + bar_w), int(peak_y))
                    p.setPen(Qt.PenStyle.NoPen)

        # Borde sutil
        p.setPen(QPen(QColor(Theme.BORDER_SOLID), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(ml, mt, w - ml - mr, h - mt - mb), 2, 2)

        p.end()
