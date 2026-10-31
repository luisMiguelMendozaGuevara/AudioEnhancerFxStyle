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
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....dsp import EQ_BAND_SETS as _EQ_BAND_SETS
from ....dsp import EQ_BANDS as _DSP_EQ_BANDS
from ....dsp import pchip_interp
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
        if len(self._gains) != n:
            # El motor (fuente de verdad) remapea las ganancias por frecuencia
            # y main_window manda primero las bandas y después las ganancias
            # exactas; si llega un cambio de cuenta suelto se rellena/recorta
            # solo para no romper el dibujo.
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

    def _band_t(self) -> np.ndarray:
        """Posicion normalizada (0..1, log-f) de cada banda en el eje X."""
        log_lo, log_hi = math.log10(self.FREQ_MIN), math.log10(self.FREQ_MAX)
        bands = np.asarray(self._bands, dtype=np.float64)
        return (np.log10(bands) - log_lo) / (log_hi - log_lo)

    def curve_samples(self, n: int = 160) -> tuple[np.ndarray, np.ndarray]:
        """Curva dibujada completa: t 0..1 (log-f) y ganancia (dB).

        Interpola las bandas con PCHIP (pasa exactamente por los puntos, sin
        ondular ni sobreoscilar) y extiende plano hasta los bordes del grafico:
        la curva y el relleno cubren todo el ancho con cualquier numero de
        bandas (antes empezaban en la primera/ultima banda)."""
        tx = np.concatenate(([0.0], self._band_t(), [1.0]))
        gy = np.concatenate(([self._gains[0]], self._gains, [self._gains[-1]]))
        keep = np.concatenate(([True], np.diff(tx) > 1e-9))
        samples = np.linspace(0.0, 1.0, n)
        return samples, pchip_interp(tx[keep], gy[keep], samples)

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

        # Curva completa (borde a borde) interpolando las bandas con PCHIP:
        # pasa exactamente por los puntos y no ondula entre ellos; con pocas
        # bandas ya no se veian ondas en S y el relleno cubre todo el ancho.
        ts, gs = self.curve_samples()
        plot_w = max(1.0, w - ml - mr - 40)
        x0 = ml + 20
        line_path = QPainterPath()
        for i, (t, g) in enumerate(zip(ts, gs, strict=False)):
            x = x0 + plot_w * float(t)
            y = self._gain_to_y(float(g), h, mt, mb)
            if i == 0:
                line_path.moveTo(x, y)
            else:
                line_path.lineTo(x, y)

        fill_path = QPainterPath(line_path)
        fill_path.lineTo(x0 + plot_w, zero_y)
        fill_path.lineTo(x0, zero_y)
        fill_path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(accent_subtle_color(13)))
        p.drawPath(fill_path)

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


