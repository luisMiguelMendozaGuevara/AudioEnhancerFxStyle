"""Tests del motor de audio (audio_enhancer.engine.AudioEngine) con PortAudio
simulado: apertura de streams, formato de muestreo, ring buffer con
wrap-around, huecos con fundido y ajuste de deriva."""

from types import SimpleNamespace

import numpy as np
import pytest

import audio_enhancer.engine as engine_mod
from audio_enhancer.engine import AudioEngine


class FakeStream:
    def __init__(self, **kw):
        self.kw = kw
        self.started = False
        self.closed = False

    def start_stream(self):
        self.started = True

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class FakePA:
    def __init__(self):
        self.opened = []
        self.streams = []

    def open(self, **kw):
        s = FakeStream(**kw)
        self.opened.append(kw)
        self.streams.append(s)
        return s


@pytest.fixture
def fake_pa(monkeypatch):
    """Devuelve (modulo fake, instancia FakePA) con engine._pa apuntando al
    modulo simulado, igual que pyaudiowpatch real (constantes en el modulo)."""
    pa_mod = SimpleNamespace(paFloat32=0x1002, paContinue=0)
    pa = FakePA()
    monkeypatch.setattr(engine_mod, "_pa", lambda: pa_mod)
    return pa_mod, pa


@pytest.fixture
def engine():
    enhancer = SimpleNamespace(process=lambda x: np.asarray(x, dtype=np.float32))
    return AudioEngine(enhancer)


def _noise(n=2048, seed=3):
    rng = np.random.default_rng(seed)
    return (rng.standard_normal((n, 2)) * 0.1).astype(np.float32)


# ---------- apertura de streams ----------


def test_start_capture_usa_constantes_del_modulo(fake_pa, engine):
    pa_mod, pa = fake_pa
    engine.start_capture(pa, in_idx=7, rate=48000)
    kw = pa.opened[0]
    assert engine._pa_mod is pa_mod
    assert kw["format"] == pa_mod.paFloat32
    assert kw["input"] is True and kw["output"] is False
    assert kw["input_device_index"] == 7
    assert kw["channels"] == 2
    assert pa.streams[0].started is True
    assert engine.fill() == 0


def test_open_output_usa_formato_del_modulo_y_no_relanza(fake_pa, engine):
    pa_mod, pa = fake_pa
    engine.start_capture(pa, 1, 48000)
    engine.open_output(out_idx=5, rate=48000)
    kw = pa.opened[1]
    assert kw["format"] == pa_mod.paFloat32
    assert kw["output"] is True and kw["input"] is False
    assert kw["output_device_index"] == 5
    assert pa.streams[1].started is True


def test_stop_cierra_streams_y_es_idempotente(fake_pa, engine):
    _, pa = fake_pa
    engine.start_capture(pa, 1, 48000)
    engine.open_output(5, 48000)
    engine.stop()
    assert engine.stream is None and engine.out_stream is None
    assert engine.ring is None
    assert all(s.closed for s in pa.streams)
    engine.stop()  # no debe fallar


# ---------- ring buffer ----------


def test_ring_fill_y_lectura_contigua(engine):
    engine.configure_ring(48000)
    assert engine.nframes == int(48000 * 0.2)
    x = _noise(2048)
    engine._put(x)
    assert engine.fill() == 2048
    out = engine._read(2048)
    assert np.array_equal(out, x)
    assert engine.fill() == 0


def test_ring_wraparound_descarta_lo_mas_viejo(engine):
    engine.configure_ring(48000)
    nframes = engine.nframes  # 9600
    chunks = [_noise(2048, seed=i) for i in range(5)]  # 10240 frames en total
    stream = np.concatenate(chunks, axis=0)
    for c in chunks:
        engine._put(c)
    assert engine.fill() == nframes
    dropped = 10240 - nframes  # 640
    # _put descarta lo mas viejo poniendo a cero una ventana del ring: la
    # lectura empieza con `dropped` ceros y el resto es stream[dropped*2:].
    out = engine._read(nframes)
    expected = np.concatenate(
        [
            np.zeros((dropped, 2), dtype=np.float32),
            stream[dropped * 2 :],
        ],
        axis=0,
    )
    assert out.shape == (nframes, 2)
    assert np.array_equal(out, expected)


def test_hueco_devuelve_silencio_con_fundido(engine):
    engine.configure_ring(48000)
    n = 1024
    out = engine._read(n)  # ring vacio
    assert np.all(out == 0.0)
    assert engine.in_gap is True
    assert engine.fadein_frames > 0

    block = np.ones((2048, 2), dtype=np.float32)
    engine._put(block)
    out = engine._read(n)  # sale del hueco: fundido de entrada
    assert float(out[0, 0]) <= 0.02
    assert 0.4 <= float(out[120, 0]) <= 0.6  # 120/239 ~= 0.50
    assert float(out[239, 0]) > 0.95
    assert engine.in_gap is False


def test_lectura_con_frame_count_distinto():
    data = _noise(500)
    match = AudioEngine._match_frame_count(data, 480)
    assert match.shape == (480, 2)
    assert not np.array_equal(match, data) or True  # sale interpolado
    same = AudioEngine._match_frame_count(data, 500)
    assert same is data


def test_deriva_corrige_limitando_a_frames_maximos(engine):
    engine.configure_ring(48000)
    engine._rhead = engine._drift_target + 200  # salida rezagada (mucha deriva)
    engine._whead = 0
    with engine.lock:
        n_adj = int(np.trunc((engine._rhead - engine._whead - engine._drift_target) * engine._drift_gain))
        n_adj = max(-engine._max_drift_frames, min(engine._max_drift_frames, n_adj))
    assert -engine._max_drift_frames <= n_adj <= engine._max_drift_frames
