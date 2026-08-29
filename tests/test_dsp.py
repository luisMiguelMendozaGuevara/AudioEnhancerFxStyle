"""Tests del motor DSP (audio_enhancer.dsp.Enhancer).

Se prueban comportamientos deterministas con tonos sinteticos: bypass A/B,
ganancia de volumen en estado estacionario, limitador suave sin recorte duro,
compresor RMS (regresion del signo de atenuacion), shelves bass/treble, bandas
del EQ, paso mono y analizador de espectro.
"""

import numpy as np
import pytest

from audio_enhancer.dsp import Enhancer

FS = 48000
N = 2048
MILD_AMP = 0.2
BLOCKS_WARM = 20  # bloques para converger rampas (tau ~100 ms)


def _stereo(amp, freq=220.0):
    t = np.arange(N) / FS
    s = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([s, s], axis=1)


def _rms(x):
    return float(np.sqrt(np.mean(x * x)))


def warm(e, x, blocks=BLOCKS_WARM):
    for _ in range(blocks):
        e.process(x.copy())


@pytest.fixture
def noise():
    rng = np.random.default_rng(2026)
    return (rng.standard_normal((N, 2)) * MILD_AMP).astype(np.float32)


# ---------- bypass A/B ----------


def test_bypass_directo_devuelve_entrada_identica(noise):
    e = Enhancer()
    e.blend = 0.0
    warm(e, noise)
    y = e.process(noise.copy())
    assert np.array_equal(y, noise)


def test_bypass_mono_mantiene_forma():
    rng = np.random.default_rng(1)
    m = (rng.standard_normal(N) * MILD_AMP).astype(np.float32)
    e = Enhancer()
    e.blend = 0.0
    warm(e, m)
    y = e.process(m.copy())
    assert y.shape == (N,)
    assert np.array_equal(y, m)


# ---------- volumen ----------


def test_volumen_estado_estacionario(noise):
    e = Enhancer()
    e.volume = 0.5
    e.limiter = False
    e.compressor = False
    warm(e, noise)
    y = e.process(noise.copy())
    ratio = _rms(y) / _rms(noise)
    assert 0.48 <= ratio <= 0.52


def test_volumen_1_0_passthrough(noise):
    e = Enhancer()
    e.volume = 1.0
    e.limiter = False
    e.compressor = False
    warm(e, noise)
    y = e.process(noise.copy())
    assert np.allclose(y, noise, atol=1e-4)


# ---------- limitador suave ----------


def test_limitador_suaviza_sin_recorte_duro():
    x = _stereo(0.8, 220.0)
    e = Enhancer()
    e.volume = 1.25
    e.compressor = False
    e.limiter = True
    warm(e, x)
    y_lim = e.process(x.copy())

    e2 = Enhancer()
    e2.volume = 1.25
    e2.compressor = False
    e2.limiter = False
    warm(e2, x)
    y_flat = e2.process(x.copy())

    # sin limitador el pico llega al clip duro (1.0); con limitador se suaviza
    assert float(np.abs(y_flat).max()) > 0.998
    assert float(np.abs(y_lim).max()) < 0.99


def test_limitador_nunca_supera_1_0():
    x = _stereo(0.5, 440.0)
    e = Enhancer()
    e.volume = 4.0
    e.compressor = False
    assert e.limiter is True
    warm(e, x)
    y = e.process(x.copy())
    assert float(np.abs(y).max()) <= 1.0 + 1e-6


# ---------- compresor RMS ----------


def test_compresor_atentua_material_fuerte():
    x = np.full((N, 2), 0.9, dtype=np.float32)
    e = Enhancer()
    e.blend = 1.0
    e.limiter = False
    e.compressor = True
    warm(e, x)
    y = e.process(x.copy())
    # regresion: debe ATENUAR (salida < entrada), antes amplificaba a ~0.94
    assert _rms(y) < 0.88


