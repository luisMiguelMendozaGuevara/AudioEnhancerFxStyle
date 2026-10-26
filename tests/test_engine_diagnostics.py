"""Tests de integración del diagnóstico de latencia y de tiempos de DSP.

Cubren el enganche entre el módulo puro ``audio_enhancer.diagnostics`` y el
motor: ``AudioEngine.latency_breakdown()`` (desglose en ms a partir del estado
real del ring y del backlog del hijo) y ``AudioEngine.timing_snapshot()``
(percentiles del coste por bloque, publicados por el hijo o medidos en local),
más la publicación de esos tiempos desde ``dsp_process.child_main``.

Regla de la iteración: el módulo puro se prueba aparte (test_diagnostics.py);
aquí solo se prueba que el cableado devuelve lo que dice devolver.
"""

import contextlib
import queue
import threading
from types import SimpleNamespace

import numpy as np
import pytest

import audio_enhancer.dsp_process as dsp_process_mod
from audio_enhancer.constants import CHUNK, RING_SECONDS, SAMPLE_RATE
from audio_enhancer.engine import AudioEngine

MS_POR_FRAME = 1000.0 / SAMPLE_RATE


class _FakeSharedArray:
    """Doble de ``multiprocessing.Array`` (get_lock + índice)."""

    def __init__(self, values):
        self.values = list(values)
        self._lock = threading.Lock()

    def get_lock(self):
        return self._lock

    def __len__(self):
        return len(self.values)

    def __getitem__(self, i):
        return self.values[i]

    def __setitem__(self, i, v):
        self.values[i] = v


@pytest.fixture
def engine():
    enhancer = SimpleNamespace(process=lambda x: np.asarray(x, dtype=np.float32))
    return AudioEngine(enhancer)


# ---------- desglose de latencia ----------


def test_latency_breakdown_sin_ring_devuelve_etapas_en_cero(engine):
    """Sin ring configurado el desglose existe y solo cuenta los bloques.

    El colchón del ring es 0 (no hay datos) pero los bloques de captura y de
    salida existen siempre: son el coste estructural de procesar por bloques.
    """
    desglose = engine.latency_breakdown()

    assert set(desglose) == {
        "capture_block_ms",
        "ring_ms",
        "child_backlog_ms",
        "output_block_ms",
        "total_ms",
    }
    assert desglose["ring_ms"] == pytest.approx(0.0)
    assert desglose["child_backlog_ms"] == pytest.approx(0.0)
    assert desglose["capture_block_ms"] == pytest.approx(CHUNK * MS_POR_FRAME)
    assert desglose["output_block_ms"] == pytest.approx(CHUNK * MS_POR_FRAME)
    assert desglose["total_ms"] == pytest.approx(desglose["capture_block_ms"] + desglose["output_block_ms"])


def test_latency_breakdown_refleja_el_llenado_real_del_ring(engine):
    """El colchón medido es el llenado del ring, no la capacidad del ring.

    Con 2000 frames pendientes (write_pos - read_pos) el ring aporta
    2000/48000 s = 41,7 ms, independientemente de que quepan 0,5 s.
    """
    engine.configure_ring(SAMPLE_RATE)
    engine.write_pos = 2000
    engine.read_pos = 0

    desglose = engine.latency_breakdown()

    assert desglose["ring_ms"] == pytest.approx(2000 * MS_POR_FRAME, abs=1e-6)
    assert desglose["total_ms"] > desglose["ring_ms"]
    # La capacidad del ring NO es la latencia: 0,5 s serían 500 ms.
    assert desglose["ring_ms"] < RING_SECONDS * 1000.0 / 2


def test_latency_breakdown_incluye_el_backlog_medio_del_hijo(engine):
    """backlog medio = backlog_sum / blocks, convertido a ms."""
    engine.configure_ring(SAMPLE_RATE)
    # 3 bloques de media en cola: 3 * 1024 frames = 64 ms.
    engine._child_stats = _FakeSharedArray([0, 100, 300, 0])

    desglose = engine.latency_breakdown()

    assert desglose["child_backlog_ms"] == pytest.approx(3 * CHUNK * MS_POR_FRAME, abs=1e-6)


