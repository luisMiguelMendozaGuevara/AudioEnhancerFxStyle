"""Smoke test: los modulos de la app importan y las constantes son coherentes
(sin instanciar la GUI, que requiere pantalla/Qt)."""

import importlib


def test_importan_todos_los_modulos():
    for nombre in (
        "audio_enhancer.constants",
        "audio_enhancer.config",
        "audio_enhancer.device_state",
        "audio_enhancer.dsp",
        "audio_enhancer.engine",
        "audio_enhancer.i18n",
        "audio_enhancer.main",
        "audio_enhancer.single_instance",
        "audio_enhancer.startup_metrics",
        "audio_enhancer.ui",
        "audio_enhancer.ui.new",
        "audio_enhancer.ui.new.main_window",
    ):
        importlib.import_module(nombre)


def test_constantes_coherentes():
    from audio_enhancer.constants import (
        APP_VERSION,
        DEFAULT_PRESET,
        SAMPLE_RATE,
        WINDOW_TITLE,
    )
    from audio_enhancer.i18n import PRESETS

    assert isinstance(APP_VERSION, str) and APP_VERSION
    assert SAMPLE_RATE > 0
    assert WINDOW_TITLE
    assert DEFAULT_PRESET in PRESETS
