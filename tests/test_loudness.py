"""Validación del medidor de sonoridad contra las normas (no contra sí mismo).

Los valores esperados de estos tests NO son inventados por el proyecto: salen de

- **ITU-R BS.1770-4**, anexo 1: tabla oficial de coeficientes del filtro K a
  48 kHz (se compara dígito a dígito) y definición de la ecuación de sonoridad.
- **EBU Tech 3341-2023**, tabla 1 ('minimum requirements test signals'): los
  casos 1 a 5 son los que debe pasar cualquier medidor que quiera llamarse
  'EBU Mode'. Tolerancia oficial: ±0.1 LUFS.
- **EBU R 128**: objetivo -23.0 LUFS; **EBU Tech 3342**: LRA con gate -20 LU.

Si estos tests cambian, hay que cambiar la referencia normativa, no la
tolerancia. Medido en esta máquina: caso 1 = -22.993 LUFS (desviación 0.007 LU).
"""

import math

import numpy as np
import pytest

from audio_enhancer.loudness import (
    GATE_ABSOLUTE_LUFS,
    LOUDNESS_OFFSET_DB,
    LoudnessMeter,
    _scipy_signal,
    energy_to_lufs,
    k_weighting_sos,
    loudness_gain_db,
)

FS = 48000

# Tolerancia oficial de EBU Tech 3341 para los casos de la tabla 1.
TOL_LU = 0.1

# Tabla oficial de coeficientes del filtro K a 48 kHz (ITU-R BS.1770-4, anexo 1).
# Ojo a la etapa 2: el numerador NO va normalizado por a0 en el estándar.
BS1770_TABLA_48K = np.asarray(
    [
        [1.53512485958697, -2.69169618940638, 1.19839281085285, 1.0, -1.69065929318241, 0.73248077421585],
        [1.0, -2.0, 1.0, 1.0, -1.99004745483398, 0.99007225036621],
    ]
)


def _seno(segundos: float, dbfs: float, freq: float = 1000.0, sample_rate: int = FS) -> np.ndarray:
    """Seno estéreo en fase con PICO a ``dbfs`` (definición de EBU Tech 3341)."""
    amplitud = 10.0 ** (dbfs / 20.0)
    n = int(round(segundos * sample_rate))
    t = np.arange(n) / float(sample_rate)
    x = (amplitud * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)
    return np.stack([x, x], axis=1)


def _medir(partes, sample_rate: int = FS) -> LoudnessMeter:
    """Reproduce secuencias (segundos, dBFS) y devuelve el medidor."""
    _scipy_signal()  # precalentar scipy: la primera llamada importa el módulo
    medidor = LoudnessMeter(sample_rate, 2)
    for segundos, dbfs in partes:
        audio = _seno(segundos, dbfs, sample_rate=sample_rate)
        for i in range(0, len(audio), 1024):
            medidor.process(audio[i : i + 1024])
    return medidor


# ---------------------------------------------------------------------------
# 1. El filtro K coincide con la tabla del estándar
# ---------------------------------------------------------------------------


def test_filtro_k_coincide_con_la_tabla_de_bs1770_a_48k():
    """Los coeficientes son, dígito a dígito, los del anexo 1 de BS.1770-4.

    Este test es el que cazó un error real: normalizar el numerador de la etapa
    2 por a0 (la convención DSP habitual) resta 20*log10(a0) = 0.0435 dB y
    desvía TODA la medida 0.04 LU.
    """
    nuestra = k_weighting_sos(48000)

    assert np.abs(nuestra - BS1770_TABLA_48K).max() < 1e-12


@pytest.mark.parametrize("freq_hz", [997.0])
def test_ganancia_del_filtro_k_en_997_hz_es_0691_db(freq_hz):
    """A 997 Hz el filtro K vale +0.691 dB, que es justo lo que cancela el -0.691
    de la ecuación de sonoridad. Si esto se rompe, todos los LUFS se desplazan."""
    respuesta = _scipy_signal().sosfreqz(k_weighting_sos(48000), worN=np.array([freq_hz]), fs=48000)[1]
    ganancia_db = 20.0 * math.log10(float(np.abs(respuesta[0])))

    assert ganancia_db == pytest.approx(0.691, abs=0.005)


# ---------------------------------------------------------------------------
# 2. EBU Tech 3341, tabla 1: casos 1-5 (material oficial de conformidad)
# ---------------------------------------------------------------------------