class MetricsPanel(QFrame):
    #: El usuario enciende o apaga la medición de pico real (cuesta ~3 % de CPU).
    medir_pico_real_changed = Signal(bool)
    #: El usuario pide poner a cero el pico máximo medido.
    reiniciar_maximo_clicked = Signal()

    """Métricas en vivo del motor, todas en una sola vista.

    Antes vivían en la barra inferior, donde solo cabía una línea de texto y se
    cortaba con nombres de dispositivo largos. Aquí se muestran como campos
    independientes, cada uno con su unidad, en una rejilla de tres por tres
    ordenada por tema (columna = cortes / tiempos / nivel / picos):

    - Underruns: veces que la salida se quedó sin datos (cortes).
    - Huecos: bloques en los que hubo que insertar silencio.
    - Deriva: frames ajustados por el resampler para compensar los relojes.
    - Latencia: suma de etapas del pipeline (captura + ring + cola + salida).
    - Objetivo: consigna de llenado del ring (la preferencia de latencia).
    - DSP p99: percentil 99 del coste de `process()` por bloque (vs 21,3 ms).
    - LUFS: sonoridad momentánea de la salida (ITU-R BS.1770).
    - LRA: rango de sonoridad (lo que varía el nivel dentro del material).
    - Frecuencia: frecuencia de muestreo del ring (antes en la barra inferior).
    - Pico: pico de muestra de la salida (dBFS).
    - True peak: pico real entre muestras (dBTP, ITU-R BS.1770-4 anexo 2). La
      diferencia con el pico de muestra es lo que un limitador de muestras no ve.
    - Reducción: cuánto está bajando el limitador (dB).

    Las filas van: cortes, tiempos, nivel. Es de solo lectura: no toca el DSP,
    solo pinta números que ya calcula el motor.
    """

    # Filas de la rejilla (3x3). Cada campo: (clave interna, etiqueta, formato).
    _FILAS = (
        (
            ("underruns", "Underruns", "%d"),
            ("huecos", "Huecos", "%d"),
            ("latencia", "Latencia", "%.0f ms"),
            ("lufs", "LUFS", "%.1f"),
            ("pico", "Pico", "%.1f dBFS"),
        ),
        (
            ("deriva", "Deriva", "%d fr"),
            ("objetivo", "Objetivo", "%.0f ms"),
            ("dsp_p99", "DSP p99", "%.1f ms"),
            ("lra", "LRA", "%.1f LU"),
            ("true_peak", "True peak", "%+.1f dBTP"),
        ),
        (
            ("frecuencia", "Frecuencia", "%s"),
            ("reduccion", "Reducción", "%.1f dB"),
            ("pico_max", "Máximo", "%+.1f dBTP"),
            ("diferencia", "Entre muestras", "%.1f dB"),
        ),
    )
    _CAMPOS = tuple(campo for fila in _FILAS for campo in fila)
    _VACIO = "--"
    # Umbral de aviso del coste del DSP: la mitad del bloque de 21,3 ms. A partir
    # de ahí un pico de CPU empieza a acercarse a los 21,3 ms que dura el bloque.
    _DSP_P99_AVISO_MS = 10.66
    #: Un pico real por encima de 0 dBTP recorta en el convertidor del altavoz.
    _TRUE_PEAK_AVISO_DBTP = 0.0

    def __init__(self, t=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._t = t or (lambda text: text)
        # objectName "card": el QSS global lo estiliza y sigue el tema activo.
        self.setObjectName("card")
        self._valores: dict[str, QLabel] = {}
        self._build()

    def _build(self) -> None:
        grid = QGridLayout(self)
        grid.setContentsMargins(Theme.SPACING_MD, Theme.SPACING_MD, Theme.SPACING_MD, Theme.SPACING_MD)
        grid.setHorizontalSpacing(Theme.SPACING_LG)
        grid.setVerticalSpacing(Theme.SPACING_XS)
        for indice_fila, fila in enumerate(self._FILAS):
            for columna, (clave, etiqueta, _formato) in enumerate(fila):
                titulo = QLabel(self._t(etiqueta))
                titulo.setStyleSheet(
                    f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;"
                )
                valor = QLabel(self._VACIO)
                valor.setStyleSheet(
                    f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_LG}px; "
                    f"font-weight: {Theme.FONT_WEIGHT_SEMIBOLD}; background: transparent;"
                )
                grid.addWidget(titulo, indice_fila * 2, columna)
                grid.addWidget(valor, indice_fila * 2 + 1, columna)
                self._valores[clave] = valor
        # Las tres columnas se reparten el ancho del panel por igual (antes
        # quedaban amontonadas a la izquierda y sobraba medio panel vacío).
        for columna in range(len(self._FILAS[0])):
            grid.setColumnStretch(columna, 1)
        # Interruptor de la medición: apagarla no cambia el audio, solo ahorra
        # CPU. Se dice el coste para que la decisión sea informada.
        self._medir_pico_real = QCheckBox(self._t("Medir pico real (~3 % CPU)"))
        self._medir_pico_real.setChecked(True)
        self._medir_pico_real.setToolTip(
            self._t("Mide el pico entre muestras (ITU-R BS.1770-4). Solo diagnóstico: apagarlo no cambia el sonido.")
        )
        self._medir_pico_real.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; background: transparent;"
        )
        self._medir_pico_real.toggled.connect(self.medir_pico_real_changed)
        grid.addWidget(self._medir_pico_real, len(self._FILAS) * 2, 0, 1, 2)
        # Botón para poner a cero el máximo: sin él, "Máximo" no sirve de nada.
        self._reset_picos = QPushButton(self._t("Reiniciar máximo"))
        self._reset_picos.setToolTip(self._t("Pone a cero el pico máximo medido."))
        self._reset_picos.setStyleSheet(
            f"color: {Theme.TEXT_MUTED}; font-size: {Theme.FONT_SIZE_XS}px; "
            f"background: transparent; border: 1px solid {Theme.BORDER}; "
            f"border-radius: {Theme.RADIUS_SM}px; padding: 2px 8px;"
        )
        self._reset_picos.clicked.connect(self.reiniciar_maximo_clicked)
        grid.addWidget(self._reset_picos, len(self._FILAS) * 2, 4)

    def set_metrics(self, **valores: float | None) -> None:
        """Actualiza los campos indicados; ``None`` o no finito deja ``--``.

        Acepta solo las claves de ``_CAMPOS`` (las demás se ignoran: así añadir
        una métrica nueva no rompe llamadores viejos).
        """
        for clave, _etiqueta, formato in self._CAMPOS:
            if clave not in valores:
                continue
            numero = valores[clave]
            etiqueta = self._valores[clave]
            try:
                valor = float(numero) if numero is not None else float("nan")
            except (TypeError, ValueError):
                valor = float("nan")
            if not math.isfinite(valor):
                etiqueta.setText(self._VACIO)
                continue
            if clave in ("lufs", "pico", "true_peak") and not (-100.0 < valor < 30.0):
                # -1000 = silencio o ventana incompleta (centinela del motor).
                etiqueta.setText(self._VACIO)
                continue
            if clave == "frecuencia":
                etiqueta.setText(self._texto_frecuencia(valor))
            else:
                etiqueta.setText(formato % valor)
            etiqueta.setStyleSheet(self._estilo(self._en_aviso(clave, valor)))

    @staticmethod
    def _texto_frecuencia(rate_hz: float) -> str:
        """48 kHz y no 48.0; 44.1 kHz con decimal cuando no es múltiplo de 1000."""
        if rate_hz <= 0:
            return MetricsPanel._VACIO
        if rate_hz >= 1000 and rate_hz % 1000 == 0:
            return "%d kHz" % round(rate_hz / 1000)
        if rate_hz >= 1000:
            return "%.1f kHz" % (rate_hz / 1000.0)
        return "%d Hz" % round(rate_hz)

    @classmethod
    def _en_aviso(cls, clave: str, valor: float) -> bool:
        """True si el valor indica un problema (cortes o DSP cerca del límite).

        Un contador de cortes a 0 es lo normal; en cuanto sube hay algo que
        mirar. Y un p99 por encima de la mitad del bloque avisa de que el DSP se
        está quedando sin margen (los cortes vendrían después).
        """
        if clave in ("underruns", "huecos"):
            return valor > 0
        if clave == "dsp_p99":
            return valor > cls._DSP_P99_AVISO_MS
        if clave == "true_peak":
            return valor > cls._TRUE_PEAK_AVISO_DBTP
        return False

    @staticmethod
    def _estilo(aviso: bool) -> str:
        color = Theme.WARNING if aviso else Theme.TEXT
        return (
            f"color: {color}; font-size: {Theme.FONT_SIZE_LG}px; "
            f"font-weight: {Theme.FONT_WEIGHT_SEMIBOLD}; background: transparent;"
        )

    def clear(self) -> None:
        """Vuelve a ``--`` (motor parado)."""
        for etiqueta in self._valores.values():
            etiqueta.setText(self._VACIO)
            etiqueta.setStyleSheet(self._estilo(False))

    def tiene_boton_de_reinicio(self) -> bool:
        """El botón de reinicio existe y está habilitado (para tests)."""
        return bool(self._reset_picos.isEnabled())

    def medir_pico_real_activo(self) -> bool:
        """Estado del interruptor (para tests e informes)."""
        return bool(self._medir_pico_real.isChecked())

    def set_medir_pico_real(self, activo: bool) -> None:
        """Cambia el interruptor sin re-emitir (sincronización programática)."""
        self._medir_pico_real.blockSignals(True)
        self._medir_pico_real.setChecked(bool(activo))
        self._medir_pico_real.blockSignals(False)

    def text_of(self, clave: str) -> str:
        """Texto visible de un campo (para tests y para el informe)."""
        etiqueta = self._valores.get(clave)
        return etiqueta.text() if etiqueta is not None else ""


