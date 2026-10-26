"""Tests de integración del diagnóstico de latencia y de tiempos de DSP.

Cubren el enganche entre el módulo puro ``audio_enhancer.diagnostics`` y el
motor: ``AudioEngine.latency_breakdown()`` (desglose en ms a partir del estado
real del ring y del backlog del hijo) y ``AudioEngine.timing_snapshot()``
(percentiles del coste por bloque, publicados por el hijo o medidos en local),
más la publicación de esos tiempos desde ``dsp_process.child_main``.

Regla de la iteración: el módulo puro se prueba aparte (test_diagnostics.py);
aquí solo se prueba que el cableado devuelve lo que dice devolver.
"""

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
    try:
        for _ in range(bloques_esperados):
            assert out_q.get(timeout=10) is not None
    finally:
        # Sin este finally, un fallo deja vivos el hilo del hijo y su hilo "viz"
        # girando a 10 Hz durante el resto de la sesión de tests.
        stop.set()
        hilo.join(timeout=5)


def test_child_main_publica_percentiles_del_dsp():
    """El hijo publica [p50, p95, p99, max] en ms, con p99 >= p50 y max >= p99.

    Se publica cada 8 bloques; con 8 bloques ya debe haber valores > 0. La
    señal es silencio, así que el coste es el mínimo de la cadena: lo que se
    comprueba es el cableado y el orden de los percentiles, no el rendimiento.
    """
    child_timing = _FakeSharedArray([0.0, 0.0, 0.0, 0.0])

    _procesar_en_hilo(child_timing, bloques_esperados=8)

    p50, p95, p99, pico = child_timing.values
    assert p50 > 0.0, "el hijo no publicó tiempos (child_timing sin escribir)"
    assert p50 <= p95 <= p99 <= pico
    # Magnitud, no solo signo: silencio por la cadena por defecto no puede costar
    # 25 ms (el presupuesto entero del bloque) ni microsegundos (unidades mal).
    assert 0.05 <= p50 <= 10.0, f"p50={p50:.3f} ms fuera de un rango plausible"


# ---------- sonoridad (BS.1770) publicada por el hijo ----------


