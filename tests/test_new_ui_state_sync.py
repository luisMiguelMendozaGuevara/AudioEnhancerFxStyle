"""Regresion: los controles de la UI (AudioState) deben llegar al DSP.

Sin el puente state->enhancer en NewMainWindow._wire_pages, mover los sliders
de EQ/efectos solo cambiaba la interfaz, sin efecto audible."""

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


def _window(qapp):
    from audio_enhancer.ui.new.main_window import NewMainWindow

    window = NewMainWindow()
    return window


def test_state_bass_reaches_enhancer(qapp):
    window = _window(qapp)
    window.state.bass = 6.0
    assert window.enhancer.bass == pytest.approx(6.0)


def test_state_treble_reaches_enhancer(qapp):
    window = _window(qapp)
    window.state.treble = -3.5
    assert window.enhancer.treble == pytest.approx(-3.5)


def test_state_eq_gains_reach_enhancer(qapp):
    window = _window(qapp)
    gains = [3.0] * 9
    window.state.eq_gains = gains
    assert window.enhancer.eq_gains == [pytest.approx(3.0)] * 9


def test_state_limiter_compressor_reach_enhancer(qapp):
    window = _window(qapp)
    window.state.limiter = False
    window.state.compressor = False
    assert window.enhancer.limiter is False
    assert window.enhancer.compressor is False
    window.state.limiter = True
    window.state.compressor = True
    assert window.enhancer.limiter is True
    assert window.enhancer.compressor is True


def test_state_volume_reaches_enhancer(qapp):
    window = _window(qapp)
    window.state.volume = 0.75
    assert window.enhancer.volume == pytest.approx(0.75)


def test_dsp_processes_with_eq_active(qapp):
    """El DSP aplica ganancia real cuando el EQ esta activo."""
    import numpy as np

    window = _window(qapp)
    window.state.bass = 12.0
    enhancer = window.enhancer
    enhancer.sample_rate = 48000
    x = np.random.randn(4800, 2).astype(np.float32) * 0.1
    # Quemar el transitorio de rampas
    for _ in range(20):
        y = enhancer.process(x)
    # Bass +12 dB a 60 Hz: una senal de graves debe ganar energia
    t = np.linspace(0, 1, 4800, endpoint=False)
    low = np.sin(2 * np.pi * 60 * t).astype(np.float32)
    block = np.column_stack([low, low]) * 0.1
    for _ in range(10):
        y = enhancer.process(block)
    assert np.abs(y).mean() > np.abs(block).mean()
