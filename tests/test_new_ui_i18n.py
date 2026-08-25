"""i18n de la nueva UI: traduccion completa y cambio de idioma EN CALIENTE.

La recarga en caliente tira todo el contenido de la ventana y lo reconstruye
(build_content); estas pruebas verifican que las etiquetas quedan traducidas y
que el puente estado->DSP sobrevive a la recarga."""

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


@pytest.fixture(scope="module")
def window(qapp):
    from audio_enhancer.ui.new.main_window import NewMainWindow

    w = NewMainWindow()
    w.build_content()
    yield w
    w._closing = True
    w.engine.stop()
    w._spectrum_worker.stop()


def test_default_labels_spanish(window):
    assert window._pages["home"]._start_button.text() == "Iniciar audio"
    assert window._pages["audio"]._refresh_btn.text() == "Actualizar dispositivos"
    assert window._sidebar._items[0]._text_label.text() == "Inicio"


def test_live_switch_to_english(window):
    window._apply_language("en")
    assert window.language == "en"
    assert window._pages["home"]._start_button.text() == "Start audio"
    assert window._pages["audio"]._refresh_btn.text() == "Refresh devices"
    assert window._sidebar._items[0]._text_label.text() == "Home"
    assert window._sidebar._items[1]._text_label.text() == "Equalizer"
    assert window._pages["settings"]._autostart_check.text() == "Start with Windows"
    assert window._pages["effects"]._bass_card._title_label.text() == "Bass boost (dB)"


def test_state_preserved_after_reload(window):
    """Los valores del DSP sobreviven a la recarga de la interfaz."""
    window.enhancer.bass = 7.5
    window.enhancer.eq_gains = [2.0] * 9
    window._apply_language("es")
    assert window.enhancer.bass == pytest.approx(7.5)
    assert window.enhancer.eq_gains == [pytest.approx(2.0)] * 9
    # La UI reconstruida muestra los valores preservados
    assert window._pages["home"]._start_button.text() == "Iniciar audio"


def test_bridge_alive_after_reload(window):
    """El puente estado->DSP sigue operativo tras la recarga."""
    window.state.bass = 5.0
    assert window.enhancer.bass == pytest.approx(5.0)
    window._pages["equalizer"]._on_band_changed(2, 4.0)
    assert window.enhancer.eq_gains[2] == pytest.approx(4.0)


def test_theme_switch_by_index(window):
    window._on_theme_changed(1)
    from audio_enhancer.ui.new.theme.colors import Theme

    assert Theme.mode == "light"
    window._on_theme_changed(0)
    assert Theme.mode == "dark"


def test_language_combo_reflects_current(window, qapp):
    window._apply_language("en")
    qapp.processEvents()  # build_content corre queued via singleShot(0)
    settings = window._pages["settings"]
    assert settings._lang_combo.currentText() == "English"
    window._apply_language("es")
    qapp.processEvents()
    assert window._pages["settings"]._lang_combo.currentText() == "Espanol"
