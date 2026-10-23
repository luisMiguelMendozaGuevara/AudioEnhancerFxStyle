"""Validacion integral de conexiones interfaz <-> motor (NewMainWindow).

Cubre: puente estado->DSP, presets, borrado de presets, sincronizacion
state/enhancer tras cargar config, persistencia de idioma y reflejo de
estado del motor en la UI."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def window(qapp, tmp_path_factory):
    # Aislar de la config real (R3-C2): la ventana ahora lee vía
    # ConfigManager, que resuelve CONFIG_PATH del módulo en cada operación;
    # basta redirigir esa ruta a un tmp para no tocar el config del usuario.
    import audio_enhancer.config_manager as cfg_manager_mod
    from audio_enhancer.ui.new import main_window as mw

    tmp_cfg = tmp_path_factory.mktemp("ui-config") / "config.json"
    orig_path = cfg_manager_mod.CONFIG_PATH
    # Idioma FIJADO en español (B3): los tests no pueden depender del
    # locale del host.
    orig_detect = mw.detect_system_language
    cfg_manager_mod.CONFIG_PATH = str(tmp_cfg)
    mw.detect_system_language = lambda: "es"
    w = mw.NewMainWindow()
    w.build_content()
    yield w
    cfg_manager_mod.CONFIG_PATH = orig_path
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


def test_cambio_de_bandas_remezcla_la_curva_en_la_ui(window):
    """(fix EQ) Al cambiar 9->31 la curva del widget es la MISMA forma
    remapeada por frecuencia: antes se comprimia en las primeras bandas."""
    eq = window._pages["equalizer"]
    eq._on_band_changed(4, 6.0)  # pico en 1 kHz (indice 4 del set de 9)
    window._on_eq_band_count(31)
    curve = eq._eq_curve
    assert len(curve._bands) == 31
    assert len(curve._gains) == 31
    idx = min(range(31), key=lambda i: abs(curve._bands[i] - 1000.0))
    assert curve._gains[idx] == pytest.approx(6.0, abs=0.2)
    assert curve._gains[0] == pytest.approx(0.0, abs=0.05)


def test_limiter_toggle_reaches_dsp(window):
    toggle = window._pages["effects"]._limiter_card._toggle
    toggle.setChecked(False)
    assert window.enhancer.limiter is False
    assert toggle.text() == "OFF"
    toggle.setChecked(True)
    assert window.enhancer.limiter is True
    assert toggle.text() == "ON"


def test_compressor_toggle_reaches_dsp(window):
    toggle = window._pages["effects"]._compressor_card._toggle
    toggle.setChecked(False)
    assert window.enhancer.compressor is False
    toggle.setChecked(True)
    assert window.enhancer.compressor is True


def test_true_peak_toggle_reaches_dsp(window):
    toggle = window._pages["effects"]._true_peak_card._toggle
    toggle.setChecked(False)
    assert window.enhancer.true_peak is False
    toggle.setChecked(True)
    assert window.enhancer.true_peak is True


def test_exportar_diagnostico_escribe_archivo(window, tmp_path, monkeypatch):
    """C4: el diagnóstico se escribe sin tocar el audio."""
    from audio_enhancer import single_instance

    log = tmp_path / "audio_enhancer.log"
    log.write_text("linea de log\n", encoding="utf-8")
    monkeypatch.setattr(single_instance, "LOG_FILE", str(log))
    window._export_diagnostics()
    dest = tmp_path / "diagnostico.txt"
    assert dest.exists()
    contenido = dest.read_text(encoding="utf-8")
    assert "Audio Enhancer FxStyle" in contenido
    assert "== Config ==" in contenido
    assert "linea de log" in contenido


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
    def _bass_gains(v):
        if isinstance(v, dict):
            return float(v.get("bass", 0.0)), list(v.get("eq_gains", []))
        return float(v[1]), list(v[3])

    name = next(n for n, v in window._all_presets().items() if _bass_gains(v)[0] != 0 or any(_bass_gains(v)[1]))
    window._on_preset_selected(name)
    bass, gains = _bass_gains(window._all_presets()[name])
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


def test_spectrum_flows_to_home_widget(window, monkeypatch):
    # El fixture no muestra la ventana (offscreen): se simula visible, que es
    # la condición para que el espectro se aplique (gate de visibilidad).
    monkeypatch.setattr(window, "isVisible", lambda: True)
    monkeypatch.setattr(window._pages["home"], "isVisible", lambda: True)
    window._receive_spectrum([-20.0] * 64)
    window._refresh_visuals()
    assert window._pages["home"]._spectrum._smooth.size > 0  # datos recibidos


def test_spectrum_necesario_tambien_en_ecualizador(window, monkeypatch):
    """El Ecualizador dibuja barras de espectro: la FFT debe seguir activa ahí.

    Regresion: el gate R3-B2 solo miraba la pagina Inicio, asi que al abrir el
    ecualizador el SpectrumWorker paraba y las barras quedaban congeladas
    (no actualizaban automaticamente)."""
    monkeypatch.setattr(window, "isVisible", lambda: True)
    window._closing = False
    window._stack.setCurrentWidget(window._pages["home"])
    window._update_spectrum_needed()
    assert window._spectrum_worker.needed.is_set()
    window._navigate_to("equalizer")
    assert window._spectrum_worker.needed.is_set()  # el EQ tambien dibuja espectro
    window._navigate_to("presets")
    assert not window._spectrum_worker.needed.is_set()  # pagina sin espectro
    window._navigate_to("home")


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


def test_config_con_tipos_raros_no_tumba_el_arranque(window):
    """(C2/R3-C2) config.json editado a mano con tipos incorrectos -> defaults.

    La basura se escribe en la ruta AISLADA del fixture (CONFIG_PATH del
    módulo config_manager, redirigido a tmp) y ConfigManager la sanea."""
    import json
    from pathlib import Path

    import audio_enhancer.config_manager as cfg_manager_mod

    basura = {
        "volume": "alto",
        "bass": None,
        "treble": [1, 2],
        "eq_gains": ["a", 2, 3],
        "latency_pref": "rapida",
        "limiter": "sí",
        "custom_presets": {
            "malo": [1.0, 2.0, 3.0, [0.0] * 8],  # 8 ganancias: longitud no soportada
            "malo2": "no soy un preset",
            "bueno": [1.5, 4.0, -2.0, [1.0] * 9],
        },
    }
    ruta = Path(cfg_manager_mod.CONFIG_PATH)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(basura), encoding="utf-8")
    try:
        window._apply_config()  # no debe lanzar
    finally:
        ruta.unlink(missing_ok=True)
    # Valores no coercibles -> defaults intactos
    assert window.enhancer.volume == pytest.approx(1.0)
    assert window.enhancer.bass == pytest.approx(0.0)
    assert window.enhancer.limiter is True  # bool estricto: "sí" no es bool
    assert window.state.latency_pref == 60
    # Presets: el malformado se descarta, el correcto sobrevive saneado
    assert "malo" not in window.custom_presets
    assert "malo2" not in window.custom_presets
    # v1 migrado a v2 (dict)
    bueno = window.custom_presets["bueno"]
    assert bueno["volume"] == pytest.approx(1.5) and bueno["eq_gains"] == [pytest.approx(1.0)] * 9


def test_preset_de_longitud_incorrecta_es_rechazado(window):
    """(C2/F5) seleccionar un preset con una longitud NO soportada (8) no
    desalinea el DSP."""
    antes = list(window.enhancer.eq_gains)
    window.custom_presets["roto"] = (1.0, 0.0, 0.0, [2.0] * 8)
    window._on_preset_selected("roto")
    assert window.enhancer.eq_gains == antes  # el DSP queda intacto
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


# ---------- tamaño de botones y guardas del arranque ----------


def test_boton_reset_eq_crece_con_el_texto(window):
    """El botón del EQ tenía ancho FIJO 100 px: 'Restablecer todo' se recortaba
    en español. Debe poder crecer (ancho máximo por defecto)."""
    btn = window._pages["equalizer"]._reset_btn
    assert btn.text() == "Restablecer todo"
    assert btn.maximumWidth() > 1000  # no fijo
    assert btn.minimumWidth() == 100


def test_toggle_audio_deshabilita_el_boton_durante_el_prefill(window, monkeypatch):
    """Un segundo clic durante el prefill detenía el audio recién iniciado:
    el botón se bloquea hasta que la salida está lista."""
    home = window._pages["home"]
    audio_page = window._pages["audio"]
    window.loopbacks = [{"name": "CABLE Input", "index": 1, "defaultSampleRate": 48000}]
    window.speakers = [{"name": "Speakers", "index": 2, "defaultSampleRate": 48000}]
    audio_page.set_loopbacks(["CABLE Input"])
    audio_page.set_speakers(["Speakers"])
    audio_page.set_input("CABLE Input")
    audio_page.set_output("Speakers")
    window._route_guard()
    assert window.go

    monkeypatch.setattr(window.controller, "start", lambda *a, **k: None)
    window.toggle_audio()
    assert home._start_button.isEnabled() is False

    window._on_output_ready(80.0)  # fin del prefill
    assert home._start_button.isEnabled() is True


# ---------- Contrato R3-C3: la ventana no toca privados de las páginas ----------


def test_ventana_no_usa_privados_conocidos_de_paginas():
    """R3-C3: la ventana solo usa señales y métodos públicos de las páginas.

    Se comprueban los atributos privados que YA existieron como violación
    (regex acotada, no una genérica propensa a falsos positivos)."""
    import re
    from pathlib import Path

    import audio_enhancer.ui.new.main_window as mw

    src = Path(mw.__file__).read_text(encoding="utf-8")
    for attr in ("_latency_combo", "_volume_label", "_autostart_check", "_name_entry", "_start_button"):
        assert not re.search(rf'self\._pages\["[a-z]+"\]\.{attr}\b', src), f"acceso privado R3-C3: {attr}"


# ---------- Opciones nuevas: techo de seguridad, watchdog y latencia viva ----------


def test_crossfeed_avanzado_llega_al_dsp(window):
    """Preset Avanzado (Custom): los sliders de frecuencia/nivel llegan al DSP
    y sus valores se acotan a los rangos de libbs2b."""
    eff = window._pages["effects"]
    eff._crossfeed_card._toggle.setChecked(True)
    assert window.enhancer.crossfeed is True
    # Seleccionar Avanzado habilita los sliders y cambia el preset.
    idx = eff._crossfeed_combo.findData("Custom")
    eff._crossfeed_combo.setCurrentIndex(idx)
    assert window.enhancer.crossfeed_preset == "Custom"
    assert eff._cf_cut_slider.isEnabled() and eff._cf_feed_slider.isEnabled()
    # Mover los sliders llega al DSP.
    eff._cf_cut_slider.setValue(500)
    eff._cf_feed_slider.setValue(80)  # 8.0 dB
    assert window.enhancer.crossfeed_cut_hz == 500
    assert window.enhancer.crossfeed_feed_db == pytest.approx(8.0)
    # Un perfil fijo deshabilita los sliders avanzados.
    eff._crossfeed_combo.setCurrentIndex(eff._crossfeed_combo.findData("Natural"))
    assert not eff._cf_cut_slider.isEnabled()
    eff._crossfeed_card._toggle.setChecked(False)


def test_techo_seguridad_toggle_llega_al_dsp(window):
    toggle = window._pages["effects"]._safety_card._toggle
    toggle.setChecked(False)
    assert window.enhancer.safety_ceiling_enabled is False
    assert toggle.text() == "OFF"
    toggle.setChecked(True)
    assert window.enhancer.safety_ceiling_enabled is True
    assert toggle.text() == "ON"


def test_recorte_final_toggle_llega_al_dsp(window):
    toggle = window._pages["effects"]._final_clip_card._toggle
    toggle.setChecked(False)
    assert window.enhancer.final_clip_enabled is False
    assert toggle.text() == "OFF"
    toggle.setChecked(True)
    assert window.enhancer.final_clip_enabled is True
    assert toggle.text() == "ON"


def test_native_capture_es_optin_y_se_aplica(window):
    """(PRUEBA) La captura nativa es OFF por defecto y el checkbox la activa."""
    assert window.native_capture is False
    check = window._pages["settings"]._native_capture_check
    check.setChecked(True)
    assert window.native_capture is True
    check.setChecked(False)
    assert window.native_capture is False


def test_watchdog_check_llega_al_controlador(window):
    check = window._pages["settings"]._watchdog_check
    check.setChecked(False)
    assert window.watchdog_enabled is False
    assert window.controller.watchdog_enabled is False
    check.setChecked(True)
    assert window.controller.watchdog_enabled is True


def test_cambiar_latencia_en_caliente_actualiza(window, monkeypatch):
    """Con el audio activo, cambiar la latencia se aplica en caliente y el
    valor mostrado se actualiza (antes exigía reiniciar)."""
    llamadas: list[int] = []
    monkeypatch.setattr(window.controller, "set_latency", lambda ms: llamadas.append(ms) or 121.3)
    window.controller.running = True
    try:
        combo = window._pages["audio"]._latency_combo
        combo.setCurrentIndex(combo.findData(100))
    finally:
        window.controller.running = False
    assert llamadas == [100]
    assert window.state.latency_pref == 100


# ---------- Medidores estéreo (L/R) ----------


def test_estado_emite_niveles_por_canal(qapp):
    from audio_enhancer.ui.new.audio_state import AudioState

    st = AudioState()
    got = []
    st.input_levels_changed.connect(lambda left, right: got.append((left, right)))
    st.input_levels = (0.5, 0.2)
    assert got == [(0.5, 0.2)]


def test_medidor_estereo_guarda_los_dos_canales(qapp):
    from audio_enhancer.ui.new.widgets.level_meter import LevelMeterWidget

    m = LevelMeterWidget(stereo=True)
    m.set_stereo(0.8, 0.3)
    assert m._level_l == pytest.approx(0.8)
    assert m._level_r == pytest.approx(0.3)


# ---------- Importar / Exportar presets (JSON) ----------


def test_botones_importar_exportar_emiten_senales(qapp):
    """PresetsPage expone señales; la ventana las conecta (antes eran botones
    decorativos sin conectar)."""
    from audio_enhancer.ui.new.audio_state import AudioState
    from audio_enhancer.ui.new.pages.presets import PresetsPage

    page = PresetsPage(AudioState())
    got = []
    page.import_requested.connect(lambda: got.append("import"))
    page.export_requested.connect(lambda: got.append("export"))
    page._import_btn.click()
    page._export_btn.click()
    assert got == ["import", "export"]


def test_importar_presets_desde_json(window, tmp_path, monkeypatch):
    import json

    from audio_enhancer.ui.new import main_window as mw

    payload = {"Mi preset": [0.9, 3.0, 2.0, [1.0] * 9], "Invalido": [1.0, 2.0]}
    src = tmp_path / "presets.json"
    src.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(mw.QFileDialog, "getOpenFileName", lambda *a, **k: (str(src), "JSON (*.json)"))

    window._import_presets()
    mi = window.custom_presets["Mi preset"]
    assert mi["volume"] == 0.9 and mi["bass"] == 3.0 and mi["treble"] == 2.0
    assert mi["eq_gains"] == [1.0] * 9
    assert "Invalido" not in window.custom_presets  # descartado por el saneo


def test_exportar_presets_a_json(window, tmp_path, monkeypatch):
    import json

    from audio_enhancer.ui.new import main_window as mw

    window.custom_presets["Exportado"] = {
        "volume": 1.0,
        "bass": 1.0,
        "treble": 1.0,
        "eq_gains": [0.0] * 9,
        "effects": {},
        "balance_db": 0.0,
        "master_gain_db": 0.0,
        "leveling": 0.0,
    }
    dest = tmp_path / "out.json"
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), "JSON (*.json)"))

    window._export_presets()
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["Exportado"]["volume"] == 1.0
    assert data["Exportado"]["eq_gains"] == [0.0] * 9


def test_efectos_enhancer_conectados_al_motor(window):
    """Fase 1: los sliders de la capa enhancer llegan al DSP."""
    window.state.dynamic_boost = 4.0
    assert window.enhancer.effects.get("dynamic_boost") == pytest.approx(4.0)
    window.state.leveling = 6.0
    assert window.enhancer.leveling == pytest.approx(6.0)  # campo aparte, no efecto


def test_eq_avanzado_conectado_al_motor(window):
    """(F5) nº de bandas, frecuencia central y Q llegan al DSP."""
    window._on_eq_band_count(5)
    assert len(window.enhancer.eq_bands) == 5
    window._on_eq_q(3.0)
    assert window.enhancer.eq_q == pytest.approx(3.0)
    window._on_eq_freq(0, 90.0)
    assert window.enhancer.eq_bands[0] == pytest.approx(90.0, rel=0.01)
    window._on_eq_band_count(9)  # restaurar el estado para el resto de tests


def test_cambiar_el_ancho_q_redibuja_la_curva(window):
    """Regresion ("Ancho no funciona"): la curva ahora es la respuesta REAL
    del EQ; mover la Q debe cambiar su forma (antes era una Bezier cosmetica
    que ignoraba la Q)."""
    page = window._pages["equalizer"]
    page.set_bands([100.0, 1000.0, 10000.0])
    page.set_gains([12.0, 0.0, 0.0])

    def _ancho_a_mitad(q: float) -> float:
        page.set_q(q)
        freqs, resp = page._eq_curve.sample_response()
        above = freqs > 100.0
        # Primera frecuencia (por encima de f0) donde el lomo cae a la mitad.
        idx = int(np.argmax(resp[above] <= 6.0))
        return float(freqs[above][idx]) - 100.0

    assert _ancho_a_mitad(8.0) < _ancho_a_mitad(0.5) / 2.0
    # El deslizador actualiza curva y motor a la vez.
    page._on_q(800)
    assert page._eq_curve._q == pytest.approx(8.0)
    assert window.enhancer.eq_q == pytest.approx(8.0)
    assert window.state.eq_q == pytest.approx(8.0)
    # Restaurar para el resto de tests del modulo.
    page.set_q(1.4)
    window._on_eq_band_count(9)
    page.set_gains([0.0] * 9)


def test_aviso_de_recorte_con_efectos_sin_proteccion(window):
    """(F6) Con efectos activos y sin limitador/techo, la página avisa."""
    page = window._pages["effects"]
    # Estado limpio: sin efectos -> sin aviso.
    window.state.safety_ceiling = False
    window.state.limiter = True
    window.state.dynamic_boost = 0.0
    assert page._warn_label.isHidden()
    # Efecto activo sin protección -> aviso.
    window.state.limiter = False
    window.state.dynamic_boost = 5.0
    assert not page._warn_label.isHidden()
    # Cualquier protección lo apaga.
    window.state.safety_ceiling = True
    assert page._warn_label.isHidden()
    # Restaurar para el resto de tests.
    window.state.safety_ceiling = False
    window.state.dynamic_boost = 0.0
    window.state.limiter = True


def test_set_preset_no_emite_preset_selected(window):
    """(fix EQ) set_preset es programático: no debe re-aplicar el preset.

    Antes emitía (setCurrentText dispara currentTextChanged) y al arrancar o
    cambiar tema/idioma re-aplicaba el preset, machacando el EQ del config."""
    home = window._pages["home"]
    llamadas: list[str] = []
    home.preset_selected.connect(llamadas.append)
    home.set_preset("Rock")
    assert llamadas == []  # programático: silencio
    # El usuario sí emite (setCurrentText solo emite si el texto CAMBIA).
    home._preset_combo.setCurrentText("Clásica / Acústica")
    assert llamadas == ["Clásica / Acústica"]


def test_eq_multibanda_sobrevive_al_arranque_y_al_rebuild(window):
    """(fix EQ) Un EQ de 31 bandas configurado NO debe volver a 9 al re-aplicar
    el preset (arranque / cambio de tema o idioma).

    El preset guardado debe ser distinto del PRIMER item del combo: repoblar
    deja el combo en el primero, así que si el guardado es ese, set_preset no
    cambia nada y no emite. El caso real del usuario es 'Recomendado
    (altavoces)' (no es el primero)."""
    home = window._pages["home"]
    home._preset_combo.blockSignals(True)
    home._preset_combo.setCurrentText("Recomendado (altavoces)")
    home._preset_combo.blockSignals(False)

    window._on_eq_band_count(31)
    gains = list(window.state.eq_gains)
    gains[20] = 6.0
    window.state.eq_gains = gains
    window._save_config()
    assert window._config.load()["preset"] == "Recomendado (altavoces)"

    # Arranque simulado: el combo muestra otro nombre y _apply_config repuebla
    # con el guardado -> antes re-emitía y machacaba el EQ.
    home._preset_combo.blockSignals(True)
    home._preset_combo.setCurrentText("Rock")
    home._preset_combo.blockSignals(False)
    window._apply_config()
    assert len(window.enhancer.eq_bands) == 31
    assert window.enhancer.eq_gains[20] == pytest.approx(6.0)

    window._rebuild_pages()  # cambio de tema/idioma
    assert len(window.enhancer.eq_bands) == 31
    assert window.enhancer.eq_gains[20] == pytest.approx(6.0)

    window._on_eq_band_count(9)  # restaurar para el resto de tests


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


def test_perfiles_automaticos_por_dispositivo_de_salida(window):
    """Al elegir un preset se asocia al dispositivo de salida actual; al
    cambiar entre salidas físicas se restaura el preset de cada una."""
    audio_page = window._pages["audio"]
    home = window._pages["home"]
    audio_page.set_speakers(["Altavoces (Realtek)", "Auriculares (USB)"])

    audio_page.set_output("Altavoces (Realtek)")
    home.set_preset("Rock")
    window._on_preset_selected("Rock")
    assert window.device_presets["Altavoces (Realtek)"] == "Rock"

    audio_page.set_output("Auriculares (USB)")
    home.set_preset("Voz / Podcast")
    window._on_preset_selected("Voz / Podcast")
    assert window.device_presets["Auriculares (USB)"] == "Voz / Podcast"

    # Al volver a Altavoces, se restaura automáticamente su preset ("Rock").
    audio_page.set_output("Altavoces (Realtek)")
    window._on_output_device_selected()
    assert window.state.preset_name == "Rock"
    assert home.preset_text() == "Rock"
