"""Validacion integral de conexiones interfaz <-> motor (NewMainWindow).

Cubre: puente estado->DSP, presets, borrado de presets, sincronizacion
state/enhancer tras cargar config, persistencia de idioma y reflejo de
estado del motor en la UI."""

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
    from audio_enhancer.ui.new import main_window as mw

    # Aislar de la config real: los tests de idioma no deben contaminar
    # el config.json del usuario ni el arranque de otras ventanas.
    orig_load, orig_save = mw.load_config, mw.save_config
    # Idioma FIJADO en español (B3): detect_system_language depende del
    # locale del host, así que en una máquina inglesa los tests que asumen
    # etiquetas españolas fallaban sin ningún defecto real del producto.
    orig_detect = mw.detect_system_language
    mw.load_config = lambda: {}
    mw.save_config = lambda cfg: True
    mw.detect_system_language = lambda: "es"
    w = mw.NewMainWindow()
    w.build_content()
    yield w
    mw.load_config, mw.save_config = orig_load, orig_save
    mw.detect_system_language = orig_detect
    w._closing = True
    w.engine.stop()
    w._spectrum_worker.stop()


# ---------- UI -> DSP (controles) ----------


def test_bass_slider_reaches_dsp(window):
    window._pages["effects"]._on_bass(6.0)
    assert window.enhancer.bass == pytest.approx(6.0)


def test_treble_slider_reaches_dsp(window):
    window._pages["effects"]._on_treble(-2.0)
    assert window.enhancer.treble == pytest.approx(-2.0)


def test_eq_curve_reaches_dsp(window):
    window._pages["equalizer"]._on_band_changed(3, 5.0)
    assert window.enhancer.eq_gains[3] == pytest.approx(5.0)


def test_eq_reset_reaches_dsp(window):
    window._pages["equalizer"]._on_band_changed(3, 5.0)
    window._pages["equalizer"]._reset_all()
    assert window.enhancer.eq_gains == [pytest.approx(0.0)] * 9


def test_limiter_toggle_reaches_dsp(window):
    toggle = window._pages["effects"]._limiter_card._toggle
    toggle.setChecked(False)
    assert window.enhancer.limiter is False
    toggle.setChecked(True)
    assert window.enhancer.limiter is True


def test_compressor_toggle_reaches_dsp(window):
    toggle = window._pages["effects"]._compressor_card._toggle
    toggle.setChecked(False)
    assert window.enhancer.compressor is False
    toggle.setChecked(True)
    assert window.enhancer.compressor is True


def test_ab_button_reaches_dsp(window):
    window.toggle_ab()
    assert window.enhancer.blend == pytest.approx(0.0)
    window.toggle_ab()
    assert window.enhancer.blend == pytest.approx(1.0)


def test_volume_slider_reaches_dsp(window):
    window._on_volume_slider(150)
    assert window.enhancer.volume == pytest.approx(1.5)


# ---------- Presets ----------


def test_preset_selection_updates_dsp_and_state(window):
    name = next(n for n, v in window._all_presets().items() if v[1] != 0 or any(v[3]))
    window._on_preset_selected(name)
    vol, bass, treble, gains = window._all_presets()[name]
    assert window.enhancer.bass == pytest.approx(bass)
    assert window.enhancer.eq_gains == [pytest.approx(g) for g in gains]
    # state sincronizado (antes quedaba en 0 y desincronizaba la UI)
    assert window.state.bass == pytest.approx(bass)


def test_save_and_delete_custom_preset(window):
    window.enhancer.bass = 4.0
    window._pages["presets"]._name_entry.setText("test_preset_x")
    window._save_custom_preset()
    assert "test_preset_x" in window.custom_presets
    # Borrar via la signal de la pagina
    window._delete_custom_preset("test_preset_x")
    assert "test_preset_x" not in window.custom_presets


def test_presets_page_lists_all_presets(window):
    page = window._pages["presets"]
    included_rows = page._included_layout.count()
    assert included_rows >= len(__import__("audio_enhancer.i18n", fromlist=["PRESETS"]).PRESETS)
    assert page._custom_layout.count() == len(window.custom_presets)


# ---------- Motor -> UI (feedback) ----------


def test_processing_state_reflects_in_home(window):
    window.state.processing = True
    assert window._pages["home"]._start_button.text() == "Detener audio"
    window.state.processing = False
    assert window._pages["home"]._start_button.text() == "Iniciar audio"


