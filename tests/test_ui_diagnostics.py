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


def test_la_barra_muestra_latencia_y_lufs(window):
    """El texto de métricas incluye latencia total y sonoridad cuando hay medida."""
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
        texto = window._status_bar._metrics_label.text()
    finally:
        window.controller.running = False

    assert "lat total" in texto
    assert "dsp p99" in texto
    assert "-16.4 LUFS" in texto


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