def test_ebu3341_caso1_seno_estereo_1khz_menos23_dbfs():
    """Caso 1: M, S, I = -23.0 ±0.1 LUFS (y 0.0 LU en escala relativa)."""
    medidor = _medir([(20.0, -23.0)])

    assert medidor.momentary_lufs == pytest.approx(-23.0, abs=TOL_LU)
    assert medidor.short_term_lufs == pytest.approx(-23.0, abs=TOL_LU)
    assert medidor.integrated_lufs == pytest.approx(-23.0, abs=TOL_LU)


def test_ebu3341_caso2_seno_estereo_1khz_menos33_dbfs():
    """Caso 2: -33.0 LUFS. Comprueba que la escala es lineal en 10 dB."""
    medidor = _medir([(20.0, -33.0)])

    assert medidor.integrated_lufs == pytest.approx(-33.0, abs=TOL_LU)


def test_ebu3341_caso3_el_gate_relativo_excluye_los_pasajes_bajos():
    """Caso 3: 10 s a -36, 60 s a -23, 10 s a -36 => I = -23.0.

    Sin el gate relativo de -10 LU el resultado sería ~-24.2 LUFS: el test
    demuestra que los pasajes 13 LU por debajo NO cuentan.
    """
    medidor = _medir([(10.0, -36.0), (60.0, -23.0), (10.0, -36.0)])

    assert medidor.integrated_lufs == pytest.approx(-23.0, abs=TOL_LU)


def test_ebu3341_caso4_el_gate_absoluto_ignora_el_silencio_profundo():
    """Caso 4: añade 10 s a -72 dBFS (por debajo del gate absoluto -70)."""
    medidor = _medir([(10.0, -72.0), (10.0, -36.0), (60.0, -23.0), (10.0, -36.0), (10.0, -72.0)])

    assert medidor.integrated_lufs == pytest.approx(-23.0, abs=TOL_LU)


def test_ebu3341_caso5_el_gate_relativo_pondera_por_energia():
    """Caso 5: 20 s a -26, 20.1 s a -20, 20 s a -26 => I = -23.0."""
    medidor = _medir([(20.0, -26.0), (20.1, -20.0), (20.0, -26.0)])

    assert medidor.integrated_lufs == pytest.approx(-23.0, abs=TOL_LU)


def test_calibracion_ebu_tech_3341_seccion_2_9():
    """Tech 3341 §2.9: seno 1 kHz con pico a -18 dBFS => el medidor lee -18.0."""
    medidor = _medir([(10.0, -18.0)])

    assert medidor.integrated_lufs == pytest.approx(-18.0, abs=TOL_LU)


# ---------------------------------------------------------------------------
# 3. Ventanas de 'EBU Mode': M = 400 ms, S = 3 s
# ---------------------------------------------------------------------------


def test_momentary_necesita_400_ms_y_short_term_3_s():
    """Antes de llenar la ventana la medida no es válida (-inf), y luego sí."""
    _scipy_signal()
    medidor = LoudnessMeter(FS, 2)
    bloque = _seno(0.1, -23.0)  # tramos de 100 ms

    medidor.process(bloque)  # 100 ms
    assert medidor.momentary_lufs == -math.inf
    medidor.process(bloque)  # 200 ms
    medidor.process(bloque)  # 300 ms
    assert medidor.momentary_lufs == -math.inf
    medidor.process(bloque)  # 400 ms: ventana completa
    assert medidor.momentary_lufs == pytest.approx(-23.0, abs=TOL_LU)
    assert medidor.short_term_lufs == -math.inf  # 3 s aún no
    for _ in range(26):  # hasta 3 s
        medidor.process(bloque)
    assert medidor.short_term_lufs == pytest.approx(-23.0, abs=TOL_LU)


def test_integrada_necesita_un_bloque_de_400_ms():
    """El integrado solo existe desde el primer bloque de 400 ms completo."""
    _scipy_signal()
    medidor = LoudnessMeter(FS, 2)
    assert medidor.integrated_lufs == -math.inf
    medidor.process(_seno(0.1, -23.0))
    assert medidor.integrated_lufs == -math.inf  # 100 ms < 400 ms
    for _ in range(3):
        medidor.process(_seno(0.1, -23.0))
    assert medidor.integrated_lufs == pytest.approx(-23.0, abs=TOL_LU)


# ---------------------------------------------------------------------------
# 4. Silencio, robustez y ausencia de NaN
# ---------------------------------------------------------------------------


