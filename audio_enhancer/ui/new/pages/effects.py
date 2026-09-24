from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..audio_state import AudioState
from ..theme.colors import Theme


class EffectCard(QFrame):
    """Tarjeta de un efecto individual."""

    def __init__(self, title, min_val, max_val, default, unit="dB", has_toggle=True, tooltip="", parent=None) -> None:
        super().__init__(parent)
        self._unit = unit
        self._scale = 100
        # objectName "card": el QSS global lo estiliza y sigue el tema activo.
        self.setObjectName("card")
        # El tooltip se pone en la tarjeta: Qt lo muestra al pasar el mouse por
        # cualquier hijo sin tooltip propio (título, slider, valor).
        if tooltip:
            self.setToolTip(tooltip)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_MD, Theme.SPACING_LG, Theme.SPACING_MD)
        layout.setSpacing(Theme.SPACING_SM)
        self._layout = layout

        row_top = QHBoxLayout()
        lbl = QLabel(title)
        self._title_label = lbl
        lbl.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_MD}px; "
            f"font-weight: {Theme.FONT_WEIGHT_MEDIUM}; background: transparent;"
        )
        row_top.addWidget(lbl)
        row_top.addStretch()
        self._toggle = None
        if has_toggle:
            self._toggle = QCheckBox("OFF")
            self._toggle.setStyleSheet(
                f"QCheckBox {{ color: {Theme.TEXT_MUTED};"
                f" font-size: {Theme.FONT_SIZE_SM}px; background: transparent; }}"
            )
            row_top.addWidget(self._toggle)
        layout.addLayout(row_top)

        row_slider = QHBoxLayout()
        row_slider.setSpacing(Theme.SPACING_MD)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(int(min_val * self._scale), int(max_val * self._scale))
        self._slider.setValue(int(default * self._scale))
        row_slider.addWidget(self._slider, 1)
        self._value_label = QLabel()
        self._value_label.setFixedWidth(56)
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._value_label.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_SM}px; background: transparent;"
        )
        self._update_label(default)
        row_slider.addWidget(self._value_label)
        layout.addLayout(row_slider)
        self._slider.valueChanged.connect(self._on_value)

    def _update_label(self, value) -> None:
        if self._unit == "dB":
            self._value_label.setText(f"{value:+.1f} {self._unit}")
        else:
            self._value_label.setText(f"{value:.2f} {self._unit}")

    def _on_value(self, raw) -> None:
        self._update_label(raw / self._scale)

    def value(self) -> float:
        return self._slider.value() / self._scale

    def set_value(self, v) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(int(v * self._scale))
        self._slider.blockSignals(False)
        self._update_label(v)

    def is_enabled(self) -> bool:
        return self._toggle is None or self._toggle.isChecked()

    def set_enabled(self, v) -> None:
        if self._toggle is not None:
            self._toggle.blockSignals(True)
            self._toggle.setChecked(v)
            self._toggle.blockSignals(False)
            self._toggle.setText("ON" if v else "OFF")