def test_latency_breakdown_tolera_contadores_incoherentes(engine):
    """0 bloques o valores negativos no deben lanzar ni producir NaN."""
    engine.configure_ring(SAMPLE_RATE)
    engine._child_stats = _FakeSharedArray([0, 0, 0, 0])
    engine.write_pos = -50  # absurdo: el clamp lo deja en 0
    engine.read_pos = 0

    desglose = engine.latency_breakdown()

    assert desglose["ring_ms"] == pytest.approx(0.0)
    assert desglose["child_backlog_ms"] == pytest.approx(0.0)
    assert all(v == v for v in desglose.values())  # ningún NaN


# ---------- percentiles del DSP ----------


def test_timing_snapshot_vacio_devuelve_ceros(engine):
    """Sin muestras: las cuatro claves existen y valen 0.0."""
    snap = engine.timing_snapshot()

    assert set(snap) == {"p50_ms", "p95_ms", "p99_ms", "max_ms"}
    assert all(v == pytest.approx(0.0) for v in snap.values())


def test_timing_snapshot_prefiere_los_tiempos_del_hijo(engine):
    """Si el hijo publica, manda el hijo (es quien procesa en producción)."""
    engine._child_timing = _FakeSharedArray([1.5, 2.5, 3.5, 9.0])
    engine._telemetry = _FakeSharedArray([0.0] * 7)
    engine._sync_telemetry()

    snap = engine.timing_snapshot()

    assert snap["p50_ms"] == pytest.approx(1.5)
    assert snap["p99_ms"] == pytest.approx(3.5)
    assert snap["max_ms"] == pytest.approx(9.0)


def test_timing_snapshot_usa_la_estadistica_local_sin_hijo(engine):
    """Sin proceso hijo se mide en el hilo local (modo NO_DSP_PROCESS)."""
    for valor in (0.010, 0.020, 0.030, 0.040):
        engine._local_dsp_ms.add(valor)

    snap = engine.timing_snapshot()

    assert snap["p50_ms"] == pytest.approx(25.0, abs=0.6)
    assert snap["max_ms"] == pytest.approx(40.0, abs=0.01)
    assert snap["p99_ms"] >= snap["p95_ms"] >= snap["p50_ms"]


def test_sync_telemetry_no_rompe_sin_memoria_compartida(engine):
    """Sin telemetría ni tiempos (tests, modo degradado) no debe lanzar."""
    engine._telemetry = None
    engine._child_timing = None

    engine._sync_telemetry()

    assert engine.timing_snapshot()["p99_ms"] == pytest.approx(0.0)


# ---------- el hijo publica sus propios tiempos ----------


def _procesar_en_hilo(child_timing, bloques_esperados=8):
    """Arranca child_main en un hilo con colas normales y espera a que procese."""
    from audio_enhancer.dsp import _scipy_ndimage, _scipy_signal

    # Pre-calentar scipy: la primera llamada importa scipy (segundos) y sin
    # esto el test expira antes de ver el primer bloque.
    _scipy_signal()
    _scipy_ndimage()

    raw_q = queue.Queue()
    out_q = queue.Queue()
    params_q = queue.Queue()
    stop = threading.Event()
    for _ in range(bloques_esperados):
        raw_q.put(np.zeros((CHUNK, 2), dtype=np.float32).tobytes())

    hilo = threading.Thread(
        target=dsp_process_mod.child_main,
        args=(raw_q, out_q, params_q, stop, None, None, None, None, None, child_timing),
        daemon=True,
    )
    hilo.start()
    for _ in range(bloques_esperados):
        assert out_q.get(timeout=10) is not None
    stop.set()
    hilo.join(timeout=5)


def test_child_main_publica_percentiles_del_dsp():
    """El hijo publica [p50, p95, p99, max] en ms, con p99 >= p50 y max >= p99.

    Se publica cada 8 bloques; con 8 bloques ya debe haber valores > 0. La
    señal es silencio, así que el coste es el mínimo de la cadena: lo que se
    comprueba es el cableado y el orden de los percentiles, no el rendimiento.
    """
    child_timing = _FakeSharedArray([0.0, 0.0, 0.0, 0.0])

    with contextlib.suppress(Exception):
        _procesar_en_hilo(child_timing, bloques_esperados=8)

    p50, p95, p99, pico = child_timing.values
    assert p50 > 0.0, "el hijo no publicó tiempos (child_timing sin escribir)"
    assert p50 <= p95 <= p99 <= pico