def _seno_estereo(segundos: float, dbfs: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Seno 1 kHz en fase con pico a ``dbfs`` (definición de EBU Tech 3341)."""
    amplitud = 10.0 ** (dbfs / 20.0)
    n = int(round(segundos * sample_rate))
    t = np.arange(n) / float(sample_rate)
    x = (amplitud * np.sin(2.0 * np.pi * 1000.0 * t)).astype(np.float32)
    return np.stack([x, x], axis=1)


def test_child_main_publica_la_sonoridad_de_la_salida():
    """El hijo mide LUFS de la salida y los publica en los huecos 7..9.

    Se le da 12 s de un seno de 1 kHz a -23 dBFS por canal: segun EBU Tech 3341
    (caso 1) la sonoridad momentanea y la integrada deben valer -23.0 +-0.1.
    La cadena del hijo esta en su configuracion por defecto (sin EQ ni efectos),
    asi que la senal llega intacta al medidor.
    """
    from audio_enhancer.dsp import _scipy_ndimage, _scipy_signal

    _scipy_signal()
    _scipy_ndimage()
    raw_q = queue.Queue()
    out_q = queue.Queue()
    params_q = queue.Queue()
    stop = threading.Event()
    telemetria = _FakeSharedArray([0.0] * 11)
    audio = _seno_estereo(12.0, -23.0)
    bloques = [audio[i : i + CHUNK] for i in range(0, len(audio), CHUNK)]
    for bloque in bloques:
        raw_q.put(bloque.tobytes())

    hilo = threading.Thread(
        target=dsp_process_mod.child_main,
        args=(raw_q, out_q, params_q, stop, None, telemetria, None, None, None, None),
        daemon=True,
    )
    hilo.start()
    for _ in bloques:
        assert out_q.get(timeout=20) is not None
    stop.set()
    hilo.join(timeout=5)

    momentanea, corto_plazo, integrada = (telemetria.values[7], telemetria.values[8], telemetria.values[9])
    rango = telemetria.values[10]
    assert momentanea == pytest.approx(-23.0, abs=0.15), f"M={momentanea:.2f} LUFS"
    assert corto_plazo == pytest.approx(-23.0, abs=0.15), f"S={corto_plazo:.2f} LUFS"
    assert integrada == pytest.approx(-23.0, abs=0.15), f"I={integrada:.2f} LUFS"
    # LRA de un tono constante: 0 LU (publicado en el hueco 10).
    assert rango == pytest.approx(0.0, abs=0.2), f"LRA={rango:.2f} LU"


def test_engine_expone_la_sonoridad_del_hijo(engine):
    """_sync_telemetry copia los huecos 7..9 y loudness_snapshot() los publica."""
    engine._telemetry = _FakeSharedArray([0.0] * 7 + [-23.0, -24.5, -22.0, 3.5])
    engine._sync_telemetry()

    snap = engine.loudness_snapshot()

    assert snap["momentary_lufs"] == pytest.approx(-23.0)
    assert snap["short_term_lufs"] == pytest.approx(-24.5)
    assert snap["integrated_lufs"] == pytest.approx(-22.0)
    assert snap["lra_lu"] == pytest.approx(3.5)


def test_engine_sin_telemetria_de_sonoridad_no_falla(engine):
    """Con la malla de 7 huecos (modo antiguo/tests) no hay medida y no lanza."""
    engine._telemetry = _FakeSharedArray([0.0] * 7)
    engine._sync_telemetry()

    # Contrato unico en ambos caminos: -1000.0 = "sin medida" (no -inf).
    assert engine.loudness_snapshot() == {
        "momentary_lufs": -1000.0,
        "short_term_lufs": -1000.0,
        "integrated_lufs": -1000.0,
        "lra_lu": 0.0,
    }


def test_el_medidor_local_mide_la_salida_del_hilo_de_dsp(engine):
    """Sin proceso hijo, el medidor local del motor sigue midiendo (fallback)."""
    from audio_enhancer.engine import AudioEngine  # noqa: F401 - claridad del origen

    engine.configure_ring(SAMPLE_RATE)
    audio = _seno_estereo(6.0, -23.0)
    for i in range(0, len(audio), CHUNK):
        engine._local_meter.process(engine.enhancer.process(audio[i : i + CHUNK]))

    snap = engine.loudness_snapshot()

    assert snap["momentary_lufs"] == pytest.approx(-23.0, abs=0.3)
    assert snap["integrated_lufs"] == pytest.approx(-23.0, abs=0.3)


# ---------- cableado real del hilo de DSP (lo que la suite NO cubría) ----------


def test_dsp_loop_body_alimenta_el_ring_y_mide(engine, monkeypatch):
    """`_dsp_loop_body` de verdad: lee, procesa, publica, mide y cuenta tiempos.

    Antes este cableado no lo ejecutaba ningún test: se podía borrar
    `self._local_meter.process(y)` (o `self._put(y)`) y las 445 pruebas seguían
    verdes con el diagnóstico (o el audio) muerto en el modo sin proceso hijo.
    """
    engine.configure_ring(SAMPLE_RATE)
    audio = _seno_estereo(6.0, -23.0)
    bloques = [audio[i : i + CHUNK] for i in range(0, len(audio), CHUNK)]
    pendientes = list(bloques)

    def falso_read_raw():
        return pendientes.pop(0) if pendientes else None

    monkeypatch.setattr(engine, "_read_raw", falso_read_raw)
    monkeypatch.setattr(engine, "_dsp_running", True, raising=False)

    def parar_cuando_no_haya(*args, **kwargs):
        if not pendientes:
            engine._dsp_running = False

    monkeypatch.setattr(engine, "_put", lambda data, _orig=engine._put: (_orig(data), parar_cuando_no_haya())[0])
    monkeypatch.setattr(engine, "_dsp_wakeup", None, raising=False)

    engine._dsp_loop_body()

    # 1) El audio llegó al ring: hay frames escritos.
    assert engine.write_pos > 0, "el hilo de DSP no publicó audio"
    # 2) El coste por bloque se midió (percentiles con muestras).
    assert engine._local_dsp_ms.count == len(bloques), "no se midió el coste por bloque"
    assert engine.timing_snapshot()["max_ms"] > 0.0
    # 3) El medidor local mide la salida (sonoridad real, no centinela).
    lufs = engine.loudness_snapshot()["momentary_lufs"]
    assert -30.0 < lufs < -15.0, f"sonoridad local fuera de rango: {lufs}"


def test_loudness_snapshot_tiene_las_mismas_claves_en_los_dos_caminos(engine):
    """Contrato único: 4 claves siempre (antes el camino del hijo devolvía 3)."""
    claves = {"momentary_lufs", "short_term_lufs", "integrated_lufs", "lra_lu"}

    assert set(engine.loudness_snapshot()) == claves  # sin hijo

    engine._child_loudness = {
        "momentary_lufs": -20.0,
        "short_term_lufs": -21.0,
        "integrated_lufs": -22.0,
        "lra_lu": 4.0,
    }
    assert set(engine.loudness_snapshot()) == claves  # con hijo


# ---------- el audio sobrevive a un fallo del diagnóstico ----------


def test_un_medidor_roto_no_deja_el_audio_en_silencio(engine, monkeypatch):
    """Si la medición de sonoridad falla, el bloque debe llegar IGUAL al ring.

    Es la propiedad que justifica el orden "audio primero, diagnóstico después":
    meterla (de nuevo) dentro del try del audio dejaría la app muda para siempre
    sin que ningún test se enterara.
    """

    class _MedidorRoto:
        def process(self, block):  # noqa: ARG002 - firma exigida por el motor
            raise RuntimeError("medidor simulado roto")

        def reset(self):
            raise RuntimeError("medidor simulado roto")

        def snapshot(self):
            raise RuntimeError("medidor simulado roto")

    engine.configure_ring(SAMPLE_RATE)
    monkeypatch.setattr(engine, "_local_meter", _MedidorRoto())
    audio = _seno_estereo(1.0, -20.0)
    pendientes = [audio[i : i + CHUNK] for i in range(0, len(audio), CHUNK)]

    def falso_read_raw():
        return pendientes.pop(0) if pendientes else None

    def parar_si_vacio(*args, **kwargs):
        if not pendientes:
            engine._dsp_running = False

    monkeypatch.setattr(engine, "_read_raw", falso_read_raw)
    monkeypatch.setattr(engine, "_dsp_running", True, raising=False)
    monkeypatch.setattr(
        engine, "_put", lambda data, _orig=engine._put: (_orig(data), parar_si_vacio())[0]
    )
    monkeypatch.setattr(engine, "_dsp_wakeup", None, raising=False)

    engine._dsp_loop_body()

    assert engine.write_pos > 0, "el fallo del medidor silenció el audio"
    assert engine._meter_error_logged is True, "el fallo del medidor no quedó registrado"


def test_telemetria_de_diez_huecos_no_revienta(engine):
    """Con la malla antigua (10 huecos, sin LRA) se degrada al centinela."""
    engine._telemetry = _FakeSharedArray([0.0] * 7 + [-20.0, -21.0, -22.0])

    engine._sync_telemetry()  # no debe lanzar IndexError

    snap = engine.loudness_snapshot()
    assert set(snap) == {"momentary_lufs", "short_term_lufs", "integrated_lufs", "lra_lu"}
    assert snap["momentary_lufs"] == -1000.0


def test_la_telemetria_se_lee_con_el_lock_tomado(engine):
    """La lectura de la sonoridad debe ocurrir DENTRO del lock compartido.

    Doble que falla si se lee sin el lock tomado: detecta una futura lectura
    fuera de la sección crítica (que es la que puede dar un valor rasgado).
    """

    class _ArrayEstricto(_FakeSharedArray):
        def __init__(self, values):
            super().__init__(values)
            self.bloqueado = False

        def get_lock(self):
            clase = self

            class _Ctx:
                def __enter__(self):
                    clase._lock.acquire()
                    clase.bloqueado = True
                    return self

                def __exit__(self, *exc):
                    clase.bloqueado = False
                    clase._lock.release()
                    return False

            return _Ctx()

        def __getitem__(self, i):
            if not self.bloqueado:
                raise AssertionError(f"lectura de telemetry[{i}] FUERA del lock")
            return self.values[i]

    engine._telemetry = _ArrayEstricto([0.0] * 7 + [-20.0, -21.0, -22.0, 3.0])

    engine._sync_telemetry()

    assert engine.loudness_snapshot()["lra_lu"] == pytest.approx(3.0)