def test_silencio_total_no_produce_nan_y_da_menos_infinito():
    """Un medidor que devolviera NaN envenenaría la telemetría de la UI."""
    _scipy_signal()
    medidor = LoudnessMeter(FS, 2)
    for _ in range(50):
        medidor.process(np.zeros((1024, 2), dtype=np.float32))

    valores = medidor.snapshot()

    assert all(not math.isnan(v) for v in valores.values())
    assert valores["integrated_lufs"] == -math.inf
    assert valores["momentary_lufs"] == -math.inf
    # El LRA de un silencio es 0 (no hay variación que medir).
    assert valores["lra_lu"] == 0.0


def test_un_bloque_gigante_equivale_a_bloques_pequenos():
    """La medida no puede depender del tamaño de bloque (streaming correcto)."""
    _scipy_signal()
    audio = _seno(6.0, -23.0)

    grande = LoudnessMeter(FS, 2)
    grande.process(audio)

    menudo = LoudnessMeter(FS, 2)
    for i in range(0, len(audio), 1024):
        menudo.process(audio[i : i + 1024])

    assert grande.integrated_lufs == pytest.approx(menudo.integrated_lufs, abs=1e-6)
    assert grande.short_term_lufs == pytest.approx(menudo.short_term_lufs, abs=1e-6)


def test_entrada_mono_se_duplica_y_sobrante_se_recorta():
    """Un solo canal se mide como estéreo (misma sonoridad); más canales se
    recortan a los soportados sin lanzar."""
    _scipy_signal()
    mono = _seno(6.0, -23.0)[:, :1]  # (N, 1)

    medidor = LoudnessMeter(FS, 2)
    for i in range(0, len(mono), 1024):
        medidor.process(mono[i : i + 1024])
    referencia = _medir([(6.0, -23.0)])

    assert medidor.integrated_lufs == pytest.approx(referencia.integrated_lufs, abs=TOL_LU)

    cuatro = np.repeat(_seno(1.0, -23.0), 2, axis=1)  # (N, 4)
    medidor2 = LoudnessMeter(FS, 2)
    medidor2.process(cuatro)
    assert math.isfinite(medidor2.momentary_lufs)


def test_reset_reinicia_todo_y_reset_integrated_conserva_m_y_s():
    """El reset de un medidor EBU borra el integrado; M y S pueden sobrevivir."""
    _scipy_signal()
    medidor = _medir([(6.0, -23.0)])
    assert math.isfinite(medidor.integrated_lufs)

    medidor.reset_integrated()
    assert medidor.integrated_lufs == -math.inf
    assert medidor.momentary_lufs == pytest.approx(-23.0, abs=TOL_LU)  # M intacta

    medidor.reset()
    assert medidor.momentary_lufs == -math.inf
    assert len(medidor) == 0


# ---------------------------------------------------------------------------
# 5. Utilidades de ganancia (base del futuro AGC por sonoridad)
# ---------------------------------------------------------------------------


def test_energy_to_lufs_y_offset_del_estandar():
    """La ecuación 2 de BS.1770 es -0.691 + 10*log10(energía)."""
    assert pytest.approx(-0.691) == LOUDNESS_OFFSET_DB
    assert energy_to_lufs(1.0) == pytest.approx(-0.691)
    assert energy_to_lufs(0.0) == -math.inf
    assert energy_to_lufs(float("nan")) == -math.inf


def test_loudness_gain_db_acota_y_no_reacciona_al_silencio():
    """Sin esta salvaguarda, un AGC subiría al máximo en cada pausa."""
    assert loudness_gain_db(-30.0, -23.0) == pytest.approx(7.0)
    assert loudness_gain_db(-10.0, -23.0) == pytest.approx(-9.0)  # acotado a 9 dB
    assert loudness_gain_db(-30.0, -23.0, max_db=3.0) == pytest.approx(3.0)
    # Silencio / NaN => sin corrección.
    assert loudness_gain_db(-math.inf, -23.0) == 0.0
    assert loudness_gain_db(float("nan"), -23.0) == 0.0


# ---------------------------------------------------------------------------
# 6. Otras tasas de muestreo y rango de sonoridad
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sample_rate", [44100, 96000])
def test_caso1_en_otras_tasas_sigue_dentro_de_tolerancia(sample_rate):
    """El filtro se recalcula por fórmula, no por tabla: debe cumplir en 44,1 y 96k."""
    medidor = _medir([(20.0, -23.0)], sample_rate=sample_rate)

    assert medidor.integrated_lufs == pytest.approx(-23.0, abs=TOL_LU)


