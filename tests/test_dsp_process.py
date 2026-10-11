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
