"""Exciters: armónicos del Bass exciter y anti-aliasing del Clarity.

Dos cambios de v1.8 con motivación medible:

1. **Bass exciter**: antes solo generaba el 2º armónico de una banda <100 Hz. Con
   una fundamental de 40 Hz eso cae en 80 Hz, donde un altavoz pequeño (corte
   real 150-250 Hz) no reproduce nada: el efecto era inaudible justo en los
   equipos donde se supone que ayuda, y en auriculares podía leerse como un
   error de octava. Ahora genera 2º **y 3er** armónico, y el 3º (120 Hz para una
   fundamental de 40 Hz) sí cae en la zona audible.

2. **Clarity**: la rectificación (|x|) para sacar el 2º armónico genera armónicos
   pares que, por encima de Nyquist, se pliegan de vuelta al espectro audible. El
   paso-bajo posterior no los quitaba porque el aliasing ya había ocurrido. Ahora
   la no linealidad usa anti-aliasing por antiderivada (ADAA).

Los valores de referencia de este archivo son MEDIDOS (comentados en cada test),
no inventados: si alguien cambia el generador de armónicos, el test lo dice.
"""

from __future__ import annotations

import numpy as np
import pytest

from audio_enhancer.constants import SAMPLE_RATE
from audio_enhancer.dsp import (
    BASS_EXCITER_HP3_HZ,
    BASS_EXCITER_LP_HZ,
    Enhancer,
)

FS = SAMPLE_RATE


def _stereo(amplitud: float, freq: float, segundos: float = 2.0) -> np.ndarray:
    n = int(segundos * FS)
    t = np.arange(n) / float(FS)
    x = (amplitud * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)
    return np.stack([x, x], axis=1)


def _enhancer(**efectos: float) -> Enhancer:
    e = Enhancer()
    e.sample_rate = FS
    e.compressor = False
    e.limiter = False
    # El DC blocker va siempre activo en producción y sí toca la señal: para
    # medir el exciter aislado se desactiva (igual que `_fx` en test_dsp.py).
    e.dc_block_enabled = False
    e.effects = {clave: efectos.get(clave, 0.0) for clave in e.effects}
    return e


def _procesar_asentado(e: Enhancer, x: np.ndarray, calentar: int = 10) -> np.ndarray:
    """Procesa ``x`` tras calentar el estado (rampas de activación)."""
    for _ in range(calentar):
        e.process(x.copy())
    return e.process(x.copy())


def _energia(x: np.ndarray, freq: float) -> float:
    """Magnitud espectral en ``freq`` (ventana de Hann, ±1 bin)."""
    v = np.asarray(x, dtype=np.float64)
    if v.ndim > 1:
        v = v[:, 0]
    espectro = np.abs(np.fft.rfft(v * np.hanning(v.shape[0])))
    frecuencias = np.fft.rfftfreq(v.shape[0], 1.0 / FS)
    i = int(np.argmin(np.abs(frecuencias - freq)))
    return float(espectro[max(0, i - 1) : i + 2].max())


def _db_rel(valor: float, referencia: float) -> float:
    return 20.0 * np.log10(max(valor, 1e-15) / max(referencia, 1e-15))


# ---------------------------------------------------------------------------
# 1. Bass exciter: 2º Y 3er armónico
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("f0", [40.0, 60.0, 80.0])
def test_bass_genera_segundo_y_tercer_armonico(f0):
    """A cantidad 10: 2F ≈ -15 dB y 3F ≈ -23 dB respecto a la fundamental.

    Valores medidos con este código (no teóricos): 2F -14,5 / -14,7 / -15,6 dB y
    3F -24,1 / -22,2 / -22,6 dB. Los márgenes son amplios a propósito, pero
    acotados: si el 3F desapareciera (como antes, a -37 dB) el test falla.
    """
    x = _stereo(0.3, f0)
    y = _procesar_asentado(_enhancer(bass=10.0), x)
    fundamental = _energia(y, f0)

    segundo = _db_rel(_energia(y, 2.0 * f0), fundamental)
    tercero = _db_rel(_energia(y, 3.0 * f0), fundamental)

    assert -20.0 <= segundo <= -9.0, f"2F a {segundo:.1f} dB (esperado ~-15)"
    assert -30.0 <= tercero <= -15.0, f"3F a {tercero:.1f} dB (esperado ~-23)"


def test_el_tercer_armonico_cae_donde_el_altavoz_pequeno_si_reproduce():
    """Con una fundamental de 40 Hz, el 3F está en 120 Hz (audible) y el 2F en 80.

    Es la razón del cambio: el 2º solo no llega a la zona útil de un altavoz
    pequeño. Se comprueba que el 3F (120 Hz) tiene energía real, no residual.
    """
    x = _stereo(0.3, 40.0)
    y = _procesar_asentado(_enhancer(bass=10.0), x)

    assert 3.0 * 40.0 >= 120.0
    assert _db_rel(_energia(y, 120.0), _energia(y, 40.0)) > -30.0


def test_el_exciter_de_graves_no_toca_la_fundamental():
    """No es un refuerzo de graves: la fundamental debe quedar igual (±1 dB)."""
    x = _stereo(0.3, 60.0)
    y = _procesar_asentado(_enhancer(bass=10.0), x)

    assert abs(_db_rel(_energia(y, 60.0), _energia(x, 60.0))) < 1.0