class EffectsPage(QWidget):
    """Pagina de efectos: Bass, Treble, Compressor, Limiter, True-peak."""

    def __init__(self, state: AudioState, t=None, explain=None, parent=None) -> None:
        super().__init__(parent)
        self._state = state
        self._t = t or (lambda text: text)
        self._explain = explain or (lambda key: "")
        self._build()

    def _build(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(Theme.SPACING_LG, Theme.SPACING_LG, Theme.SPACING_LG, Theme.SPACING_LG)
        layout.setSpacing(Theme.SPACING_LG)

        title = QLabel(self._t("Efectos"))
        title.setStyleSheet(
            f"color: {Theme.TEXT}; font-size: {Theme.FONT_SIZE_XL}px; "
            f"font-weight: {Theme.FONT_WEIGHT_BOLD}; background: transparent;"
        )
        layout.addWidget(title)

        self._bass_card = EffectCard(
            self._t("Refuerzo de graves (dB)"), 0.0, 12.0, 0.0, "dB", tooltip=self._explain("bass")
        )
        self._bass_card._slider.valueChanged.connect(lambda v: self._on_bass(v / 100))
        if self._bass_card._toggle:
            self._bass_card._toggle.toggled.connect(self._on_bass_toggle)
        layout.addWidget(self._bass_card)

        self._treble_card = EffectCard(
            self._t("Refuerzo de agudos (dB)"), 0.0, 12.0, 0.0, "dB", tooltip=self._explain("treble")
        )
        self._treble_card._slider.valueChanged.connect(lambda v: self._on_treble(v / 100))
        if self._treble_card._toggle:
            self._treble_card._toggle.toggled.connect(self._on_treble_toggle)
        layout.addWidget(self._treble_card)

        self._limiter_card = EffectCard(
            self._t("Limitador suave"), 0.0, 1.0, 1.0, "", has_toggle=True, tooltip=self._explain("limiter")
        )
        self._limiter_card._slider.setEnabled(False)
        if self._limiter_card._toggle is not None:
            self._limiter_card._toggle.setChecked(True)
            self._limiter_card._toggle.toggled.connect(self._on_limiter_toggle)
        layout.addWidget(self._limiter_card)

        self._compressor_card = EffectCard(
            self._t("Compresor RMS"), 0.0, 1.0, 1.0, "", has_toggle=True, tooltip=self._explain("compressor")
        )
        self._compressor_card._slider.setEnabled(False)
        if self._compressor_card._toggle is not None:
            self._compressor_card._toggle.setChecked(True)
            self._compressor_card._toggle.toggled.connect(self._on_compressor_toggle)
        layout.addWidget(self._compressor_card)

        # True-peak: limita picos inter-muestra con sobremuestreo x4. Es lo más
        # caro del DSP; se puede apagar en equipos lentos.
        self._true_peak_card = EffectCard(
            self._t("True-peak (x4)"), 0.0, 1.0, 1.0, "", has_toggle=True, tooltip=self._explain("true_peak")
        )
        self._true_peak_card._slider.setEnabled(False)
        if self._true_peak_card._toggle is not None:
            self._true_peak_card._toggle.setChecked(True)
            self._true_peak_card._toggle.toggled.connect(self._on_true_peak_toggle)
        layout.addWidget(self._true_peak_card)

        # Techo de seguridad: con el limitador apagado evita el recorte duro
        # (reutiliza el limitador como limitador transparente a 0.99). Apagarlo
        # deja el único tope en el recorte a ±1.0.
        self._safety_card = EffectCard(
            self._t("Techo de seguridad"),
            0.0,
            1.0,
            1.0,
            "",
            has_toggle=True,
            tooltip=self._explain("safety_ceiling"),
        )
        self._safety_card._slider.setEnabled(False)
        if self._safety_card._toggle is not None:
            self._safety_card._toggle.setChecked(True)
            self._safety_card._toggle.toggled.connect(self._on_safety_ceiling_toggle)
        layout.addWidget(self._safety_card)

        # Recorte duro final (±1.0): guardia digital del último paso. Apagado,
        # la señal sale sin recortar y el recorte (si lo hay) lo hace el driver.
        self._final_clip_card = EffectCard(
            self._t("Recorte final (±1.0)"),
            0.0,
            1.0,
            1.0,
            "",
            has_toggle=True,
            tooltip=self._explain("final_clip"),
        )
        self._final_clip_card._slider.setEnabled(False)
        if self._final_clip_card._toggle is not None:
            self._final_clip_card._toggle.setChecked(True)
            self._final_clip_card._toggle.toggled.connect(self._on_final_clip_toggle)
        layout.addWidget(self._final_clip_card)

        # Crossfeed BS2B: imagen estéreo "fuera de la cabeza" para auriculares.
        # OFF por defecto (modifica la separación estéreo: no es universal).
        self._crossfeed_card = EffectCard(
            self._t("Crossfeed (auriculares)"),
            0.0,
            1.0,
            1.0,
            "",
            has_toggle=True,
            tooltip=self._explain("crossfeed"),
        )
        self._crossfeed_card._slider.setEnabled(False)
        preset_row = QHBoxLayout()
        preset_lbl = QLabel(self._t("Perfil"))
        preset_lbl.setStyleSheet(f"color: {Theme.TEXT_MUTED}; background: transparent;")
        preset_row.addWidget(preset_lbl)
        self._crossfeed_combo = QComboBox()
        for name in ("Natural", "Moderate", "Strong", "Custom"):
            label = self._t("Avanzado") if name == "Custom" else self._t(name)
            self._crossfeed_combo.addItem(label, name)
        self._crossfeed_combo.currentIndexChanged.connect(self._on_crossfeed_preset)
        preset_row.addWidget(self._crossfeed_combo, 1)
        self._crossfeed_card._layout.addLayout(preset_row)

        # Modo Avanzado (preset "Custom"): frecuencia y nivel de cruce, con los
        # rangos válidos de libbs2b (300-2000 Hz, 1-15 dB).
        adv_row = QHBoxLayout()
        lbl_cut = QLabel(self._t("Frec."))
        lbl_cut.setStyleSheet(f"color: {Theme.TEXT_MUTED}; background: transparent;")
        adv_row.addWidget(lbl_cut)
        self._cf_cut_slider = QSlider(Qt.Orientation.Horizontal)
        self._cf_cut_slider.setRange(300, 2000)
        self._cf_cut_slider.setValue(700)
        self._cf_cut_slider.valueChanged.connect(self._on_cf_advanced)
        adv_row.addWidget(self._cf_cut_slider, 1)
        self._cf_cut_label = QLabel("700 Hz")
        self._cf_cut_label.setFixedWidth(64)
        self._cf_cut_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._cf_cut_label.setStyleSheet(f"color: {Theme.TEXT}; background: transparent;")
        adv_row.addWidget(self._cf_cut_label)
        self._crossfeed_card._layout.addLayout(adv_row)

        adv_row2 = QHBoxLayout()
        lbl_feed = QLabel(self._t("Nivel"))
        lbl_feed.setStyleSheet(f"color: {Theme.TEXT_MUTED}; background: transparent;")
        adv_row2.addWidget(lbl_feed)
        self._cf_feed_slider = QSlider(Qt.Orientation.Horizontal)
        self._cf_feed_slider.setRange(10, 150)  # 1.0-15.0 dB en décimas
        self._cf_feed_slider.setValue(45)
        self._cf_feed_slider.valueChanged.connect(self._on_cf_advanced)
        adv_row2.addWidget(self._cf_feed_slider, 1)
        self._cf_feed_label = QLabel("4.5 dB")
        self._cf_feed_label.setFixedWidth(64)
        self._cf_feed_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._cf_feed_label.setStyleSheet(f"color: {Theme.TEXT}; background: transparent;")
        adv_row2.addWidget(self._cf_feed_label)
        self._crossfeed_card._layout.addLayout(adv_row2)
        # Los sliders solo se habilitan en el perfil Avanzado.
        self._set_cf_advanced_enabled(False)

        if self._crossfeed_card._toggle is not None:
            self._crossfeed_card._toggle.setChecked(False)  # OFF por defecto
            self._crossfeed_card._toggle.setText("OFF")
            self._crossfeed_card._toggle.toggled.connect(self._on_crossfeed_toggle)
        layout.addWidget(self._crossfeed_card)

        layout.addStretch()
        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _on_bass(self, v) -> None:
        self._state.bass = v

    def _on_treble(self, v) -> None:
        self._state.treble = v

    def _on_bass_toggle(self, on) -> None:
        if self._bass_card._toggle is not None:
            self._bass_card._toggle.setText("ON" if on else "OFF")
        self._state.bass = self._bass_card.value() if on else 0.0

    def _on_treble_toggle(self, on) -> None:
        if self._treble_card._toggle is not None:
            self._treble_card._toggle.setText("ON" if on else "OFF")
        self._state.treble = self._treble_card.value() if on else 0.0

    def _on_limiter_toggle(self, on) -> None:
        if self._limiter_card._toggle is not None:
            self._limiter_card._toggle.setText("ON" if on else "OFF")
        self._state.limiter = on

    def _on_compressor_toggle(self, on) -> None:
        if self._compressor_card._toggle is not None:
            self._compressor_card._toggle.setText("ON" if on else "OFF")
        self._state.compressor = on

    def _on_true_peak_toggle(self, on) -> None:
        if self._true_peak_card._toggle is not None:
            self._true_peak_card._toggle.setText("ON" if on else "OFF")
        self._state.true_peak = on

    def _on_safety_ceiling_toggle(self, on) -> None:
        if self._safety_card._toggle is not None:
            self._safety_card._toggle.setText("ON" if on else "OFF")
        self._state.safety_ceiling = on

    def _on_final_clip_toggle(self, on) -> None:
        if self._final_clip_card._toggle is not None:
            self._final_clip_card._toggle.setText("ON" if on else "OFF")
        self._state.final_clip = on

    def _on_crossfeed_toggle(self, on) -> None:
        if self._crossfeed_card._toggle is not None:
            self._crossfeed_card._toggle.setText("ON" if on else "OFF")
        self._state.crossfeed = on

    def _on_crossfeed_preset(self, index: int) -> None:
        name = str(self._crossfeed_combo.itemData(index) or "Natural")
        self._set_cf_advanced_enabled(name == "Custom")
        self._state.crossfeed_preset = name

    def _set_cf_advanced_enabled(self, on: bool) -> None:
        self._cf_cut_slider.setEnabled(on)
        self._cf_feed_slider.setEnabled(on)

    def _on_cf_advanced(self) -> None:
        cut = self._cf_cut_slider.value()
        feed = self._cf_feed_slider.value() / 10.0
        self._cf_cut_label.setText(f"{cut} Hz")
        self._cf_feed_label.setText(f"{feed:.1f} dB")
        # Solo tiene efecto si el perfil es Avanzado; se emite igual (la ventana
        # reenvía a state, y state solo aplica crossfeed_cut_hz/feed_db).
        self._state.crossfeed_cut_hz = cut
        self._state.crossfeed_feed_db = feed

    def set_bass(self, v) -> None:
        self._bass_card.set_value(v)

    def set_treble(self, v) -> None:
        self._treble_card.set_value(v)

    def set_limiter(self, on) -> None:
        self._limiter_card.set_enabled(on)

    def set_compressor(self, on) -> None:
        self._compressor_card.set_enabled(on)

    def set_true_peak(self, on) -> None:
        self._true_peak_card.set_enabled(on)

    def set_safety_ceiling(self, on) -> None:
        self._safety_card.set_enabled(on)

    def set_final_clip(self, on) -> None:
        self._final_clip_card.set_enabled(on)

    def set_crossfeed(self, on, preset: str = "Natural", cut_hz: int = 700, feed_db: float = 4.5) -> None:
        self._crossfeed_card.set_enabled(on)
        idx = self._crossfeed_combo.findData(preset)
        if idx >= 0:
            self._crossfeed_combo.blockSignals(True)
            self._crossfeed_combo.setCurrentIndex(idx)
            self._crossfeed_combo.blockSignals(False)
        # Reflejar los valores del modo Avanzado sin re-emitir señales.
        for slider, label, value, fmt in (
            (self._cf_cut_slider, self._cf_cut_label, int(cut_hz), "{:d} Hz"),
            (self._cf_feed_slider, self._cf_feed_label, int(round(feed_db * 10)), "{:.1f} dB"),
        ):
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)
            label.setText(fmt.format(value if slider is self._cf_cut_slider else value / 10.0))
        self._set_cf_advanced_enabled(preset == "Custom")
