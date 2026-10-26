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

    # Cebado (LEVELING_PRIME_S = 1 s) + suavizado (LEVELING_SLEW_S = 0,5 s):
    # se descartan los primeros 2 s para medir el régimen, no la entrada.
    asentado = niveles[100:]
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


def test_no_hay_clic_en_las_fronteras_de_bloque():
    """Sin estado persistente del filtro K habría un salto en cada frontera.

    Nota de diseño: la comparación "4096 en un bloque vs 4x1024" ya no aplica —
    el AGC suaviza su corrección POR BLOQUE (es lo correcto para un AGC lento),
    así que la granularidad cambia el resultado por diseño. La propiedad que sí
    debe cumplirse es que la señal no tenga discontinuidades en las fronteras:
    un filtro que se reiniciara por bloque dejaría un escalón audible.
    """
    e = _enhancer_con_nivelacion(10.0)
    audio = _seno(4.0, 0.1, freq=1000.0)
    salida = _procesar(e, audio).reshape(-1)

    saltos = np.abs(np.diff(salida.astype(np.float64)))
    # Pendiente natural del tono: 0.1 * 2*pi*1000/48000 = 0.0131 por muestra.
    pendiente = 0.1 * 2.0 * math.pi * 1000.0 / FS
    assert float(saltos.max()) < 1.5 * pendiente, (
        f"salto de {saltos.max():.4f} entre muestras (clic); la pendiente del tono es {pendiente:.4f}"
    )

    # Y las fronteras de bloque no son peores que el interior.
    fronteras = [i * CHUNK - 1 for i in range(1, len(audio) // CHUNK)]
    en_frontera = float(saltos[[f for f in fronteras if f < len(saltos)]].max())
    assert en_frontera <= float(np.percentile(saltos, 99.9)), "hay un escalón en las fronteras de bloque"


# ---------------------------------------------------------------------------
# 5. Contrato numérico (mutation thinking: 13 de 17 mutaciones sobrevivían)
# ---------------------------------------------------------------------------


def test_el_objetivo_es_menos_18_lufs_y_no_otro():
    """El objetivo se fija con un LITERAL, no leyendo la propia constante.

    Con la constante en la mano, cambiar LEVELING_TARGET_LUFS a -23 pasaba el
    test de convergencia (comparaba contra sí mismo).
    """
    audio = _seno(20.0, 10.0 ** (-26.0 / 20.0))
    salida = _lufs(_procesar(_enhancer_con_nivelacion(10.0), audio))

    assert salida == pytest.approx(-18.0, abs=1.0), f"salida {salida:.2f} LUFS (objetivo documentado -18)"


def test_el_tope_de_correccion_es_9_db_literal():
    """Material muy bajo pide +22 dB: el AGC no puede pasar de 9 dB (literal)."""
    audio = _seno(20.0, 0.01)  # -40 LUFS
    salida = _procesar(_enhancer_con_nivelacion(10.0), audio)
    ganancia_db = 20.0 * math.log10(max(_rms(salida) / _rms(audio), 1e-9))

    assert 8.2 <= ganancia_db <= 9.5, f"tope aplicado {ganancia_db:.2f} dB (documentado 9)"


def test_la_envolvente_es_lenta_de_verdad():
    """Tras una caída brusca de nivel, la envolvente tarda en bajar.

    Se mide el método directamente (la cadena tiene además un suavizado de
    bloque que enmascararía un ataque rápido): con una envolvente rápida
    (0.005 s / 0.35 s) el valor caería decenas de dB en 100 ms; con la lenta
    (0.5 s / 3 s) apenas se mueve.
    """
    e = _enhancer_con_nivelacion(10.0)
    fuerte = _seno(2.0, 10.0 ** (-12.0 / 20.0))
    debil = _seno(2.0, 10.0 ** (-30.0 / 20.0))

    # Asentar la envolvente con el material fuerte.
    for i in range(0, len(fuerte), CHUNK):
        e._loudness_env_db(fuerte[i : i + CHUNK])
    antes = float(np.median(e._loudness_env_db(fuerte[:CHUNK])))

    # 100 ms de material 18 dB más bajo: la envolvente no debe seguirlo.
    caida = 0.0
    for i in range(0, 5 * CHUNK, CHUNK):
        bloque = e._loudness_env_db(debil[i : i + CHUNK])
        caida = antes - float(np.median(bloque))

    assert caida < 3.0, f"la envolvente cayó {caida:.1f} dB en 100 ms (demasiado rápida para un AGC)"


def test_el_control_0_a_10_escala_la_correccion():
    """leveling=5 aplica aproximadamente la mitad de corrección que 10."""
    audio = _seno(20.0, 10.0 ** (-28.0 / 20.0))

    def ganancia(amount: float) -> float:
        salida = _procesar(_enhancer_con_nivelacion(amount), audio)
        return 20.0 * math.log10(max(_rms(salida) / _rms(audio), 1e-9))

    plena = ganancia(10.0)
    media = ganancia(5.0)

    assert media == pytest.approx(plena / 2.0, abs=0.8), f"5 -> {media:.2f} dB, 10 -> {plena:.2f} dB"


def test_con_el_compresor_activo_tambien_converge():
    """El AGC va DESPUÉS del compresor (si fuera antes, el compresor se comería el boost)."""
    e = _enhancer_con_nivelacion(10.0)
    e.compressor = True
    e.comp_threshold = 0.1  # muy bajo a propósito: comprime de verdad
    e.comp_ratio = 4.0
    audio = _seno(20.0, 10.0 ** (-26.0 / 20.0))

    salida = _lufs(_procesar(e, audio))

    assert salida == pytest.approx(-18.0, abs=2.0), f"salida {salida:.1f} LUFS con compresor"


def test_con_el_eq_activo_mide_la_senal_procesada_no_la_entrada():
    """Sube +6 dB una banda: el AGC debe compensarlo (mide después del EQ).

    Si midiera la entrada, la salida quedaría ~6 dB por encima del objetivo.
    """
    e = _enhancer_con_nivelacion(10.0)
    ganancias = [0.0] * len(e.eq_bands)
    indice = min(range(len(e.eq_bands)), key=lambda i: abs(e.eq_bands[i] - 1000.0))
    ganancias[indice] = 6.0
    e.eq_gains = ganancias
    audio = _seno(20.0, 10.0 ** (-26.0 / 20.0))

    salida = _lufs(_procesar(e, audio))

    assert salida == pytest.approx(-18.0, abs=1.5), f"salida {salida:.1f} LUFS con EQ +6 dB"


def test_el_silencio_hace_decaer_la_correccion_en_vez_de_congelarla():
    """En una pausa la corrección cae hacia la unidad (no se queda en +9 dB).

    Se probó primero a congelarla ("hold") y medido era peor: tras 6 s de pausa
    seguía en +9 dB y lo aplicaba al material que volviera. Con decaimiento y
    puerta rápida, el AGC vuelve a medir cuando hay audio.
    """
    e = _enhancer_con_nivelacion(10.0)
    callado = _seno(4.0, 10.0 ** (-30.0 / 20.0))
    for i in range(0, len(callado), CHUNK):
        e.process(callado[i : i + CHUNK])
    correccion_con_audio = e._lv_corr_db
    assert correccion_con_audio > 7.0  # material muy bajo: pide el tope

    silencio = np.zeros((CHUNK, 2), dtype=np.float32)
    for _ in range(100):  # ~2 s de pausa
        e.process(silencio)

    assert abs(e._lv_corr_db) < 1.0, (
        f"la correccion no decayo en la pausa: {correccion_con_audio:.2f} -> {e._lv_corr_db:.2f} dB"
    )


def test_el_material_fuerte_que_vuelve_tras_una_pausa_no_recibe_boost():
    """El defecto clásico de un AGC sin puerta rápida: +9 dB a la primera nota."""
    e = _enhancer_con_nivelacion(10.0)
    callado = _seno(4.0, 10.0 ** (-30.0 / 20.0))
    for i in range(0, len(callado), CHUNK):
        e.process(callado[i : i + CHUNK])
    assert e._lv_corr_db > 7.0  # está aplicando el tope

    silencio = np.zeros((CHUNK, 2), dtype=np.float32)
    for _ in range(100):  # ~2 s de pausa
        e.process(silencio)

    fuerte = _seno(4.0, 10.0 ** (-12.0 / 20.0))
    primer_bloque = e.process(fuerte[:CHUNK])
    ganancia_db = 20.0 * math.log10(max(_rms(primer_bloque) / _rms(fuerte[:CHUNK]), 1e-9))

    assert abs(ganancia_db) < 1.0, f"la primera nota tras la pausa recibio {ganancia_db:+.2f} dB"


def test_la_puerta_rapida_detecta_la_pausa_antes_que_la_envolvente():
    """La puerta usa un detector de 50 ms: cruza -70 LUFS mucho antes que la del AGC."""
    e = _enhancer_con_nivelacion(10.0)
    callado = _seno(2.0, 10.0 ** (-30.0 / 20.0))
    e.process(callado[:CHUNK])
    puerta_con_audio = e._lv_gate_db

    silencio = np.zeros((CHUNK, 2), dtype=np.float32)
    for _ in range(35):  # ~750 ms: detector de 25 ms + hold de 150 ms
        e.process(silencio)

    assert puerta_con_audio > -70.0
    assert e._lv_gate_db < -70.0, f"la puerta sigue abierta tras 750 ms de silencio ({e._lv_gate_db:.1f} LUFS)"


def test_un_hueco_corto_entre_notas_no_reinicia_el_agc():
    """Un silencio de 100 ms (staccato) NO debe tomarse por una pausa.

    Sin el hold de la puerta, cada hueco entre notas haría caer la corrección y
    volver a cebar: el AGC bombearía con material articulado.
    """
    e = _enhancer_con_nivelacion(10.0)
    callado = _seno(3.0, 10.0 ** (-30.0 / 20.0))
    for i in range(0, len(callado), CHUNK):
        e.process(callado[i : i + CHUNK])
    antes = e._lv_corr_db

    silencio = np.zeros((CHUNK, 2), dtype=np.float32)
    for _ in range(5):  # ~106 ms
        e.process(silencio)

    assert abs(e._lv_corr_db - antes) < 3.0, (
        f"un hueco de 100 ms movio la correccion {antes:.2f} -> {e._lv_corr_db:.2f} dB"
    )


def test_apagar_y_reactivar_no_produce_una_rafaga():
    """Al reactivar, el cebado impide que el primer bloque pegue +9 dB."""
    e = _enhancer_con_nivelacion(10.0)
    fuerte = _seno(3.0, 10.0 ** (-30.0 / 20.0))
    for i in range(0, len(fuerte), CHUNK):
        e.process(fuerte[i : i + CHUNK])

    # Apagar: el control se suaviza (tau ~30 ms), así que se dejan pasar varios
    # bloques hasta que el efecto quede realmente en 0 y limpie su estado.
    e.leveling = 0.0
    for _ in range(30):  # la rampa de apagado tarda ~14 bloques en llegar a 0
        e.process(fuerte[:CHUNK])
    assert not any(clave.startswith(("lvl_", "lvk")) for clave in e._env_zi)
    assert not any(clave.startswith("lvk") for clave in e._exc_zi)

    # Reactivar con material fuerte: el primer bloque no puede amplificar.
    e.leveling = 10.0
    entrada = fuerte[:CHUNK]
    salida = e.process(entrada)
    ganancia_db = 20.0 * math.log10(max(_rms(salida) / _rms(entrada), 1e-9))

    assert abs(ganancia_db) < 1.0, f"ráfaga al reactivar: {ganancia_db:+.2f} dB en el primer bloque"
