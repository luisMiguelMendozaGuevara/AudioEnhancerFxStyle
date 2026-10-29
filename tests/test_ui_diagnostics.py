"""Tests de la UI de diagnóstico: formato de LUFS y contenido del informe.

La instrumentación se añadió sin cobertura de UI (los tests existentes usan la
fixture con `running=False`, así que el bloque de métricas del informe nunca se
ejecutaba). Aquí se cubre el formateo y el informe completo con el motor activo.
"""

from __future__ import annotations

import math
import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import audio_enhancer.config_manager as cfg_manager_mod  # noqa: E402
import audio_enhancer.single_instance as single_instance_mod  # noqa: E402
import audio_enhancer.ui.new.main_window as mw  # noqa: E402
from audio_enhancer.constants import SAMPLE_RATE  # noqa: E402
from audio_enhancer.ui.new.theme.colors import Theme  # noqa: E402


class _FakeSharedArray:
    """Doble de ``multiprocessing.Array`` (get_lock + índice + len)."""

    def __init__(self, values):
        self.values = list(values)
        self._lock = threading.Lock()

    def get_lock(self):
        return self._lock

    def __len__(self):
        return len(self.values)

    def __getitem__(self, i):
        return self.values[i]

    def __setitem__(self, i, value):
        self.values[i] = value


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """Ventana real (offscreen) con configuración temporal y motor simulado."""
    monkeypatch.setattr(cfg_manager_mod, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(mw, "detect_system_language", lambda: "es")
    w = mw.NewMainWindow()
    w.build_content()
    yield w
    w._closing = True
    w.engine.stop()
    with __import__("contextlib").suppress(Exception):
        w._spectrum_worker.stop()


def test_fmt_lufs_formatea_y_oculta_las_medidas_invalidas():
    """Nunca debe imprimir 'nan' ni 'inf' en un informe de soporte."""
    fmt = mw.NewMainWindow._fmt_lufs

    assert fmt(-23.44) == "-23.4"
    assert fmt(0.0) == "0.0"
    assert fmt(None) == "--"
    assert fmt(float("nan")) == "--"
    assert fmt(float("-inf")) == "--"
    assert fmt(float("inf")) == "--"
    assert fmt(-1000.0) == "--"
    assert fmt(-500.0) == "--"
    assert fmt("no es un numero") == "--"


def test_el_panel_del_ecualizador_muestra_las_metricas(window):
    """Las métricas del motor se ven en la página Ecualizador (no en la barra)."""
    engine = window.engine
    engine.configure_ring(SAMPLE_RATE)
    engine.write_pos, engine.read_pos = 2880, 0
    engine._child_stats = _FakeSharedArray([0, 100, 300, 0])
    engine._child_timing = _FakeSharedArray([1.2, 2.1, 2.9, 7.5])
    engine._telemetry = _FakeSharedArray([0.0] * 7 + [-16.4, -15.9, -18.2, 5.0])
    engine._sync_telemetry()
    window.controller.running = True
    try:
        for _ in range(31):  # el tick de métricas corre 1 de cada 30 refrescos
            window._refresh_visuals()
        eq = window._pages["equalizer"]
        claves = ("underruns", "huecos", "latencia", "dsp_p99", "lufs", "lra")
        valores = {clave: eq.metric_text(clave) for clave in claves}
    finally:
        window.controller.running = False

    assert valores["underruns"] == "0"
    assert valores["huecos"] == "0"
    assert valores["latencia"].endswith(" ms") and valores["latencia"] != "--"
    assert valores["dsp_p99"] == "2.9 ms"
    assert valores["lufs"] == "-16.4"
    assert valores["lra"] == "5.0 LU"


def test_la_barra_inferior_ya_no_lleva_metricas(window):
    """La barra de estado queda con estado y ruta (sin métricas ni frecuencia)."""
    barra = window._status_bar

    for atributo in ("_metrics_label", "_latency_label", "_sr_label", "_sep1", "_route_label"):
        assert not hasattr(barra, atributo)
    for metodo in ("set_metrics", "set_latency", "set_sample_rate", "set_route"):
        assert not hasattr(barra, metodo)


def test_la_frecuencia_de_muestreo_esta_en_el_panel(window):
    """48 kHz vive en el panel del Ecualizador, también antes del primer tick."""
    window._on_audio_started("CABLE Input", "Altavoces", 48000)

    assert window._pages["equalizer"].metric_text("frecuencia") == "48 kHz"


def test_la_barra_debajo_solo_dice_si_esta_activo(window):
    """Abajo no aparece ninguna ruta: solo el estado (Activo / Inactivo)."""
    barra = window._status_bar

    window._on_audio_started("CABLE Input (VB-Audio Virtual Cable)", "Altavoces (Synaptics)", 48000)
    window._on_output_ready(81.3)
    activo = barra._status_label.text()
    assert activo == "Activo"

    window._status_bar.set_processing(False)
    inactivo = barra._status_label.text()
    assert inactivo == "Inactivo"

    # Ni nombres de dispositivo ni flechas: la ruta vive en la página Audio.
    for texto in (activo, inactivo):
        assert "CABLE" not in texto
        assert "->" not in texto


@pytest.mark.parametrize(
    ("rate", "esperado"),
    [(48000, "48 kHz"), (44100, "44.1 kHz"), (96000, "96 kHz"), (8000, "8 kHz"), (0, "--")],
)
def test_formato_de_la_frecuencia(window, rate, esperado):
    """Múltiplos de 1000 sin decimales; el resto con un decimal."""
    panel = window._pages["equalizer"]._metrics
    panel.set_metrics(frecuencia=float(rate))

    assert panel.text_of("frecuencia") == esperado


def test_el_panel_vuelve_a_guiones_cuando_se_para(window):
    """Sin audio no hay cifras: el panel no debe mostrar valores viejos."""
    engine = window.engine
    engine._child_timing = _FakeSharedArray([1.2, 2.1, 2.9, 7.5])
    engine._telemetry = _FakeSharedArray([0.0] * 7 + [-16.4, -15.9, -18.2, 5.0])
    engine._sync_telemetry()
    window.controller.running = True
    for _ in range(31):
        window._refresh_visuals()
    assert window._pages["equalizer"].metric_text("lufs") == "-16.4"

    window.controller.running = False
    for _ in range(31):
        window._refresh_visuals()

    assert window._pages["equalizer"].metric_text("lufs") == "--"
    assert window._pages["equalizer"].metric_text("latencia") == "--"


def test_el_panel_tolera_valores_absurdos(window):
    """NaN, None y el centinela de silencio se muestran como '--', no como texto."""
    panel = window._pages["equalizer"]._metrics

    panel.set_metrics(lufs=float("nan"), latencia=None, dsp_p99=float("inf"), lra=-1000.0)
    assert panel.text_of("lufs") == "--"
    assert panel.text_of("latencia") == "--"
    assert panel.text_of("dsp_p99") == "--"

    # El centinela de "sin medida" del motor (-1000 LUFS) también es '--'.
    panel.set_metrics(lufs=-1000.0)
    assert panel.text_of("lufs") == "--"

    # Y una medida real se formatea con un decimal.
    panel.set_metrics(lufs=-17.25, lra=6.5)
    assert panel.text_of("lufs") == "-17.2"
    assert panel.text_of("lra") == "6.5 LU"


def test_el_informe_de_diagnostico_incluye_latencia_dsp_y_sonoridad(window, tmp_path, monkeypatch):
    """El botón de soporte escribe las líneas nuevas con el motor activo."""
    destino_dir = tmp_path / "logs"
    destino_dir.mkdir()
    monkeypatch.setattr(single_instance_mod, "LOG_FILE", str(destino_dir / "app.log"))
    (destino_dir / "app.log").write_text("linea de log", encoding="utf-8")

    engine = window.engine
    engine._child_timing = _FakeSharedArray([1.2, 2.1, 2.9, 7.5])
    engine._telemetry = _FakeSharedArray([0.0] * 7 + [-16.4, -15.9, -18.2, 5.0])
    engine._sync_telemetry()
    window.controller.running = True
    try:
        window._export_diagnostics()
    finally:
        window.controller.running = False

    informe = (destino_dir / "diagnostico.txt").read_text(encoding="utf-8")
    assert "latencia=bloque" in informe
    assert "dsp=p50" in informe
    assert "sonoridad M=-16.4 S=-15.9 I=-18.2 LUFS" in informe
    assert "LRA 5.0 LU" in informe
    assert "rt=" in informe  # estado de MMCSS para soporte


def test_el_informe_no_se_pierde_si_el_motor_falla(window, tmp_path, monkeypatch):
    """Si las métricas del motor lanzan, el informe se escribe igual."""

    class _MotorRoto:
        def __getattr__(self, nombre):
            raise RuntimeError("motor simulado roto")

    destino_dir = tmp_path / "logs"
    destino_dir.mkdir()
    monkeypatch.setattr(single_instance_mod, "LOG_FILE", str(destino_dir / "app.log"))

    window.controller.running = True
    original = window.controller.engine
    window.controller.engine = _MotorRoto()
    try:
        window._export_diagnostics()
    finally:
        window.controller.engine = original
        window.controller.running = False

    informe = (destino_dir / "diagnostico.txt").read_text(encoding="utf-8")
    assert "motor=ERROR" in informe
    assert "== Log (cola) ==" in informe  # el resto del informe sigue presente


def test_los_parametros_de_sonoridad_son_finitos_o_centinela(window):
    """`loudness_snapshot()` de la ventana nunca devuelve NaN (contrato de la UI)."""
    snap = window.engine.loudness_snapshot()

    assert set(snap) == {"momentary_lufs", "short_term_lufs", "integrated_lufs", "lra_lu"}
    assert all(not math.isnan(v) for v in snap.values())


def test_los_valores_en_aviso_se_marcan(window):
    """Cortes > 0 y un p99 cerca del bloque se pintan en color de aviso."""
    panel = window._pages["equalizer"]._metrics

    panel.set_metrics(underruns=0, huecos=0, dsp_p99=2.9)
    assert panel._en_aviso("underruns", 0.0) is False
    assert panel._en_aviso("dsp_p99", 2.9) is False

    assert panel._en_aviso("underruns", 1.0) is True
    assert panel._en_aviso("huecos", 3.0) is True
    assert panel._en_aviso("dsp_p99", 12.0) is True  # más de la mitad del bloque
    assert panel._en_aviso("lufs", -99.0) is False  # no todo avisa


def test_inicio_nace_con_el_estado_real_del_motor(window):
    """Una página creada con el motor en marcha NO puede decir 'Detenido'.

    El setter de AudioState solo emite cuando el valor cambia, así que la página
    reconstruida (cambio de tema o de idioma) no recibe ninguna señal: tiene que
    leer el estado al construirse.
    """
    window.state.processing = True  # sin emitir hacia esta página nueva
    from audio_enhancer.ui.new.pages.home import HomePage

    pagina = HomePage(window.state, window._t)

    assert pagina._status_text.text() == "ACTIVO"
    assert pagina._start_button.text() == "Detener audio"


def test_cambiar_tema_con_audio_activo_mantiene_activo_en_inicio(window):
    """El bug reportado: al cambiar el tema, Inicio volvía a 'Detenido'."""
    window._on_audio_started("CABLE Input", "Altavoces", 48000)
    window._on_output_ready(81.3)
    assert window._pages["home"]._status_text.text() == "ACTIVO"

    window._on_theme_changed(1 if Theme.mode == "dark" else 0)

    home = window._pages["home"]
    assert home._status_text.text() == "ACTIVO", "el audio sigue activo tras el rebuild"
    assert home._start_button.text() == "Detener audio"
    assert window._status_bar._status_label.text() == "Activo"
    # La página Audio también conserva su estado (antes quedaba en '--').
    assert window._pages["audio"]._info_labels["status"].text() == "Processing"


def test_el_rebuild_sin_audio_deja_las_paginas_en_inactivo(window):
    """El arreglo no debe pintar 'ACTIVO' cuando el motor está parado."""
    window._rebuild_pages()

    assert window._pages["home"]._status_text.text() == "Detenido"
    assert window._pages["home"]._start_button.text() == "Iniciar audio"
    assert window._status_bar._status_label.text() == "Inactivo"


def test_un_aviso_en_la_barra_caduca_y_vuelve_al_estado(window):
    """Un aviso ('Dispositivos listos.') no puede quedarse fijo para siempre."""
    barra = window._status_bar
    barra.set_processing(True)

    barra.set_status_text("Dispositivos listos.", Theme.SUCCESS)
    assert barra.estado_visible == "Dispositivos listos."
    assert barra._timer.isActive(), "el aviso debe tener caducidad"

    barra._volver_al_estado()  # lo que hará el timer a los 6 s

    assert barra.estado_visible == "Activo"
    assert not barra._timer.isActive()


def test_el_estado_cancela_el_aviso_pendiente(window):
    """Si cambia el estado, manda el estado (no el aviso viejo)."""
    barra = window._status_bar
    barra.set_processing(True)
    barra.set_status_text("Activando salida...", Theme.WARNING)

    barra.set_processing(False)

    assert barra.estado_visible == "Inactivo"
    assert not barra._timer.isActive()
    barra._volver_al_estado()  # un timeout tardío no debe resucitar el aviso
    assert barra.estado_visible == "Inactivo"


def test_la_barra_se_traduce_al_estado_en_ingles(window):
    anterior = window.language
    try:
        window._apply_language("en")
        window._status_bar.set_processing(True)
        assert window._status_bar.estado_visible == "Active"
        window._status_bar.set_processing(False)
        assert window._status_bar.estado_visible == "Inactive"
    finally:
        window._apply_language(anterior)


def test_un_pico_real_por_encima_de_cero_avisa_en_el_panel(window):
    """El panel marca en color de aviso un true-peak que recortaría el DAC."""
    panel = window._pages["equalizer"]._metrics

    panel.set_metrics(pico=-1.2, true_peak=0.4, reduccion=-0.5, latencia=81.0)

    assert panel.text_of("pico") == "-1.2 dBFS"
    assert panel.text_of("true_peak") == "+0.4 dBTP"
    assert panel.text_of("reduccion") == "-0.5 dB"
    assert panel._en_aviso("true_peak", 0.4) is True
    assert panel._en_aviso("true_peak", -0.4) is False


def test_el_interruptor_de_pico_real_apaga_la_medicion(window):
    """Apagar la medición no toca el audio, pero deja de medir (y ahorra CPU)."""
    panel = window._pages["equalizer"]._metrics
    assert panel.medir_pico_real_activo() is True
    assert window.engine.medir_true_peak is True

    panel._medir_pico_real.setChecked(False)

    assert window.engine.medir_true_peak is False
    picos = window.engine.true_peak_snapshot()
    assert picos["true_peak_dbtp"] == -1000.0

    panel._medir_pico_real.setChecked(True)
    assert window.engine.medir_true_peak is True