def test_el_exciter_de_graves_no_anade_continua_ni_subsonido():
    """Los armónicos se generan sin DC y sin energía por debajo de la banda."""
    x = _stereo(0.3, 40.0)
    y = _procesar_asentado(_enhancer(bass=10.0), x)

    assert abs(float(np.mean(y))) < 1e-6, "se añadió componente continua"
    assert _db_rel(_energia(y, 20.0), _energia(y, 40.0)) < -40.0, "hay energía sub-40 Hz"


def test_la_banda_de_entrada_del_exciter_cubre_los_graves_musicales():
    """La banda llega a 120 Hz (antes 100): cubre los graves típicos."""
    assert BASS_EXCITER_LP_HZ >= 120.0
    assert BASS_EXCITER_HP3_HZ >= 120.0  # el 3F empieza donde acaba la banda


# ---------------------------------------------------------------------------
# 2. Clarity: anti-aliasing por antiderivada (ADAA)
# ---------------------------------------------------------------------------


def test_adaa_reduce_el_aliasing_de_la_rectificacion():
    """Con un tono de 11 kHz, el 4º armónico (44 kHz) se pliega a 4 kHz.

    Medido: sin ADAA el alias queda a -13,8 dB del 2º armónico útil (¡audible!);
    con ADAA, a -31,6 dB. Se exige una mejora de al menos 10 dB.
    """
    x = _stereo(0.25, 11000.0)

    def alias_relativo(adaa: bool) -> float:
        e = Enhancer()
        e.sample_rate = FS
        h = e._harmonic_2nd(x, "test_h2", adaa=adaa)
        return _db_rel(_energia(h, 4000.0), _energia(h, 22000.0))

    sin_adaa = alias_relativo(False)
    con_adaa = alias_relativo(True)

    assert con_adaa < sin_adaa - 10.0, f"ADAA solo mejora {sin_adaa - con_adaa:.1f} dB"
    assert con_adaa < -25.0, f"el alias sigue alto con ADAA: {con_adaa:.1f} dB"


def test_el_adaa_no_debilita_el_armonico_util():
    """La compensación de ganancia mantiene el 2º armónico al mismo nivel."""
    x = _stereo(0.25, 11000.0)
    e = Enhancer()
    e.sample_rate = FS

    h_con = e._harmonic_2nd(x, "a", adaa=True)
    h_sin = e._harmonic_2nd(x, "b", adaa=False)

    diferencia_db = _db_rel(_energia(h_con, 22000.0), _energia(h_sin, 22000.0))
    assert abs(diferencia_db) < 2.0, f"el armónico cambió {diferencia_db:+.1f} dB con ADAA"


def test_el_alias_de_la_cadena_de_clarity_queda_muy_bajo():
    """En la cadena real (HP 3 kHz + 2F + LP 16 kHz) el alias queda bajo control."""
    x = _stereo(0.25, 11000.0)
    e = _enhancer(fidelity=10.0)
    y = _procesar_asentado(e, x)

    fundamental = _energia(x, 11000.0)
    # 4 kHz y 8 kHz son alias de 4F y 6F. Se referencian a la fundamental de la
    # entrada (el paso-bajo de 16 kHz atenúa el 2F de 22 kHz, así que comparar
    # contra él exageraría el alias). Medido: -50,8 dB en 4 kHz y -65 dB en 8 kHz.
    assert _db_rel(_energia(y, 4000.0), fundamental) < -40.0, "alias audible en 4 kHz"
    assert _db_rel(_energia(y, 8000.0), fundamental) < -40.0, "alias audible en 8 kHz"


def test_clarity_sigue_generando_el_segundo_armonico():
    """El ADAA no puede haberse cargado el efecto: a 3 kHz debe aparecer 6 kHz."""
    x = _stereo(0.2, 3000.0)

    apagado = _procesar_asentado(_enhancer(fidelity=0.0), x)
    encendido = _procesar_asentado(_enhancer(fidelity=10.0), x)

    assert _db_rel(_energia(encendido, 6000.0), _energia(apagado, 6000.0)) > 10.0


# ---------------------------------------------------------------------------
# 3. Ambos: apagados y robustez
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("clave", ["bass", "fidelity"])
def test_exciters_apagados_son_passthrough(clave):
    x = _stereo(0.2, 3000.0 if clave == "fidelity" else 60.0)
    y = _procesar_asentado(_enhancer(**{clave: 0.0}), x)

    assert np.allclose(y, x, atol=1e-4)


@pytest.mark.parametrize("clave", ["bass", "fidelity"])
def test_exciters_robustos_con_material_extremo(clave):
    """Ruido a fondo de escala: sin NaN y con la salida acotada."""
    rng = np.random.default_rng(11)
    x = (rng.standard_normal((FS, 2)) * 0.9).astype(np.float32)
    e = _enhancer(**{clave: 10.0})
    e.limiter = True

    y = _procesar_asentado(e, x)

    assert np.isfinite(y).all()
    assert float(np.abs(y).max()) <= 1.0
