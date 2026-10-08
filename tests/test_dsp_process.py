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
