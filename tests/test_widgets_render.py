"""Widgets graficos: logica pura testeable sin depender de pantalla real.

`spectrum.py`, `level_meter.py` y `equalizer.py` estaban al 30-41% de
cobertura. Aqui se prueba lo determinista: mapeos dB->Y, agregacion de
espectro por banda, hit-testing del EQ y estado de los medidores. Los
`paintEvent` se ejercitan con un QImage (offscreen) para cazar crashes."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


# ---------- medidor de nivel ----------


def test_meter_mapeo_db_y_pico(qapp):
    from audio_enhancer.ui.new.widgets.level_meter import LevelMeterWidget

    m = LevelMeterWidget()
    assert m._db_from_level(1.0) == pytest.approx(0.0)
    assert m._db_from_level(0.5) == pytest.approx(-6.02, abs=0.1)
    assert m._db_from_level(0.0) <= -60.0  # piso
    # set_level con pico explícito hace hold max
    m.set_level(0.5, peak=0.9)
    assert m._peak == pytest.approx(0.9)
    # set_level clampa a [0, 1]
    m.set_level(2.0)
    assert m._level == 1.0


def _render(w, width, height):
    """Renderiza un widget a un QImage (offscreen) para ejercitar paintEvent."""
    from PySide6.QtGui import QImage

    w.resize(width, height)
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    w.render(img)
    return img


def test_meter_modo_gr(qapp):
    from audio_enhancer.ui.new.widgets.level_meter import LevelMeterWidget

    m = LevelMeterWidget()
    m.set_gr(-12.0)  # mitad del rango 0..-24
    assert m._level == pytest.approx(0.5)
    m.set_gr(0.0)
    assert m._level == pytest.approx(0.0)
    m.set_gr(-100.0)  # se acota a 1.0
    assert m._level == 1.0


def test_meter_pinta_sin_crash(qapp):
    from audio_enhancer.ui.new.widgets.level_meter import LevelMeterWidget

    for stereo in (False, True):
        for orient in ("horizontal", "vertical"):
            w = LevelMeterWidget(orientation=orient, stereo=stereo)
            if stereo:
                w.set_stereo(0.8, 0.3)
            else:
                w.set_level(0.7, peak=0.9)
            _render(w, 120, 40)


# ---------- espectro ----------


def test_spectrum_agrega_y_pinta(qapp):
    from audio_enhancer.ui.new.widgets.spectrum import SpectrumWidget

    w = SpectrumWidget()
    w.set_spectrum([-30.0] * 64)
    assert len(w._smooth) > 0
    _render(w, 300, 160)
    # Sin datos, las barras decaen sin lanzar
    w.set_spectrum([])
    w.set_spectrum(None)


# ---------- curva del ecualizador ----------


def test_eq_freq_to_band_mapea_centros(qapp):
    from audio_enhancer.ui.new.pages.equalizer import EQCurveWidget

    w = EQCurveWidget()
    # Cada frecuencia central debe mapear a su propia banda
    for i, center in enumerate(w.EQ_BANDS):
        assert w._freq_to_band(center) == i


def test_eq_y_to_gain_es_inverso_de_gain_to_y(qapp):
    from audio_enhancer.ui.new.pages.equalizer import EQCurveWidget

    w = EQCurveWidget()
    h, mt, mb = 200, 16, 24
    for gain in (-12.0, -6.0, 0.0, 6.0, 12.0):
        y = w._gain_to_y(gain, h, mt, mb)
        assert w._y_to_gain(y, h, mt, mb) == pytest.approx(gain, abs=0.01)


def test_eq_x_to_nearest_band(qapp):
    from audio_enhancer.ui.new.pages.equalizer import EQCurveWidget

    w = EQCurveWidget()
    w.resize(600, 200)
    ml, mr = 44, 12
    for i in range(9):
        x = w._band_to_x(i, 600, ml, mr)
        assert w._x_to_nearest_band(x, 600, ml, mr) == i


def test_eq_set_gains_y_spectrum_pinta(qapp):
    from audio_enhancer.ui.new.pages.equalizer import EQCurveWidget

    w = EQCurveWidget()
    w.set_gains([3.0] * 9)
    assert w._gains == [3.0] * 9
    w.set_spectrum([-40.0] * 64)
    assert any(b > 0 for b in w._bars)
    w.reset()
    assert w._gains == [0.0] * 9
    _render(w, 600, 260)


def test_eq_spectrum_vacio_decae_barras(qapp):
    from audio_enhancer.ui.new.pages.equalizer import EQCurveWidget

    w = EQCurveWidget()
    w.set_spectrum([-10.0] * 64)
    antes = list(w._bars)
    w.set_spectrum([])  # sin audio: decae
    assert all(b <= a for b, a in zip(w._bars, antes, strict=False))