def test_lra_de_un_tono_constante_es_cero():
    """Sin variación de sonoridad no hay rango."""
    medidor = _medir([(10.0, -23.0)])

    assert medidor.loudness_range_lu == pytest.approx(0.0, abs=0.1)


def test_lra_de_dos_niveles_separados_10_lu_es_10():
    """Dos tramos separados 10 LU (20 s cada uno) dan un LRA de ~10 LU."""
    medidor = _medir([(20.0, -20.0), (20.0, -30.0)])

    assert medidor.loudness_range_lu == pytest.approx(10.0, abs=1.0)


def test_el_gate_absoluto_esta_en_menos_70():
    """Constante normativa (EBU Tech 3341 §2.3): -70 LUFS."""
    assert GATE_ABSOLUTE_LUFS == -70.0


# ---------------------------------------------------------------------------
# 7. Coste: el medidor no puede comerse el presupuesto de un bloque
# ---------------------------------------------------------------------------


def test_coste_por_bloque_muy_por_debajo_del_presupuesto():
    """Un bloque de 1024 frames dura 21,3 ms: medir debe costar < 15 % de eso.

    Medido en desarrollo: 0,126 ms/bloque (0,6 %). El umbral es holgado a
    propósito (los runners de CI son 5-10x más lentos y con carga variable),
    pero sigue cazando una regresión algorítmica del tipo O(n) por muestra.
    """
    import time

    _scipy_signal()
    medidor = LoudnessMeter(FS, 2)
    rng = np.random.default_rng(7)
    bloques = [(rng.standard_normal((1024, 2)) * 0.1).astype(np.float32) for _ in range(200)]

    t0 = time.perf_counter()
    for bloque in bloques:
        medidor.process(bloque)
    por_bloque_ms = (time.perf_counter() - t0) / len(bloques) * 1000.0

    assert por_bloque_ms < 3.0, f"{por_bloque_ms:.3f} ms/bloque: el medidor se come el presupuesto"


# ---------------------------------------------------------------------------
# 8. Memoria acotada: las rutas de "desplazar la mitad" nunca se ejercitaban
# ---------------------------------------------------------------------------


def test_historial_acotado_desplaza_la_mitad_sin_romper_la_medida():
    """Con `max_blocks` pequeño, el historial se recorta y sigue midiendo.

    Estas ramas (crecer, desplazar por mitades, y lo mismo en la serie de corto
    plazo) solo se alcanzaban tras HORAS de audio, así que no las cubría nada:
    un off-by-one ahí corrompe el integrado de una sesión larga en silencio.
    """
    _scipy_signal()
    medidor = LoudnessMeter(FS, 2, max_blocks=8)
    bloque = _seno(0.1, -23.0)

    for _ in range(60):  # 6 s: 60 tramos, muchas vueltas del historial
        medidor.process(bloque)

    snap = medidor.snapshot()

    assert 0 < len(medidor) <= 8
    assert snap["integrated_lufs"] == pytest.approx(-23.0, abs=0.2)
    assert snap["momentary_lufs"] == pytest.approx(-23.0, abs=0.1)
    assert math.isfinite(snap["lra_lu"])


def test_parametros_degenerados_no_revientan():
    """`hop_s=0`, tasa inválida o `max_blocks` diminuto se sanean, no lanzan."""
    _scipy_signal()
    for kwargs in ({"hop_s": 0.0}, {"hop_s": -1.0}, {"max_blocks": 1}, {"sample_rate": 0}, {"channels": 0}):
        medidor = LoudnessMeter(**kwargs)
        medidor.process(_seno(0.5, -23.0))
        snap = medidor.snapshot()
        assert all(not math.isnan(v) for v in snap.values()), kwargs


def test_hop_distinto_de_100_ms_mantiene_la_ventana():
    """Con hop de 50 ms, M sigue siendo 400 ms (8 tramos) y S 3 s (60 tramos)."""
    _scipy_signal()
    medidor = LoudnessMeter(FS, 2, hop_s=0.05)
    bloque = _seno(0.05, -23.0)

    for _ in range(7):  # 350 ms < 400 ms
        medidor.process(bloque)
    assert medidor.momentary_lufs == -math.inf
    medidor.process(bloque)  # 400 ms
    assert medidor.momentary_lufs == pytest.approx(-23.0, abs=0.1)
