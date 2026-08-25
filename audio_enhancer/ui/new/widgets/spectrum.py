"""Analizador de espectro profesional con QPainter.

Spectrum analizador de espectro profesional con QPainter.

Recibe datos de espectro y los dibuja. No hace FFT ni DSP.
Incluye escala dB, etiquetas de frecuencia, indicador de pico y grilla.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QWidget

from ..theme.colors import Theme, numeric_font

# Frecuencias de las etiquetas del eje X
_FREQ_LABELS = [31, 60, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
_FREQ_SHORT = {
    31: "31",
    60: "60",
    125: "125",
    250: "250",
    500: "500",
    1000: "1k",
    2000: "2k",
    4000: "4k",
    8000: "8k",
    16000: "16k",
}

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
        self._spectrum: list[float] = []
        self._peaks: list[float] = []
        self._peak_hold: list[int] = []
        self._smooth: list[float] = []
        self._bar_count = 64
        self.setMinimumHeight(140)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def set_spectrum(self, values: list[float] | None) -> None:
        """Recibe nuevos datos de espectro y dispara repintado."""
        if values is None:
            self._spectrum = []
            self._peaks = []
            self._peak_hold = []
            self._smooth = []
            self.update()
            return
        self._spectrum = [float(v) for v in values]
        n = len(self._spectrum)
        # Inicializar o ajustar picos
        while len(self._peaks) < n:
            self._peaks.append(_DB_MIN)
            self._peak_hold.append(0)
        while len(self._smooth) < n:
            self._smooth.append(_DB_MIN)
        # Actualizar picos
        for i in range(min(n, len(self._peaks))):
            val = self._spectrum[i]
            if val > self._peaks[i]:
                self._peaks[i] = val
                self._peak_hold[i] = _PEAK_HOLD
            else:
                if self._peak_hold[i] > 0:
                    self._peak_hold[i] -= 1
                else:
                    self._peaks[i] *= _PEAK_DECAY
                    if self._peaks[i] < val:
                        self._peaks[i] = val
            # Suavizado para animacion fluida
            self._smooth[i] += (val - self._smooth[i]) * 0.4
        self._bar_count = n
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
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
        p.setPen(QPen(QColor(Theme.SPECTRUM_GRID), 1, Qt.PenStyle.DotLine))
        for db in [-48, -36, -24, -12, 0]:
            y = _db_to_y(db, h, mt, mb)
            p.drawLine(int(ml), int(y), int(w - mr), int(y))

        # Etiquetas dB
        p.setPen(QColor(Theme.SPECTRUM_LABEL))
        font = numeric_font(8)
        p.setFont(font)
        for db in [-48, -36, -24, -12, 0]:
            y = _db_to_y(db, h, mt, mb)
            label = f"{db}" if db < 0 else "0"
            p.drawText(2, int(y - 6), ml - 6, 12, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

        # Etiquetas de frecuencia
        freq_font = numeric_font(8)
        p.setFont(freq_font)
        p.setPen(QColor(Theme.SPECTRUM_LABEL))
        for freq in _FREQ_LABELS:
            x = _freq_to_x(freq, w, ml, mr)
            label = _FREQ_SHORT.get(freq, str(freq))
            p.drawText(int(x) - 12, int(h - mb + 4), 24, 14, Qt.AlignmentFlag.AlignCenter, label)
            # Linea vertical sutil
            p.setPen(QPen(QColor(Theme.SPECTRUM_GRID), 1, Qt.PenStyle.DotLine))
            p.drawLine(int(x), int(mt), int(x), int(h - mb))
            p.setPen(QColor(Theme.SPECTRUM_LABEL))

        # Barras de espectro
        if not self._smooth:
            p.end()
            return

        n = len(self._smooth)
        bar_area_w = w - ml - mr
        bar_w = max(1, bar_area_w / n - 1)
        gap = 1

        for i in range(n):
            db_val = self._smooth[i]
            peak_db = self._peaks[i] if i < len(self._peaks) else _DB_MIN

            x = ml + i * (bar_w + gap)
            if x + bar_w > w - mr:
                break

            # Color segun nivel
            if db_val > -8:
                color = QColor(Theme.SPECTRUM_BAR_HIGH)
            elif db_val > -25:
                color = QColor(Theme.SPECTRUM_BAR_MID)
            else:
                color = QColor(Theme.SPECTRUM_BAR_LOW)

            # Altura de la barra
            bar_top = _db_to_y(db_val, h, mt, mb)
            bar_bottom = _db_to_y(_DB_MIN, h, mt, mb)
            bar_h = bar_bottom - bar_top

            if bar_h > 0:
                # Gradiente sutil
                grad = QLinearGradient(x, bar_top, x, bar_bottom)
                grad.setColorAt(0, color)
                darker = QColor(color)
                darker.setAlpha(80)
                grad.setColorAt(1, darker)
                p.setBrush(QBrush(grad))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRectF(x, bar_top, bar_w, bar_h), 1, 1)

            # Indicador de pico
            if peak_db > _DB_MIN + 2:
                peak_y = _db_to_y(peak_db, h, mt, mb)
                if peak_db > -8:
                    peak_color = QColor(Theme.SPECTRUM_BAR_PEAK)
                elif peak_db > -25:
                    peak_color = QColor(Theme.SPECTRUM_BAR_HIGH)
                else:
                    peak_color = QColor(Theme.SPECTRUM_BAR_MID)
                p.setPen(QPen(peak_color, 2))
                p.drawLine(int(x), int(peak_y), int(x + bar_w), int(peak_y))

        # Borde sutil
        p.setPen(QPen(QColor(Theme.BORDER_SOLID), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(ml, mt, w - ml - mr, h - mt - mb), 2, 2)

        p.end()
