# -*- coding: utf-8 -*-
"""Tests del motor DSP (audio_enhancer.dsp.Enhancer).

Se prueban comportamientos deterministas con tonos sinteticos: bypass A/B,
ganancia de volumen en estado estacionario, limitador suave sin recorte duro,
compresor RMS (regresion del signo de atenuacion), shelves bass/treble, bandas
del EQ, paso mono y analizador de espectro.
"""

import numpy as np
import pytest

from audio_enhancer.dsp import Enhancer

FS = 48000
N = 2048
MILD_AMP = 0.2
BLOCKS_WARM = 20  # bloques para converger rampas (tau ~100 ms)


def _stereo(amp, freq=220.0):
    t = np.arange(N) / FS
    s = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([s, s], axis=1)


def _rms(x):
    return float(np.sqrt(np.mean(x * x)))


def warm(e, x, blocks=BLOCKS_WARM):
    for _ in range(blocks):
        e.process(x.copy())


@pytest.fixture
def noise():
    rng = np.random.default_rng(2026)
    return (rng.standard_normal((N, 2)) * MILD_AMP).astype(np.float32)


# ---------- bypass A/B ----------

def test_bypass_directo_devuelve_entrada_identica(noise):
    e = Enhancer()
    e.blend = 0.0
    warm(e, noise)
    y = e.process(noise.copy())
    assert np.array_equal(y, noise)


def test_bypass_mono_mantiene_forma():
    rng = np.random.default_rng(1)
    m = (rng.standard_normal(N) * MILD_AMP).astype(np.float32)
    e = Enhancer()
    e.blend = 0.0
    warm(e, m)
    y = e.process(m.copy())
    assert y.shape == (N,)
    assert np.array_equal(y, m)


# ---------- volumen ----------

def test_volumen_estado_estacionario(noise):
    e = Enhancer()
    e.volume = 0.5
    e.limiter = False
    e.compressor = False
    warm(e, noise)
    y = e.process(noise.copy())
    ratio = _rms(y) / _rms(noise)
    assert 0.48 <= ratio <= 0.52


def test_volumen_1_0_passthrough(noise):
    e = Enhancer()
    e.volume = 1.0
    e.limiter = False
    e.compressor = False
    warm(e, noise)
    y = e.process(noise.copy())
    assert np.allclose(y, noise, atol=1e-4)


# ---------- limitador suave ----------

def test_limitador_suaviza_sin_recorte_duro():
    x = _stereo(0.8, 220.0)
    e = Enhancer()
    e.volume = 1.25
    e.compressor = False
    e.limiter = True
    warm(e, x)
    y_lim = e.process(x.copy())

    e2 = Enhancer()
    e2.volume = 1.25
    e2.compressor = False
    e2.limiter = False
    warm(e2, x)
    y_flat = e2.process(x.copy())

    # sin limitador el pico llega al clip duro (1.0); con limitador se suaviza
    assert float(np.abs(y_flat).max()) > 0.998
    assert float(np.abs(y_lim).max()) < 0.99


def test_limitador_nunca_supera_1_0():
    x = _stereo(0.5, 440.0)
    e = Enhancer()
    e.volume = 4.0
    e.compressor = False
    assert e.limiter is True
    warm(e, x)
    y = e.process(x.copy())
    assert float(np.abs(y).max()) <= 1.0 + 1e-6


# ---------- compresor RMS ----------

def test_compresor_atentua_material_fuerte():
    x = np.full((N, 2), 0.9, dtype=np.float32)
    e = Enhancer()
    e.blend = 1.0
    e.limiter = False
    e.compressor = True
    warm(e, x)
    y = e.process(x.copy())
    # regresion: debe ATENUAR (salida < entrada), antes amplificaba a ~0.94
    assert _rms(y) < 0.88


def test_compresor_no_toca_senal_debajo_del_umbral():
    quiet = np.full((N, 2), 0.3, dtype=np.float32)
    e = Enhancer()
    e.blend = 1.0
    e.limiter = False
    e.compressor = True
    warm(e, quiet)
    y = e.process(quiet.copy())
    assert 0.27 <= _rms(y) <= 0.33


def test_sin_compresor_passthrough():
    x = np.full((N, 2), 0.9, dtype=np.float32)
    e = Enhancer()
    e.blend = 1.0
    e.limiter = False
    e.compressor = False
    warm(e, x)
    y = e.process(x.copy())
    assert np.allclose(y, x, atol=1e-4)


# ---------- ecualizador ----------

def test_bass_boost_incrementa_energia_en_graves():
    x = _stereo(0.2, 60.0)
    e = Enhancer()
    e.bass = 6.0
    e.limiter = False
    e.compressor = False
    warm(e, x)
    y = e.process(x.copy())
    assert _rms(y) / _rms(x) > 1.5


def test_treble_boost_incrementa_energia_en_agudos():
    x = _stereo(0.2, 8000.0)
    e = Enhancer()
    e.treble = 6.0
    e.limiter = False
    e.compressor = False
    warm(e, x)
    y = e.process(x.copy())
    assert _rms(y) / _rms(x) > 1.3


def test_banda_eq_negativa_atenua():
    x = _stereo(0.2, 60.0)
    e = Enhancer()
    e.eq_gains[0] = -12.0  # banda de 60 Hz
    e.limiter = False
    e.compressor = False
    warm(e, x)
    y = e.process(x.copy())
    assert _rms(y) < _rms(x)


# ---------- salida acotada y analizador ----------

def test_salida_siempre_acotada_a_1():
    rng = np.random.default_rng(7)
    x = (rng.standard_normal((N, 2)) * 3.0).astype(np.float32)
    e = Enhancer()
    e.volume = 3.0
    warm(e, x)
    y = e.process(x.copy())  # dBFS nunca debe exceder la unidad
    assert float(np.abs(y).max()) <= 1.0 + 1e-6


def test_espectro_64_barras_validas():
    x = _stereo(0.5, 440.0)
    e = Enhancer()
    e.blend = 1.0
    e.compressor = False
    warm(e, x)
    e.compute_spectrum()
    sp = e.spectrum
    assert sp is not None and sp.shape == (64,)
    assert np.isfinite(sp).all()
    assert float(sp.max()) <= 0.0            # dBFS: senal <= 1.0 -> <= 0 dB
    assert int(np.sum(sp > -60.0)) >= 1      # el tono debe marcar bins calidos


def test_espectro_silencio_queda_en_piso():
    x = np.zeros((N, 2), dtype=np.float32)
    e = Enhancer()
    e.blend = 1.0
    e.compressor = False
    warm(e, x)
    e.compute_spectrum()
    assert e.spectrum is not None
    assert int(np.sum(e.spectrum > -79.0)) == 0