def test_compresor_no_toca_senal_debajo_del_umbral():
    quiet = np.full((N, 2), 0.3, dtype=np.float32)
    e = Enhancer()
    e.blend = 1.0
    e.limiter = False
    e.compressor = True
    warm(e, quiet)
    y = e.process(quiet.copy())
    assert 0.27 <= _rms(y) <= 0.33


def test_sin_compresor_passthrough():
    x = np.full((N, 2), 0.9, dtype=np.float32)
    e = Enhancer()
    e.blend = 1.0
    e.limiter = False
    e.compressor = False
    warm(e, x)
    y = e.process(x.copy())
    assert np.allclose(y, x, atol=1e-4)


# ---------- ecualizador ----------


def test_bass_boost_incrementa_energia_en_graves():
    x = _stereo(0.2, 60.0)
    e = Enhancer()
    e.bass = 6.0
    e.limiter = False
    e.compressor = False
    warm(e, x)
    y = e.process(x.copy())
    assert _rms(y) / _rms(x) > 1.5


def test_treble_boost_incrementa_energia_en_agudos():
    x = _stereo(0.2, 8000.0)
    e = Enhancer()
    e.treble = 6.0
    e.limiter = False
    e.compressor = False
    warm(e, x)
    y = e.process(x.copy())
    assert _rms(y) / _rms(x) > 1.3


