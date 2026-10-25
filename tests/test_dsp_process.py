"""Tests del proceso hijo del DSP.

Lo importante: el hilo visual calcula el espectro FUERA del bucle de audio y
solo cuando la UI lo necesita, para que la gráfica nunca retrase el audio.
"""

import multiprocessing as mp
import threading
import time

import numpy as np

from audio_enhancer.dsp import Enhancer
from audio_enhancer.dsp_process import _viz_loop


def _tone():
    t = np.arange(1024) / 48000.0
    return (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def _run_viz(needed_value, seconds=2.0):
    enh = Enhancer()
    enh.spectrum_enabled = True
    enh._snapshot = _tone()
    buf = mp.Array("f", 64)
    needed = mp.Value("b", needed_value)
    stop = threading.Event()
    t = threading.Thread(target=_viz_loop, args=(enh, buf, needed, stop), daemon=True)
    t.start()
    deadline = time.time() + seconds
    vals = [0.0] * 64
    while time.time() < deadline:
        with buf.get_lock():
            vals = [buf[i] for i in range(len(buf))]
        if any(v != 0.0 for v in vals):
            break
        time.sleep(0.02)
    stop.set()
    t.join(1.0)
    return vals


def test_viz_loop_calcula_espectro():
    vals = _run_viz(needed_value=1)
    assert any(v != 0.0 for v in vals), "el hilo visual no publicó espectro"


def test_viz_loop_no_trabaja_si_no_se_necesita():
    vals = _run_viz(needed_value=0, seconds=0.4)
    assert all(v == 0.0 for v in vals), "no debería calcular espectro si no se necesita"


def test_apply_no_resetea_filtros_si_las_bandas_no_cambian():
    """(fix EQ) _apply con las MISMAS bandas no debe limpiar los estados del EQ.

    Antes llamaba set_eq_bands en cada snapshot (al mover cualquier control),
    lo que borraba _states/_section_on/_sos_cache/_coeff_cache: transitorios y
    recomputación inútil."""
    from audio_enhancer.dsp import Enhancer
    from audio_enhancer.dsp_process import _apply

    e = Enhancer()
    e.set_eq_bands([60, 250, 1000, 4000, 12000])
    e.eq_gains = [0.0, 0.0, 6.0, 0.0, 0.0]
    e._states["eq_2"] = np.zeros((2, 2), dtype=np.float32)  # simula sección activa
    _apply(e, {"eq_bands": [60.0, 250.0, 1000.0, 4000.0, 12000.0], "eq_gains": [0.0, 0.0, 3.0, 0.0, 0.0]})
    assert "eq_2" in e._states  # el estado NO se limpió
    assert e.eq_gains[2] == 3.0  # pero la ganancia sí se aplicó


def test_child_main_procesa_y_publica_contadores():
    """El bucle del hijo procesa bloques y publica sus contadores (Paso 0)."""
    import queue as _queue

    from audio_enhancer.dsp import _scipy_ndimage, _scipy_signal
    from audio_enhancer.dsp_process import child_main

    # Pre-calentar scipy: sin esto la primera llamada a process() importa
    # scipy (~segundos) y el test expira antes de ver bloques.
    _scipy_signal()
    _scipy_ndimage()
    raw_q = _queue.Queue()
    out_q = _queue.Queue()
    params_q = _queue.Queue()
    stop = threading.Event()
    stats = mp.Array("d", 4)
    t = threading.Thread(
        target=child_main,
        args=(raw_q, out_q, params_q, stop, None, None, None, None, stats),
        daemon=True,
    )
    t.start()
    block = (0.1 * np.ones((1024, 2), dtype=np.float32)).tobytes()
    raw_q.put(block)
    raw_q.put(block)
    deadline = time.time() + 20.0
    blocks = 0
    while time.time() < deadline:
        with stats.get_lock():
            blocks = int(stats[1])
        if blocks >= 2:
            break
        time.sleep(0.05)
    stop.set()
    t.join(timeout=2.0)
    assert blocks >= 2, "el hijo no procesó los bloques"
    out = out_q.get_nowait()
    assert len(out) == 1024 * 2 * 4  # 1024 frames estéreo float32


def test_apply_actualiza_bandas_y_reinicia_estado_si_cambia_sample_rate():
    """_apply actualiza eq_bands si cambian y llama reset_state si cambia sample_rate."""
    from audio_enhancer.dsp_process import _apply

    e = Enhancer()
    e._states["eq_0"] = np.zeros((2, 2), dtype=np.float32)
    _apply(
        e,
        {
            "sample_rate": 44100,
            "eq_bands": [60.0, 250.0, 1000.0, 4000.0, 12000.0],
            "eq_gains": [1.0, 2.0, 3.0, 4.0, 5.0],
            "effects": {"fidelity": 4.0},
        },
    )
    assert e.sample_rate == 44100
    assert e.eq_bands == [60.0, 250.0, 1000.0, 4000.0, 12000.0]
    assert e.eq_gains == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert e.effects["fidelity"] == 4.0
    assert e._states == {}  # reset_state limpió los filtros


class _FakeSharedValue:
    def __init__(self, v):
        self.value = v
        self._lock = threading.Lock()

    def get_lock(self):
        return self._lock


class _FakeSharedArray:
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


def test_child_main_publica_telemetria_gr_y_recortes():
    """child_main publica telemetry (7 floats), gr_value y cuenta muestras > 1.0."""
    import queue as _queue

    from audio_enhancer.dsp import _scipy_ndimage, _scipy_signal
    from audio_enhancer.dsp_process import child_main

    _scipy_signal()
    _scipy_ndimage()
    raw_q = _queue.Queue()
    out_q = _queue.Queue()
    params_q = _queue.Queue()
    # Desactivar protecciones para forzar salida > 1.0 y verificar el contador clipped.
    params_q.put(
        {
            "limiter": False,
            "compressor": False,
            "safety_ceiling_enabled": False,
            "final_clip_enabled": False,
        }
    )
    stop = threading.Event()
    gr_val = _FakeSharedValue(0.0)
    telemetry = _FakeSharedArray([0.0] * 7)
    stats = _FakeSharedArray([0.0] * 4)

    t = np.arange(1024) / 48000.0
    wave = (1.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    block = np.stack([wave, wave], axis=1).tobytes()
    raw_q.put(block)
    raw_q.put(None)  # centinela de parada limpia

    child_main(raw_q, out_q, params_q, stop, gr_val, telemetry, None, None, stats)

    assert int(stats[1]) == 1  # 1 bloque procesado
    assert int(stats[3]) > 0  # muestras > 1.0 contabilizadas en clipped
    assert float(telemetry[1]) > 0.4  # level_peak publicado (suavizado 0.3 * 1.5)


def test_engine_pump_telemetria_espectro_y_retencion_sin_descartes():
    """Cubre el camino de proceso hijo en AudioEngine: _pump (incluyendo cola
    llena con retención pending), _sync_telemetry, read_spectrum,
    set_spectrum_needed, _pump_params y _stop_dsp_process."""
    import queue as _queue

    from audio_enhancer.engine import AudioEngine

    enh = Enhancer()
    eng = AudioEngine(enh)
    eng.configure_ring(48000)

    eng._raw_q = _queue.Queue(maxsize=1)
    eng._out_q = _queue.Queue()
    eng._params_q = _queue.Queue()
    eng._telemetry = _FakeSharedArray([0.25, 0.75, 0.2, 0.25, 0.7, 0.75, -2.5])
    eng._spectrum_buf = _FakeSharedArray([0.0] * 64)
    eng._spec_needed = _FakeSharedValue(0)

    # Espectro inicialmente vacío -> None; con datos -> lista de 64 floats.
    assert eng.read_spectrum() is None
    eng._spectrum_buf[0] = -12.0
    spec = eng.read_spectrum()
    assert spec is not None and spec[0] == -12.0

    eng.set_spectrum_needed(True)
    assert eng._spec_needed.value == 1
    eng.set_spectrum_needed(False)
    assert eng._spec_needed.value == 0

    # Alimentar 2 bloques en el ring crudo con raw_q de capacidad 1 (fuerza
    # la rama de cola llena / retención sin descartar) y 1 bloque en out_q.
    raw_arr = 0.1 * np.ones((1024, 2), dtype=np.float32)
    eng._put_raw(raw_arr)
    eng._put_raw(raw_arr)
    eng._out_q.put(raw_arr.tobytes())

    eng._dsp_running = True
    pump_t = threading.Thread(target=eng._pump, daemon=True)
    params_t = threading.Thread(target=eng._pump_params, daemon=True)
    pump_t.start()
    params_t.start()

    # Cambiar un parámetro para que _pump_params publique un snapshot.
    time.sleep(0.02)
    enh.volume = 1.3
    time.sleep(0.08)

    # Drenar el primer bloque de raw_q para que _pump entregue el segundo.
    first = eng._raw_q.get(timeout=1.0)
    second = eng._raw_q.get(timeout=1.0)
    assert len(first) == 1024 * 2 * 4
    assert len(second) == 1024 * 2 * 4

    eng._dsp_running = False
    pump_t.join(timeout=1.0)
    params_t.join(timeout=1.0)

    # _sync_telemetry copió los niveles al Enhancer y _put escribió en el ring.
    assert enh.level_peak == 0.75
    assert enh.level_gr == -2.5
    assert eng.fill() >= 1024
    snap = eng._params_q.get_nowait()
    assert snap["volume"] == 1.3

    # Cubrir _stop_dsp_process con un proceso falso que requiere terminate().
    class _FakeProc:
        pid = 999999
        terminated = False

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return not self.terminated

        def terminate(self):
            self.terminated = True

    eng._mp_stop = threading.Event()
    eng._dsp_proc = _FakeProc()
    assert eng.has_dsp_process() is True
    eng._set_child_priority()
    eng._stop_dsp_process()
    assert eng.has_dsp_process() is False


def test_shared_block_queue_ciclo_completo_y_wrap_around():
    """SharedBlockQueue funciona sin pickle ni pipes, soporta wrap-around,
    centinela None, evento de notificación y descarte de antiguos en put()."""
    import queue as _queue

    import pytest

    from audio_enhancer.dsp_process import SharedBlockQueue

    ev = threading.Event()
    q = SharedBlockQueue(maxsize=2, max_block_bytes=128, notify_event=ev)
    assert q.qsize() == 0
    assert not q.full()

    q.put_nowait(b"bloque-1")
    assert ev.is_set()
    q.put_nowait(b"bloque-2")
    assert q.full()
    assert q.qsize() == 2

    with pytest.raises(_queue.Full):
        q.put_nowait(b"desborde")

    # put() cuando está llena descarta el más viejo ("bloque-1") sin bloquear.
    q.put(b"bloque-3")
    assert q.get_nowait() == b"bloque-2"
    assert q.get(timeout=0.05) == b"bloque-3"
    assert q.qsize() == 0

    with pytest.raises(_queue.Empty):
        q.get(timeout=0.02)

    # Centinela None (longitud -1) y wrap-around de índices.
    q.put_nowait(None)
    assert q.get_nowait() is None


def test_parent_alive_detecta_padre_vivo_y_pid_inexistente():
    """Si el principal muere a lo bruto, el hijo debe poder detectarlo y salir
    (evita el proceso DSP huerfano que sigue capturando en silencio)."""
    import os as os_mod

    from audio_enhancer.dsp_process import _parent_alive

    if os_mod.name != "nt":
        assert _parent_alive(0) is True  # fuera de Windows no se comprueba
        return
    assert _parent_alive(os_mod.getpid()) is True
    assert _parent_alive(0x7FFFFFF0) is False  # PID inexistente
