"""El Leveling mide SONORIDAD (LUFS), no RMS.

Por qué existe este archivo: el AGC antiguo normalizaba por RMS, y el RMS no es
sonoridad percibida. El oído pesa distinto las frecuencias (de ahí el filtro K de
ITU-R BS.1770) y la energía de los graves molesta mucho menos que la de los
medios. Dos consecuencias prácticas:

1. Con RMS, una pista con mucho grave se queda baja de volumen percibido y una
   brillante sube de más.
2. Con RMS, el AGC reacciona a la energía, no a lo que se oye: en material con
   bajo fuerte "bombea" sin que el usuario entienda por qué.

Los tests miden la salida con ``LoudnessMeter`` (validado contra EBU Tech 3341 en
test_loudness.py), no con el propio AGC: la referencia es externa.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from audio_enhancer.constants import SAMPLE_RATE
from audio_enhancer.dsp import LEVELING_MAX_DB, LEVELING_TARGET_LUFS, Enhancer
from audio_enhancer.loudness import LoudnessMeter

FS = SAMPLE_RATE
CHUNK = 1024


def _enhancer_con_nivelacion(amount: float) -> Enhancer:
    """Enhancer con la cadena neutra salvo la nivelación pedida."""
    e = Enhancer()
    e.sample_rate = FS
    e.effects = dict.fromkeys(e.effects, 0.0)
    e.leveling = float(amount)
    e.compressor = False
    e.limiter = False
    e.volume = 1.0
    e.master_gain_db = 0.0
    e.bass = 0.0
    e.treble = 0.0
    e.eq_gains = [0.0] * len(e.eq_bands)
    return e


def _seno(segundos: float, amplitud: float, freq: float = 1000.0) -> np.ndarray:
    n = int(segundos * FS)
    t = np.arange(n) / float(FS)
    x = (amplitud * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)
    return np.stack([x, x], axis=1)


def _procesar(e: Enhancer, audio: np.ndarray) -> np.ndarray:
    return np.concatenate(
        [e.process(audio[i : i + CHUNK]) for i in range(0, len(audio), CHUNK)],
        axis=0,
    )


def _lufs(audio: np.ndarray) -> float:
    """Sonoridad integrada (BS.1770) de la señal, medida con el medidor real."""
    medidor = LoudnessMeter(FS, 2)
    for i in range(0, len(audio), CHUNK):
        medidor.process(audio[i : i + CHUNK])
    return medidor.integrated_lufs


def _rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(audio, dtype=np.float64) ** 2)))


# ---------------------------------------------------------------------------
# 1. Comportamiento básico
# ---------------------------------------------------------------------------


def test_nivelacion_apagada_no_aplica_agc_ni_crea_estado():
    """Con amount=0 no hay corrección de nivel y no se crea estado del AGC.

    No se compara muestra a muestra contra la entrada: el DC blocker de 10 Hz
    (siempre activo) sí cambia la señal unos micrómetros. Lo que importa es que
    el AGC no actúe y no deje estado (ni filtro K ni envolventes), porque de eso
    depende que al subir el control el efecto arranque limpio.
    """
    e = _enhancer_con_nivelacion(0.0)
    audio = _seno(2.0, 0.1)
    salida = _procesar(e, audio)

    ganancia_db = 20.0 * math.log10(max(_rms(salida) / _rms(audio), 1e-9))
    assert abs(ganancia_db) < 0.05, f"el AGC actuó con amount=0 ({ganancia_db:+.3f} dB)"
    assert not any(clave.startswith(("lvl_", "lvk")) for clave in e._env_zi)
    assert not any(clave.startswith("lvk") for clave in e._exc_zi)


def test_silencio_no_produce_nan_ni_ganancia_loca():
    """En silencio no hay medida: no debe amplificar ni envenenar el estado."""
    e = _enhancer_con_nivelacion(10.0)
    silencio = np.zeros((CHUNK * 20, 2), dtype=np.float32)

    salida = _procesar(e, silencio)

    assert np.isfinite(salida).all()
    assert float(np.abs(salida).max()) == 0.0


# ---------------------------------------------------------------------------
# 2. La propiedad que justifica el cambio: convergencia al objetivo en LUFS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nivel_inicial_lufs", [-26.0, -12.0])
def test_el_nivel_sale_cerca_del_objetivo_en_lufs(nivel_inicial_lufs):
    """Material a -26 y a -12 LUFS converge al objetivo (-18) con amount=10.

    La amplitud se elige con la relación exacta de la calibración de BS.1770: un
    seno estéreo en fase de 1 kHz con amplitud A mide 20*log10(A) LUFS (el -0.691
    de la ecuación y la ganancia +0.691 del filtro K se cancelan).
    """
    amplitud = 10.0 ** (nivel_inicial_lufs / 20.0)
    audio = _seno(20.0, amplitud)
    entrada = _lufs(audio)
    assert entrada == pytest.approx(nivel_inicial_lufs, abs=0.2)  # precondición

    salida = _lufs(_procesar(_enhancer_con_nivelacion(10.0), audio))

    # Se descarta el transitorio: la envolvente es lenta (ataque 0,5 / release 3 s)
    # y en 20 s el AGC ya está asentado.
    assert salida == pytest.approx(LEVELING_TARGET_LUFS, abs=2.5), f"salida {salida:.1f} LUFS"


def test_sin_nivelacion_los_dos_niveles_siguen_muy_separados():
    """Control negativo: sin AGC la diferencia entre los dos materiales es grande."""
    bajo = _lufs(_procesar(_enhancer_con_nivelacion(0.0), _seno(10.0, 10.0 ** (-26.0 / 20.0))))
    alto = _lufs(_procesar(_enhancer_con_nivelacion(0.0), _seno(10.0, 10.0 ** (-12.0 / 20.0))))

    assert alto - bajo == pytest.approx(14.0, abs=0.5)


def test_la_correccion_se_acota_a_mas_menos_nueve_db():
    """Nunca más de ±LEVELING_MAX_DB, por muy lejos que esté el material."""
    e = _enhancer_con_nivelacion(10.0)
    audio = _seno(10.0, 0.01)  # -40 LUFS: pediría +22 dB

    salida = _procesar(e, audio)
    ganancia_db = 20.0 * math.log10(max(_rms(salida) / _rms(audio), 1e-9))

    assert ganancia_db <= LEVELING_MAX_DB + 0.5


# ---------------------------------------------------------------------------
# 3. La diferencia real frente al RMS: la ponderación frecuencial
# ---------------------------------------------------------------------------


def test_grave_y_medio_con_el_mismo_rms_reciben_correcciones_distintas():
    """Mismo RMS, distinta sonoridad: el grave recibe MÁS ganancia.

    Es la prueba de que el AGC mide con el filtro K y no con RMS. Con RMS ambas
    correcciones serían idénticas (era el defecto del AGC antiguo). El filtro K
    atenúa los graves (RLB a 38 Hz), así que un tono de 60 Hz "suena" menos que
    uno de 2 kHz con la misma energía.
    """
    def ganancia_aplicada(freq: float) -> float:
        e = _enhancer_con_nivelacion(10.0)
        audio = _seno(15.0, 0.05, freq=freq)
        salida = _procesar(e, audio)
        return 20.0 * math.log10(max(_rms(salida) / _rms(audio), 1e-9))

    grave = ganancia_aplicada(60.0)
    medio = ganancia_aplicada(2000.0)

    assert grave > medio + 1.5, f"grave {grave:.2f} dB vs medio {medio:.2f} dB (sin diferencia = RMS)"


def test_la_diferencia_de_medida_entre_grave_y_medio_es_la_del_filtro_k():
    """Cuantifica el efecto: a 60 Hz la sonoridad medida es ~4-5 LU menor que a 2 kHz."""
    grave = _lufs(_seno(10.0, 0.05, freq=60.0))
    medio = _lufs(_seno(10.0, 0.05, freq=2000.0))

    assert 3.0 < (medio - grave) < 7.0, f"60 Hz {grave:.1f} vs 2 kHz {medio:.1f} LUFS"


# ---------------------------------------------------------------------------
# 4. Estabilidad: el AGC no debe "bombear"
# ---------------------------------------------------------------------------


def test_el_agc_es_estable_y_no_bombea():
    """Un tono estacionario no oscila: el nivel por bloque varía < 1 dB.

    Es la razón de los tiempos lentos (ataque 0,5 s / release 3 s). Con una
    envolvente rápida, un AGC sobre música "respira" y se oye.
    """
    e = _enhancer_con_nivelacion(10.0)
    audio = _seno(30.0, 0.03)
    niveles = []
    for i in range(0, len(audio), CHUNK):
        salida = e.process(audio[i : i + CHUNK])
        niveles.append(20.0 * math.log10(max(_rms(salida), 1e-9)))

    asentado = niveles[20:]  # primeros ~0,4 s: rampa de activación
    saltos = [abs(b - a) for a, b in zip(asentado, asentado[1:], strict=False)]

    assert max(saltos) < 1.0, f"salto de {max(saltos):.2f} dB entre bloques (bombeo)"
    assert max(asentado) - min(asentado) < 2.0, "el nivel no se asienta"


def test_el_agc_convive_con_la_cadena_completa_sin_nan():
    """Con compresor, limitador y EQ activos la nivelación sigue siendo finita."""
    e = Enhancer()
    e.sample_rate = FS
    e.leveling = 8.0
    audio = _seno(10.0, 0.3)

    salida = _procesar(e, audio)

    assert np.isfinite(salida).all()
    assert float(np.abs(salida).max()) <= 1.0


def test_el_estado_del_filtro_k_persiste_entre_bloques():
    """Sin estado persistente aparecería un transitorio en cada frontera.

    Se compara procesar 4096 en un bloque contra 4x1024: si el filtro K se
    reiniciara por bloque, la diferencia sería audible (no ~1e-6).
    """
    audio = _seno(2.0, 0.1)

    entero = _enhancer_con_nivelacion(10.0).process(audio.copy())

    por_bloques = _enhancer_con_nivelacion(10.0)
    trozos = [por_bloques.process(t) for t in np.array_split(audio.copy(), 4)]
    partes = np.concatenate(trozos, axis=0)

    escala = max(float(np.abs(entero).max()), 1e-6)
    assert float(np.abs(partes - entero).max()) / escala < 1e-5