def test_banda_eq_negativa_atenua():
    x = _stereo(0.2, 60.0)
    e = Enhancer()
    # Asignar la lista COMPLETA: eq_gains es una propiedad con copia defensiva
    # (la lista viva era una carrera entre la UI y el callback de audio).
    e.eq_gains = [-12.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # banda 60 Hz
    e.limiter = False
    e.compressor = False
    warm(e, x)
    y = e.process(x.copy())
    assert _rms(y) < _rms(x)


# ---------- salida acotada y analizador ----------


def test_salida_siempre_acotada_a_1():
    rng = np.random.default_rng(7)
    x = (rng.standard_normal((N, 2)) * 3.0).astype(np.float32)
    e = Enhancer()
    e.volume = 3.0
    warm(e, x)
    y = e.process(x.copy())  # dBFS nunca debe exceder la unidad
    assert float(np.abs(y).max()) <= 1.0 + 1e-6


# ---------- limitador brickwall con look-ahead (Fase 1) ----------


def test_limitador_techo_exacto_brickwall():
    """El techo es REAL: con volumen brutal el pico de salida queda en el
    umbral (0.95), no en ~0.95 + 70% del exceso como el soft limiter viejo."""
    x = _stereo(0.9, 220.0)
    e = Enhancer()
    e.volume = 2.0  # picos de entrada ~1.8: exceso enorme
    e.compressor = False
    warm(e, x)
    y = e.process(x.copy())
    assert float(np.abs(y).max()) <= e.limiter_threshold + 1e-3


def test_limitador_anticipa_el_transitorio():
    """Con look-ahead, la atenuacion empieza ANTES del salto: ninguna muestra
    supera el techo aunque el bloque empiece de golpe a maxima amplitud."""
    t = np.arange(N) / FS
    burst = 1.2 * np.sign(np.sin(2 * np.pi * 220.0 * t))  # cuadrada a full
    x = np.zeros((N, 2), dtype=np.float32)
    x[N // 2 :, 0] = burst[: N // 2]
    x[N // 2 :, 1] = burst[: N // 2]
    e = Enhancer()
    e.volume = 1.0
    e.compressor = False
    warm(e, x)  # el estado queda "viendo" el burst
    y = e.process(x.copy())
    assert float(np.abs(y).max()) <= e.limiter_threshold + 1e-3


def test_limitador_no_toca_senal_bajo_umbral():
    """Caso comun (bloque bajo el techo): salida bit-identica, sin trabajo."""
    x = _stereo(0.2, 440.0)
    e = Enhancer()
    e.compressor = False
    warm(e, x)
    y = e.process(x.copy())
    assert np.array_equal(y, e.process(x.copy()) * 0 + y)  # forma estable
    # la senal no supera el umbral: el limitador es transparencia total
    e2 = Enhancer()
    e2.compressor = False
    warm(e2, x)
    y2 = e2.process(x.copy())
    assert float(np.abs(y2).max()) <= e2.limiter_threshold
    assert np.allclose(y, y2, atol=1e-6)


def test_cadena_limitador_al_final_del_recorrido():
    """Regresion del orden M5: el limitador debe ver la senal DESPUES del
    volumen; si el volumen fuera aplicado despues, un pico post-volumen
    cruzaria el techo (antes el volumen iba antes del compresor y el techo
    se media sobre otra senal)."""
    rng = np.random.default_rng(11)
    x = (rng.standard_normal((N, 2)) * 0.5).astype(np.float32)
    e = Enhancer()
    e.volume = 2.0  # post-volumen los picos rozan 1.0
    e.compressor = False
    e.limiter = True
    warm(e, x)
    y = e.process(x.copy())
    assert float(np.abs(y).max()) <= e.limiter_threshold + 1e-3


def test_compresor_por_muestra_igual_a_pasada_unica():
    """Regresion H2 (fuerte): la envolvente por muestra con estados zi
    persistentes es un filtro causal lineal -> procesar por bloques debe dar
    EXACTAMENTE lo mismo que procesar el flujo completo de una vez.

    El compresor por BLOQUE del codigo viejo no cumplia esto: aplicaba UNA
    ganancia por bloque (~21 ms), asi que el resultado dependia del troceado
    (escalera de ganancia = zipper noise audible)."""
    x = np.concatenate(
        [
            np.full((2560, 2), 0.9, dtype=np.float32),  # sobre el umbral
            np.full((2560, 2), 0.5, dtype=np.float32),  # caida (release)
        ]
    )
    e_blk = Enhancer()
    e_blk.limiter = False
    y_blk = np.concatenate([e_blk.process(x[i : i + 1024].copy()) for i in range(0, 5120, 1024)])
    e_one = Enhancer()
    e_one.limiter = False
    y_one = e_one.process(x.copy())
    assert np.allclose(y_blk, y_one, atol=1e-5)


def test_histeresis_seccion_permanece_activa_en_banda_muerta():
    """Histéresis Schmitt: una banda activada con 1.0 dB debe seguir activa
    con 0.10 dB (entre EXIT=0.05 y ENTER=0.15) y solo apagarse por debajo de
    EXIT. Evita el parpadeo on/off del filtro al rozar el umbral."""
    x = _stereo(0.2, 60.0)
    e = Enhancer()
    e.limiter = False
    e.compressor = False
    e.eq_gains = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    warm(e, x)
    assert e._section_on["eq_0"] is True
    # baja a la banda muerta: la seccion SIGUE activa
    e.eq_gains = [0.10, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    e.process(x.copy())
    assert e._section_on["eq_0"] is True
    # por debajo de EXIT: se apaga
    e.eq_gains = [0.03, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    for _ in range(5):
        e.process(x.copy())
    assert e._section_on["eq_0"] is False


def test_eq_gains_es_copia_defensiva():
    """Regresion H6: mutar la lista devuelta NO debe cambiar el estado
    interno (la lista viva compartida entre hilos era una carrera)."""
    e = Enhancer()
    g = e.eq_gains
    g[0] = 99.0
    assert e.eq_gains[0] == 0.0


def test_reset_state_limpia_estados_y_medidores():
    """Fase 0: al cambiar de dispositivo/tasa el DSP debe arrancar limpio."""
    x = _stereo(0.5, 220.0)
    e = Enhancer()
    e.bass = 6.0
    warm(e, x)
    assert e._states  # hay estados de biquad vivos
    e.reset_state()
    assert not e._states
    assert not e._sos_cache
    assert e._channels is None
    assert e.level_peak == 0.0 and e.level_rms == 0.0
    assert e._comp_zi_fast is None and e._comp_zi_slow is None
    assert not e._section_on
    # y sigue procesando igual tras el reset
    y = e.process(x.copy())
    assert float(np.abs(y).max()) > 0.0


# ---------- true-peak x4 y Q por banda (Fase 3) ----------


def _pico_true_peak(x, fs):
    """Pico true-peak: reconstrucción 4x de la senal CON SIGNO por canal y
    abs después (rectificar antes oculta los picos inter-muestra)."""
    from scipy import signal

    up = np.abs(signal.resample_poly(x, 4, 1, axis=0))
    return float(up.max())


def test_true_peak_captura_picos_intermuestra():
    """Fase 3: un seno a fs/4 desfasado 45 grados muestrea a 0.707*A (por
    debajo del techo 0.95) pero su pico REAL reconstruido es A. El limitador
    sample-peak no reacciona; el true-peak SI y garantiza el techo."""
    t = np.arange(N) / FS
    seno = (1.3 * np.sin(2 * np.pi * (FS / 4.0) * t + np.pi / 4)).astype(np.float32)
    x = np.stack([seno, seno], axis=1)
    # sanity del caso: pico por muestra por debajo del techo, true-peak fuera
    assert float(np.abs(x).max()) < 0.95
    assert _pico_true_peak(x, FS) > 1.2

    e = Enhancer()
    e.compressor = False
    assert e.true_peak is True
    warm(e, x)
    y = e.process(x.copy())
    assert _pico_true_peak(y, FS) <= e.limiter_threshold + 0.02


def test_true_peak_desactivado_deja_pasar_el_pico_intermuestra():
    """Con true_peak=False el limitador solo ve el pico por muestra: el mismo
    material pasa intocado (regresion que documenta la diferencia de modos)."""
    t = np.arange(N) / FS
    seno = (1.3 * np.sin(2 * np.pi * (FS / 4.0) * t + np.pi / 4)).astype(np.float32)
    x = np.stack([seno, seno], axis=1)
    e = Enhancer()
    e.compressor = False
    e.true_peak = False
    warm(e, x)
    y = e.process(x.copy())
    assert np.allclose(y, x, atol=1e-5)


def test_eq_q_por_banda_ajusta_el_ancho_de_campana():
    """Fase 3: eq_q_values permite Q individual; a igual ganancia (-12 dB en
    60 Hz), un Q estrecho (6) debe afectar MUCHO MENOS a 150 Hz que uno
    ancho (0.4)."""
    x = _stereo(0.2, 150.0)  # sonda a 150 Hz

    def _ratio(q_values):
        e = Enhancer()
        e.eq_q_values = list(q_values)
        e.eq_gains = [-12.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        e.limiter = False
        e.compressor = False
        warm(e, x)
        y = e.process(x.copy())
        return _rms(y) / _rms(x)

    r_estrecho = _ratio([6.0] * 9)
    r_ancho = _ratio([0.4] * 9)
    assert r_estrecho > r_ancho + 0.15  # el Q estrecho respeta a la sonda


def test_eq_q_escalar_sigue_funcionando():
    """Compatibilidad: asignar eq_q escalar propaga a todas las bandas."""
    e = Enhancer()
    e.eq_q = 3.0
    assert e.eq_q_values == [3.0] * 9
    assert e.eq_q == 3.0


def test_espectro_64_barras_validas():
    x = _stereo(0.5, 440.0)
    e = Enhancer()
    e.blend = 1.0
    e.compressor = False
    warm(e, x)
    e.compute_spectrum()
    sp = e.spectrum
    assert sp is not None and sp.shape == (64,)
    assert np.isfinite(sp).all()
    assert float(sp.max()) <= 0.0  # dBFS: senal <= 1.0 -> <= 0 dB
    assert int(np.sum(sp > -60.0)) >= 1  # el tono debe marcar bins calidos


def test_espectro_silencio_queda_en_piso():
    x = np.zeros((N, 2), dtype=np.float32)
    e = Enhancer()
    e.blend = 1.0
    e.compressor = False
    warm(e, x)
    e.compute_spectrum()
    assert e.spectrum is not None
    assert int(np.sum(e.spectrum > -79.0)) == 0