def test_spectrum_flows_to_home_widget(window):
    window._receive_spectrum([-20.0] * 64)
    window._refresh_visuals()
    assert window._pages["home"]._spectrum._smooth  # datos recibidos


def test_route_guard_updates_audio_page(window):
    page = window._pages["audio"]
    page._input_combo.setCurrentText("")
    window._route_guard()
    assert window.go is False
    assert page._route_label.text() != ""


# ---------- Config / idioma ----------


def test_language_persisted_in_config(window):
    window._on_language_changed("English")
    assert window.language == "en"
    window._on_language_changed("Espanol")
    assert window.language == "es"


# ---------- R2: checkboxes de comportamiento (C1) y config robusta (C2) ----------


def test_checkboxs_de_comportamiento_gobiernan_la_app(window):
    """Los tres checkboxes de Config dejaron de ser decorativos."""
    window._pages["settings"]._tray_check.setChecked(False)
    assert window.minimize_to_tray is False
    window._pages["settings"]._autostart_audio_check.setChecked(False)
    assert window.autostart_audio is False
    window._pages["settings"]._notifications_check.setChecked(False)
    assert window.notifications_enabled is False
    # Restaurar: el default del producto es todo activado
    window._pages["settings"]._tray_check.setChecked(True)
    window._pages["settings"]._autostart_audio_check.setChecked(True)
    window._pages["settings"]._notifications_check.setChecked(True)


def test_config_con_tipos_raros_no_tumba_el_arranque(window, monkeypatch):
    """(C2) config.json editado a mano con tipos incorrectos -> defaults."""
    from audio_enhancer.ui.new import main_window as mw

    basura = {
        "volume": "alto",
        "bass": None,
        "treble": [1, 2],
        "eq_gains": ["a", 2, 3],
        "latency_pref": "rapida",
        "limiter": "sí",
        "custom_presets": {
            "malo": (1.0, 2.0, 3.0, [0.0] * 10),  # 10 ganancias: longitud incorrecta
            "malo2": "no soy un preset",
            "bueno": (1.5, 4.0, -2.0, [1.0] * 9),
        },
    }
    original = mw.load_config
    mw.load_config = lambda: basura
    try:
        window._apply_config()  # no debe lanzar
    finally:
        mw.load_config = original
    # Valores no coercibles -> defaults intactos
    assert window.enhancer.volume == pytest.approx(1.0)
    assert window.enhancer.bass == pytest.approx(0.0)
    assert window.enhancer.limiter is True  # bool estricto: "sí" no es bool
    assert window.state.latency_pref == 60
    # Presets: el malformado se descarta, el correcto sobrevive saneado
    assert "malo" not in window.custom_presets
    assert "malo2" not in window.custom_presets
    vol, bass, treble, gains = window.custom_presets["bueno"]
    assert vol == pytest.approx(1.5) and gains == [pytest.approx(1.0)] * 9


def test_preset_de_longitud_incorrecta_es_rechazado(window):
    """(C2) seleccionar un preset con 10 ganancias no desalinea el DSP."""
    window.custom_presets["roto"] = (1.0, 0.0, 0.0, [2.0] * 10)
    window._on_preset_selected("roto")
    assert len(window.enhancer.eq_gains) == 9  # el DSP queda intacto
    del window.custom_presets["roto"]


def test_preferences_de_comportamiento_sobreviven_al_rebuild(window):
    """(C1) tras reconstruir páginas los checkboxes reflejan la preferencia."""
    window.minimize_to_tray = False
    window.notifications_enabled = False
    window._rebuild_pages()
    settings = window._pages["settings"]
    assert settings._tray_check.isChecked() is False
    assert settings._notifications_check.isChecked() is False
    assert settings._autostart_audio_check.isChecked() is True  # default
    window.minimize_to_tray = True
    window.notifications_enabled = True


def test_spectrum_worker_necesidad_calculada_por_visibilidad(window):
    """R3-B2: la FFT solo corre si la ventana es visible, la página activa es
    Home y la app no se está cerrando (offscreen: isVisible() es False)."""
    window._closing = False
    window._update_spectrum_needed()
    assert not window._spectrum_worker.needed.is_set()  # ventana nunca mostrada

    # Simular condiciones de "alguien mira": visible + Home activa.
    window._spectrum_worker.needed.set()
    assert window._spectrum_worker.needed.is_set()
    window._spectrum_worker.set_needed(False)
    assert not window._spectrum_worker.needed.is_set()