class EqualizerPage(QWidget):
    """Página de ecualizador con curva interactiva y bandas configurables."""

    band_count_selected = Signal(int)
    freq_selected = Signal(int, float)
    #: El usuario apaga/enciende la medición de pico real (solo diagnóstico).
    true_peak_toggled = Signal(bool)
    reiniciar_picos = Signal()

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

        # Controles (Fase 5): numero de bandas. El Ancho (Q) vive en Efectos
        # (es un ajuste fino que casi no se percibe y cargaba esta pagina).
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
        ctrl.addStretch()
        layout.addLayout(ctrl)

        self._eq_curve = EQCurveWidget()
        self._eq_curve.band_changed.connect(self._on_band_changed)
        self._eq_curve.freq_changed.connect(self._on_freq_changed)
        layout.addWidget(self._eq_curve, 1)

        # Métricas del motor: viven aquí (antes en la barra inferior, donde solo
        # cabía una línea y se cortaba con rutas de dispositivo largas).
        self._metrics = MetricsPanel(self._t)
        self._metrics.medir_pico_real_changed.connect(self.true_peak_toggled)
        self._metrics.reiniciar_maximo_clicked.connect(self.reiniciar_picos)
        layout.addWidget(self._metrics)

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

    def set_metrics(self, **valores: float | None) -> None:
        """Publica las métricas en vivo del motor (ver ``MetricsPanel``)."""
        self._metrics.set_metrics(**valores)

    def clear_metrics(self) -> None:
        """Motor parado: todos los campos a ``--``."""
        self._metrics.clear()

    def set_medir_pico_real(self, activo: bool) -> None:
        """Sincroniza el interruptor sin re-emitir (al cargar la config)."""
        self._metrics.set_medir_pico_real(activo)

    def metric_text(self, clave: str) -> str:
        """Texto de un campo de métricas (para tests)."""
        return self._metrics.text_of(clave)
