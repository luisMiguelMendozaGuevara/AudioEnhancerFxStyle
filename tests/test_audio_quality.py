"""Red de seguridad de CALIDAD objetiva del audio (DSP puro, sin UI ni hardware).

La suite del proyecto cubre el COMPORTAMIENTO del DSP (bypass, rampas, presets,
limites de parametros), pero no la CALIDAD de la senal: un refactor podia
degradar el sonido sin que ningun test fallara. Estos tests miden magnitudes
fisicas sobre el propio ``Enhancer`` en memoria:

* distorsion armonica + ruido (THD+N) del camino neutro,
* respuesta en frecuencia del EQ (peaking y shelves) frente a la teoria,
* techo real del limitador con picos INTER-MUESTRA (true-peak x4),
* robustez (nunca NaN/Inf ni |y| > 1) con parametros extremos,
* causalidad y retardo de grupo de la cadena, en muestras,
* equivalencia bloque a bloque (detector de fugas de estado),
* true-peak de un arranque con fade-in.

Todo es determinista (semilla fija), no toca dispositivos de audio ni Qt y no
depende de la plataforma: corre igual en el CI de Linux. Las tolerancias estan
justificadas junto a cada aserto; no se relajan sin una medida que lo explique.

Nota sobre la terminologia: los nombres de atributo usados aqui son los REALES
de ``audio_enhancer.dsp.Enhancer`` (``limiter_threshold`` es el techo del
limitador, ``effects`` es el dict de la capa enhancer con claves
``default_effects()``), no alias inventados.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import signal

from audio_enhancer.constants import CHUNK, SAMPLE_RATE
from audio_enhancer.dsp import Enhancer, default_effects

FS = SAMPLE_RATE
SEED = 1234  # semilla fija: resultados reproducibles bit a bit
AMP_NEUTRA = 0.5  # -6 dBFS, el nivel del test de THD+N
IMPUESTO_EN = 1024  # posicion del impulso dentro del bloque (causalidad)


# ---------------------------------------------------------------------------
# Construccion del Enhancer
# ---------------------------------------------------------------------------


def _aplicar(enh: Enhancer, **params) -> Enhancer:
    """Escribe parametros y reinicia las rampas: los valores entran YA.

    Sin ``reset_state()`` el EQ, el volumen y el master llegan a su objetivo con
    una rampa de ~30-100 ms, y una medicion corta saldria baja por construccion
    (no por un defecto del DSP)."""
    for key, value in params.items():
        setattr(enh, key, value)
    enh.reset_state()
    return enh


def _make_neutral(**overrides) -> Enhancer:
    """Enhancer en modo NEUTRO: sin EQ, sin dinamica, sin efectos y a unidad.

    Deja la cadena en el camino mas transparente posible. El unico proceso que
    queda es el DC blocker de 10 Hz, que en produccion va SIEMPRE activo (es
    parte del camino real, no un adorno del test): asi la red de seguridad mide
    la cadena de verdad y no un bypass artificial.

    Cualquier etapa se activa por test con ``overrides`` (una sola etapa por
    test, para que lo medido se pueda atribuir)."""
    enh = Enhancer()
    enh.sample_rate = FS
    enh.eq_gains = [0.0] * len(enh.eq_bands)
    enh.bass = 0.0
    enh.treble = 0.0
    enh.volume = 1.0
    enh.master_gain_db = 0.0
    enh.balance_db = 0.0
    enh.leveling = 0.0
    enh.effects = default_effects()
    enh.blend = 1.0
    enh.compressor = False
    enh.limiter = False
    enh.crossfeed = False
    enh.spectrum_enabled = False
    return _aplicar(enh, **overrides)


# ---------------------------------------------------------------------------
# Senales y medidas
# ---------------------------------------------------------------------------


def _tono(freq: float, amp: float, n: int, phase: float = 0.0) -> np.ndarray:
    """Tono puro estereo (L = R) de ``n`` muestras a ``FS``, float32 (N, 2)."""
    t = np.arange(n, dtype=np.float64) / FS
    s = (amp * np.sin(2.0 * np.pi * freq * t + phase)).astype(np.float32)
    return np.stack([s, s], axis=1)


def _ruido_rosa(n: int, rng: np.random.Generator, amp: float = 0.2) -> np.ndarray:
    """Ruido rosa reproducible: blanco coloreado en la FFT (1/sqrt(f)).

    Se normaliza el pico a ``amp`` para que la senal sea realista (no satura el
    limitador por si misma) y sea identica en cada ejecucion."""
    blanco = rng.standard_normal((n, 2))
    freqs = np.fft.rfftfreq(n, 1.0 / FS)
    freqs[0] = freqs[1] if freqs.size > 1 else 1.0
    spec = np.fft.rfft(blanco, axis=0) / np.sqrt(freqs)[:, None]
    rosa = np.fft.irfft(spec, n=n, axis=0)
    pico = float(np.abs(rosa).max())
    return np.asarray(rosa * (amp / max(pico, 1e-12)), dtype=np.float32)


def _procesar(enh: Enhancer, x: np.ndarray, bloque: int = CHUNK) -> np.ndarray:
    """Procesa ``x`` por bloques de ``bloque`` y devuelve la salida concatenada.

    Se trocea a proposito: el camino real entrega bloques de ``CHUNK`` y el
    estado entre bloques es justo lo que puede romper un refactor."""
    trozos = [enh.process(x[i : i + bloque]) for i in range(0, x.shape[0], bloque)]
    return np.concatenate(trozos, axis=0) if trozos else np.zeros_like(x)


def _rms(x: np.ndarray) -> float:
    """RMS global de la senal (float64 para la medida)."""
    dato = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(dato * dato)))


def _db(valor: float) -> float:
    """20*log10 con suelo para no devolver -inf en las trazas."""
    return 20.0 * math.log10(max(abs(float(valor)), 1e-30))


def _thdn_db(x: np.ndarray, bin_fundamental: int, saltar_dc: int = 2) -> float:
    """THD+N en dB: energia NO fundamental / energia del fundamental.

    Requisitos de la ventana (responsabilidad de quien llama): debe contener un
    numero ENTERO de ciclos del tono y ser contigua en la salida del DSP. Asi el
    fundamental cae exacto en ``bin_fundamental`` y la ventana rectangular no
    tiene fuga espectral (sin fuga, cualquier energia fuera del bin es
    distorsion o ruido real, no un artefacto de la FFT).

    ``saltar_dc`` descarta los bins 0..(saltar_dc-1): continua y residuo del
    transitorio del DC blocker, que es infra-sonico y no es distorsion audible.
    """
    dato = np.asarray(x, dtype=np.float64)
    if dato.ndim > 1:
        dato = dato[:, 0]
    potencia = np.abs(np.fft.rfft(dato)) ** 2
    fundamental = float(potencia[bin_fundamental])
    total = float(potencia[saltar_dc:].sum())
    resto = max(total - fundamental, 1e-30)
    return 10.0 * math.log10(resto / max(fundamental, 1e-30))


def _true_peak_dbtp(x: np.ndarray, sobremuestreo: int = 4) -> float:
    """True-peak (dBTP) con un sobremuestreo x4 INDEPENDIENTE del DSP.

    Se usa ``scipy.signal.resample_poly`` en float64 sobre la senal completa: el
    pico por muestra es ciego a los picos INTER-MUESTRA (un seno a fs/4
    desfasado 45 grados muestrea a 0.707 de su amplitud real), y el techo del
    limitador true-peak esta definido sobre la senal continua."""
    up = signal.resample_poly(np.asarray(x, dtype=np.float64), sobremuestreo, 1, axis=0)
    pico = float(np.abs(up).max())
    return _db(pico)


def _retardo_de_grupo(ir: np.ndarray, inicio: int, n_fft: int = 1 << 16) -> tuple[float, float]:
    """Retardo de grupo (muestras) de la respuesta al impulso ``ir``.

    ``ir`` se recorta a partir de ``inicio`` (posicion del impulso), de modo que
    lo medido es el retardo DEL SISTEMA y no el de la posicion del impulso. Se
    deriva la fase desenrollada del espectro (``-dphi/domega``, en muestras) y
    se devuelve ``(mediana, media)`` en la banda 200 Hz - 2 kHz, que es donde
    vive la mayor parte del contenido musical y donde un retardo se nota.

    La mediana es la medida robusta (la media se informa solo como contraste:
    cerca de las cancelaciones la derivada numerica se dispara).
    """
    segmento = np.asarray(ir[inicio:], dtype=np.float64)
    # El pipeline es estereo (N, 2): la fase se mide sobre el promedio de los
    # canales. Sin esto, rfft se aplicaria a (N, 2) con n=65536 (100 M muestras
    # complejas, ~1,6 GB) y np.gradient recibiria un eje de mas (TypeError).
    if segmento.ndim == 2:
        segmento = segmento.mean(axis=1)
    spec = np.fft.rfft(segmento, n_fft)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / FS)
    omega = 2.0 * np.pi * freqs / FS
    retardo = -np.gradient(np.unwrap(np.angle(spec)), omega)
    banda = (freqs >= 200.0) & (freqs <= 2000.0)
    return float(np.median(retardo[banda])), float(np.mean(retardo[banda]))


# ---------------------------------------------------------------------------
# 1. THD+N del camino neutro
# ---------------------------------------------------------------------------


def test_thdn_del_camino_neutro_por_debajo_de_menos_80_db():
    """THD+N del camino neutro: debe quedar por debajo de -80 dB.

    Estimulo: seno de 1 kHz a -6 dBFS (0.5), 2 s, entregado en bloques de 1024.
    Se descarta el primer 0.5 s (transitorio del DC blocker: tau = 1/(2*pi*10 Hz)
    ~ 16 ms) y se analiza el 1.5 s restante, que son 72 000 muestras = 1500
    ciclos EXACTOS de 1 kHz (48 muestras por ciclo a 48 kHz). Asi el fundamental
    cae en el bin 1500 y la ventana rectangular no tiene fuga.

    Criterio: THD+N < -80 dB. El suelo real del camino neutro es el redondeo a
    float32 de la salida (~-140 dB) mas el residuo del transitorio inicial, muy
    por debajo de -100 dB: -80 dB es el margen que deja sitio a que un refactor
    meta un lazo no lineal (un soft-clip, un redondeo a 16 bits, un recorte, un
    limitador que entre antes de tiempo) sin que pase desapercibido.
    """
    n_pre = FS // 2  # 0.5 s descartados
    n_med = 3 * FS // 2  # 1.5 s = 1500 ciclos exactos de 1 kHz
    x = _tono(1000.0, AMP_NEUTRA, n_pre + n_med)

    y = _procesar(_make_neutral(), x)
    medido = y[n_pre:]
    bin_fundamental = int(round(1000.0 * n_med / FS))  # 1500

    thdn = _thdn_db(medido, bin_fundamental)

    assert thdn < -80.0, f"THD+N del camino neutro = {thdn:.1f} dB (criterio: < -80 dB)"


# ---------------------------------------------------------------------------
# 2. Respuesta en frecuencia del EQ frente a la teoria
# ---------------------------------------------------------------------------


def test_ganancia_peaking_1khz_correcta():
    """Una banda peaking de +6 dB @ 1 kHz con Q=1 debe dar +6.0 dB en 1 kHz.

    Teoria: en la frecuencia central, el biquad RBJ peaking vale exactamente
    A^2 = 10^(ganancia_db/20) -> +6.0 dB, independientemente del Q. Se mide la
    ganancia como cociente de RMS con EQ y sin EQ (el DC blocker y el resto del
    camino neutro se cancelan al ser identicos en ambas medidas), sobre el
    ultimo segundo de un tono de 2 s (el primero se descarta para que el filtro
    este en regimen permanente).

    Tolerancia +-1 dB: cubre el redondeo de la frecuencia central a la banda
    disponible (1000 Hz esta en el set por defecto, asi que el error esperado es
    de milesimas de dB) y sigue detectando los fallos tipicos: un EQ que no
    aplica la banda (0 dB), que aplica el doble (+12 dB) o que invierte el signo.
    """
    x = _tono(1000.0, 0.25, 2 * FS)
    referencia = _procesar(_make_neutral(), x)

    enh = _make_neutral()
    ganancias = [0.0] * len(enh.eq_bands)
    indice = min(range(len(enh.eq_bands)), key=lambda i: abs(enh.eq_bands[i] - 1000.0))
    ganancias[indice] = 6.0
    _aplicar(enh, eq_gains=ganancias, eq_q=1.0)
    con_eq = _procesar(enh, x)

    ganancia_db = _db(_rms(con_eq[FS:]) / _rms(referencia[FS:]))

    assert abs(ganancia_db - 6.0) <= 0.3, f"ganancia peaking medida = {ganancia_db:.2f} dB (teoria: 6.0 dB)"


@pytest.mark.parametrize("db_shelf", [6.0, -6.0])
def test_shelf_graves_100hz_mueve_los_50hz(db_shelf):
    """El shelf de graves (corte 100 Hz) debe mover la banda de 50 Hz en su signo.

    Un Low Shelf RBJ con S=1 vale ~A^2 (la ganancia pedida) muy por debajo del
    corte y ~A (la mitad en dB) EN el corte: a 50 Hz (una octava por debajo de
    100 Hz) se espera entre +4 y +6 dB para un shelf de +6 dB. Se exige el
    SIGNO con margen (>1.5 dB en el sentido pedido) y que no se sobrepase la
    ganancia nominal (maximo +6.5 dB): eso caza un shelf que no se aplica, que
    se aplica invertido o que se aplica dos veces, y deja holgura al valor
    exacto de S sin acoplar el test a una constante.
    """
    x = _tono(50.0, 0.25, 72000)  # 1.5 s: 0.5 s de asentamiento + 1 s de medida
    referencia = _procesar(_make_neutral(), x)

    enh = _make_neutral(bass=float(db_shelf), bass_freq=100.0)
    con_shelf = _procesar(enh, x)

    ganancia_db = _db(_rms(con_shelf[FS // 2 :]) / _rms(referencia[FS // 2 :]))

    if db_shelf > 0.0:
        assert 4.0 <= ganancia_db <= 6.0, f"shelf +6 dB @100 Hz: 50 Hz sube {ganancia_db:.2f} dB (teoria 4-6)"
    else:
        assert -6.0 <= ganancia_db <= -4.0, f"shelf -6 dB @100 Hz: 50 Hz baja {ganancia_db:.2f} dB (teoria 4-6)"
    assert abs(ganancia_db) <= 6.0, f"shelf {db_shelf:+.0f} dB: 50 Hz se mueve {ganancia_db:.2f} dB (de mas)"


# ---------------------------------------------------------------------------
# 3. El limitador nunca supera su techo (true-peak)
# ---------------------------------------------------------------------------


def test_limitador_respeta_el_techo_con_picos_inter_muestra():
    """Con picos INTER-MUESTRA, la salida no debe superar el techo declarado.

    Estimulo: seno a fs/4 = 12 kHz con amplitud 0.99 y desfase de 45 grados. Sus
    muestras valen +-0.99*sin(45) = +-0.70 (pico POR MUESTRA 0.70, 3.0 dB por
    debajo del techo), pero su pico REAL (continuo) es 0.99 = -0.09 dBTP, muy
    por encima del techo de 0.95 (-0.45 dBTP). Es el caso clasico que engana a
    un limitador que solo mira el pico por muestra.

    Se mide el true-peak de la salida con un sobremuestreo x4 propio (en
    float64) sobre el ultimo 80% de la senal (el primer 20% se descarta: las
    rampas del limitador necesitan asentarse) y se exige:

    * ``tp <= techo + 0.05 dB``: la tolerancia es de 0.05 dB porque el estimador
      del test y el del DSP son dos pasadas de interpolacion distintas (mismo
      diseno Kaiser, distinto recorte de flancos): la discrepancia esperada es
      de milesimas de dB, y 0.05 dB sigue siendo ~1/10 de un paso de fader.
    * ``tp > techo - 6 dB``: la senal NO esta silenciada (el limitador atenua,
      no muta); sin este aserto un limitador que devolviera ceros pasaria.
    """
    techo = 0.95
    n = 96 * CHUNK  # 2.048 s: numero entero de bloques de CHUNK
    x = _tono(FS / 4.0, 0.99, n, phase=math.pi / 4.0)

    # Pre-condicion (independiente del DSP): el estimulo tiene pico por muestra
    # muy por debajo del techo (0.7001 = -3.10 dBFS) y picos INTER-MUESTRA unos
    # 3 dB por encima de su pico por muestra (amplitud real 0.99 = -0.09 dBTP).
    # Ambas cosas se asertan en dB, de modo que el escenario queda demostrado
    # sin depender de la precision del estimador (la diferencia entre el pico
    # por muestra y el real es lo que distingue este caso).
    pico_muestra_db = _db(float(np.abs(x).max()))
    tp_entrada_db = _true_peak_dbtp(x)
    assert pico_muestra_db < _db(techo) - 2.0
    # Diferencia entre pico muestreado y true-peak: es lo que distingue este caso.
    assert tp_entrada_db > pico_muestra_db + 2.0, "el estimulo no tiene picos inter-muestra"

    enh = _make_neutral(limiter=True, limiter_threshold=techo)
    y = _procesar(enh, x)

    tp_salida_db = _true_peak_dbtp(y[n // 5 :])
    techo_db = _db(techo)

    assert tp_salida_db <= techo_db + 0.05, (
        f"true-peak de salida {tp_salida_db:.3f} dBTP supera el techo {techo_db:.3f} dBTP + 0.05 dB"
    )
    assert tp_salida_db > techo_db - 6.0, f"true-peak de salida {tp_salida_db:.3f} dBTP: la senal sale silenciada"


# ---------------------------------------------------------------------------
# 4. Nunca NaN/Inf con parametros extremos
# ---------------------------------------------------------------------------


def test_parametros_extremos_no_producen_nan_ni_inf():
    """Parametros absurdos: la salida debe ser finita, acotada y no muda.

    Configuracion extrema pero representable desde la UI: EQ de 31 bandas a
    +24 dB con Q=10, los 5 efectos (mas leveling) al maximo, volumen 4.0 (que
    ya es mas que el maximo de la UI), master a +20 dB, balance a +20 dB,
    compresor y limitador activos. Se procesan 100 bloques de ruido rosa.

    Asertos por bloque:
    * ``np.isfinite(out).all()``: ningun NaN/Inf (el DSP tiene auto-curacion,
      pero un bloque en silencio por auto-curacion no debe pasar inadvertido).
    * ``abs(out).max() <= 1.0``: el recorte final y el techo de seguridad
      garantizan el rango digital; nada sale de escala.
    * ``abs(out).max() > 0.01``: el bloque NO es un bloque de silencio de la
      auto-curacion (un estado zi envenenado devolveria ceros y el assert de
      finitud solo no lo detectaria).
    """
    rng = np.random.default_rng(SEED)
    enh = _make_neutral(compressor=True, limiter=True)
    enh.set_eq_band_count(31)
    _aplicar(
        enh,
        eq_gains=[24.0] * len(enh.eq_bands),
        eq_q=10.0,
        effects={k: 10.0 for k in default_effects()},
        leveling=10.0,
        volume=4.0,
        master_gain_db=20.0,
        balance_db=20.0,
    )

    rosa = _ruido_rosa(100 * CHUNK, rng)
    maximo = 0.0
    for i in range(100):
        out = enh.process(rosa[i * CHUNK : (i + 1) * CHUNK])
        assert np.isfinite(out).all(), f"bloque {i}: salida no finita (NaN/Inf) con parametros extremos"
        pico = float(np.abs(out).max())
        assert pico <= 1.0, f"bloque {i}: pico {pico:.4f} fuera del rango digital"
        assert pico > 0.01, f"bloque {i}: bloque en silencio (auto-curacion de estado)"
        maximo = max(maximo, pico)

    assert maximo > 0.5, f"la cadena extrema solo llega a {maximo:.3f} de pico"


# ---------------------------------------------------------------------------
# 5. Causalidad y retardo de grupo
# ---------------------------------------------------------------------------


def test_causalidad_y_retardo_de_grupo_en_muestras():
    """La cadena es causal y su retardo de grupo es de pocas muestras.

    Se mide la respuesta al impulso (1.0 en la muestra ``IMPUESTO_EN`` dentro de
    un bloque de 4096) con la configuracion neutra y con el EQ activo (+6 dB
    @1 kHz, Q=1), en dos casos separados para que lo medido sea atribuible.

    * Causalidad: la salida antes del impulso debe ser exactamente cero (los
      biquads y el DC blocker son causales; un ``filtfilt`` o un suavizado
      centrado que se colara en la senal produciria PRE-eco y aqui se veria).
    * Decaimiento: 3000 muestras (~62 ms) despues del impulso la cola debe estar
      por debajo de -60 dB: un filtro inestable o con ringing infinito no pasa.
    * Retardo de grupo (banda 200 Hz - 2 kHz, mediana): MEDIDO 0.09 muestras en
      el camino neutro (el unico filtro es el DC blocker de 10 Hz, cuyo retardo
      de grupo a 200 Hz es ~0.01 muestras) y 0.53 con el EQ peaking +6 dB Q=1
      @1 kHz (la resonancia solo afecta a la fase alrededor del centro). Es una
      cadena de fase minima con retardo practicamente nulo: eso es un merito y
      ahora esta acotado. El limite SUPERIOR no es cosmetico: caza una etapa
      nueva que meta un buffer de bloque entero (1024 muestras = 21 ms).
    """
    n = 4096
    x = np.zeros((n, 2), dtype=np.float32)
    x[IMPUESTO_EN] = 1.0

    # El limitador y el techo de seguridad se apagan: un impulso de amplitud 1.0
    # es senal "hot" y la compresion de ganancia haria no lineal la respuesta
    # (el retardo de grupo solo tiene sentido en un sistema LTI). El recorte
    # final tambien se apaga para no deformar el pico del EQ.
    comun = {"safety_ceiling_enabled": False, "final_clip_enabled": False}

    neutro = _make_neutral(**comun)
    ir_neutro = np.asarray(neutro.process(x.copy()), dtype=np.float64)

    enh_eq = _make_neutral(**comun)
    ganancias = [0.0] * len(enh_eq.eq_bands)
    indice = min(range(len(enh_eq.eq_bands)), key=lambda i: abs(enh_eq.eq_bands[i] - 1000.0))
    ganancias[indice] = 6.0
    _aplicar(enh_eq, eq_gains=ganancias, eq_q=1.0)
    ir_eq = np.asarray(enh_eq.process(x.copy()), dtype=np.float64)

    for nombre, ir in (("neutro", ir_neutro), ("eq", ir_eq)):
        pre = float(np.abs(ir[:IMPUESTO_EN]).max())
        assert pre < 1e-7, f"{nombre}: hay contenido ANTES del impulso ({pre:.3e}): la cadena no es causal"
        cola = float(np.abs(ir[IMPUESTO_EN + 3000 :]).max())
        assert cola < 1e-3, f"{nombre}: la cola no decae ({cola:.3e}): filtro inestable o ringing infinito"

    gd_neutro, media_neutro = _retardo_de_grupo(ir_neutro, IMPUESTO_EN)
    gd_eq, media_eq = _retardo_de_grupo(ir_eq, IMPUESTO_EN)

    assert np.isfinite(gd_neutro) and np.isfinite(gd_eq)
    # Ventanas estrechas alrededor de los valores MEDIDOS (0.09 y 0.53 muestras):
    # antes eran [-1, 8] y [-16, 48], que dejaban pasar un buffer de bloque entero
    # (1024 muestras = 21 ms). La cadena es de fase minima: el retardo es casi nulo.
    assert 0.0 <= gd_neutro <= 0.6, f"retardo de grupo neutro {gd_neutro:.2f} muestras (medido 0.09)"
    assert 0.2 <= gd_eq <= 1.5, f"retardo de grupo con EQ {gd_eq:.2f} muestras (medido 0.53)"

    # Referencias para el informe: mediana (robusta) y media, en muestras y ms.
    assert math.isfinite(media_neutro) and math.isfinite(media_eq)
    for nombre, gd in (("neutro", gd_neutro), ("eq", gd_eq)):
        assert abs(gd) / FS * 1000.0 <= 1.0, f"{nombre}: {gd:.2f} muestras equivale a mas de 1 ms"


# ---------------------------------------------------------------------------
# 6. Consistencia bloque a bloque
# ---------------------------------------------------------------------------


def test_misma_senal_en_un_bloque_o_en_cuatro_da_lo_mismo():
    """4096 muestras de una vez vs 4 bloques de 1024: la salida debe coincidir.

    Es el detector de fugas de estado: si un bloque reiniciara un ``zi``, una
    envolvente o una rampa (o si el comportamiento dependiera del tamano de
    bloque), las dos ejecuciones divergirian. En la configuracion neutra la
    unica etapa con memoria es el DC blocker de 10 Hz, que se calcula con la
    solucion cerrada del filtro (cumsum): el error esperado es de redondeo
    (~1e-13 relativo), por eso 1e-3 relativo es un margen amplio que sigue
    detectando cualquier discontinuidad real (un corte entre bloques se oiria
    como un clic, ~1e-2 o mas).

    El techo de seguridad se apaga: su magnitud depende del pico DEL BLOQUE y
    aqui lo que se compara es la continuidad del procesado, no la dinamica.
    """
    rng = np.random.default_rng(SEED)
    n = 4 * CHUNK
    x = _ruido_rosa(n, rng, amp=0.2)

    de_una = np.asarray(_make_neutral(safety_ceiling_enabled=False).process(x.copy()), dtype=np.float64)
    enh = _make_neutral(safety_ceiling_enabled=False)
    de_cuatro = np.asarray(_procesar(enh, x.copy(), bloque=CHUNK), dtype=np.float64)

    escala = max(float(np.abs(de_una).max()), 1e-9)
    diferencia_rel = float(np.abs(de_una - de_cuatro).max()) / escala

    assert diferencia_rel < 1e-3, f"diferencia relativa {diferencia_rel:.2e} entre 1 bloque y 4 bloques"


# ---------------------------------------------------------------------------
# 7. True-peak de un arranque con fade-in
# ---------------------------------------------------------------------------


def test_true_peak_de_un_arranque_con_fade_in():
    """El arranque de un stream con fade-in tampoco debe rebasar el techo.

    Caso real: al abrir la salida el motor empieza a emitir audio y el limitador
    parte de ``_lim_last_g = 1.0`` sin historial. Se usa el peor material
    (12 kHz a 0.99 con desfase de 45 grados: true-peak por encima del techo y
    pico por muestra enganoso) con un fade-in lineal de 200 ms y se mide el
    true-peak de TODA la salida, incluido el arranque.

    Tolerancia 0.1 dB (el doble que en regimen permanente, test 3): cubre los
    flancos del interpolador del propio medidor al principio del array, donde no
    hay muestras anteriores. Sigue siendo 1/5 de un paso de fader, y ademas se
    exige que la salida llegue cerca del techo (> techo - 3 dB): el arranque no
    puede quedar silenciado ni dispararse.
    """
    techo = 0.95
    n = 48 * CHUNK  # 1.024 s
    x = _tono(FS / 4.0, 0.99, n, phase=math.pi / 4.0)
    fade = np.linspace(0.0, 1.0, int(0.2 * FS), dtype=np.float32)
    x[: fade.size] *= fade[:, None]

    enh = _make_neutral(limiter=True, limiter_threshold=techo)
    y = _procesar(enh, x)

    tp_salida_db = _true_peak_dbtp(y)
    techo_db = _db(techo)

    assert tp_salida_db <= techo_db + 0.1, (
        f"fade-in: true-peak {tp_salida_db:.3f} dBTP supera el techo {techo_db:.3f} dBTP + 0.1 dB"
    )
    assert tp_salida_db > techo_db - 3.0, f"fade-in: true-peak {tp_salida_db:.3f} dBTP, la salida no llega al techo"


def test_sin_escalones_entre_bloques_con_la_cadena_completa_activa():
    """Estado estable con TODAS las etapas con memoria: sin escalones entre bloques.

    Nota de diseño: la invariancia al tamaño de bloque NO se cumple en esta
    cadena (y no es un defecto): el compresor, el limitador con look-ahead y las
    rampas de los efectos actualizan su estado POR BLOQUE, así que 4096 muestras
    en un bloque y 4x1024 no dan lo mismo. Lo que sí tiene que cumplirse —y es
    como se oye una fuga de estado: un clic— es que un tono estacionario no
    produzca saltos de nivel entre bloques consecutivos.

    El test anterior en neutro solo cubría el DC blocker: una fuga en el
    compresor, el limitador o las rampas pasaba desapercibida.
    """
    e = _make_neutral()
    ganancias = [0.0] * len(e.eq_bands)
    for i in (0, len(ganancias) // 2, len(ganancias) - 1):
        ganancias[i] = 4.0
    _aplicar(
        e,
        eq_gains=ganancias,
        eq_q=1.0,
        compressor=True,
        limiter=True,
        volume=2.0,
        bass=3.0,
        treble=2.0,
    )

    tono = _tono(440.0, 0.25, 200 * 1024)
    niveles = []
    for bloque in np.array_split(tono, 200):
        salida = e.process(bloque)
        assert np.isfinite(salida).all()
        rms = float(np.sqrt(np.mean(np.asarray(salida, dtype=np.float64) ** 2)))
        niveles.append(20.0 * np.log10(max(rms, 1e-9)))

    # Se descartan 20 bloques de asentamiento (rampas de activación).
    estables = niveles[20:]
    saltos = [abs(b - a) for a, b in zip(estables, estables[1:], strict=False)]
    assert max(saltos) < 0.5, f"escalón de {max(saltos):.2f} dB entre bloques (clic audible)"
    assert max(estables) - min(estables) < 3.0, "el nivel del tono no se estabiliza (¿oscila la dinámica?)"


def test_el_techo_del_limitador_por_defecto_esta_fijado():
    """El techo es un valor de PRODUCTO, no un detalle interno.

    Ningún test lo fijaba: subirlo de 0,95 a 0,99 (o a 1,0) cambiaba el nivel de
    salida real de la app sin que la suite dijera nada, porque los tests de
    limitador inyectan su propio techo.
    """
    from audio_enhancer.dsp import Enhancer

    e = Enhancer()
    e.sample_rate = FS

    assert e.limiter_threshold == pytest.approx(0.95, abs=0.001)  # limitador (-0.45 dBFS)
    assert e.safety_ceiling == pytest.approx(0.99, abs=0.001)  # techo de seguridad (-0.09 dBFS)
    assert e.safety_ceiling_enabled is True

    # Y con el techo POR DEFECTO la salida nunca lo supera (true-peak x4).
    n = np.arange(FS)
    x = (1.6 * np.sin(2.0 * np.pi * FS / 4 * n / FS + np.pi / 4)).astype(np.float32)
    salida = e.process(np.stack([x, x], axis=1))
    pico = _true_peak_dbtp(salida)

    assert pico <= 20.0 * math.log10(0.95) + 0.2, f"true-peak {pico:.2f} dBTP supera el techo"


class TestTechoEnDbtp:
    """El techo del limitador se expresa en dBTP, no en el 0,95 heredado."""

    def test_el_valor_por_defecto_es_exactamente_el_de_antes(self):
        """Pasar a dBTP no puede cambiar el sonido: 0,95 y -0,4455 dBTP son lo mismo."""
        from audio_enhancer.dsp import LIMITER_CEILING_DBTP, dbtp_a_lineal

        assert dbtp_a_lineal(LIMITER_CEILING_DBTP) == pytest.approx(0.95, abs=1e-12)

    def test_ida_y_vuelta_entre_dbtp_y_lineal(self):
        from audio_enhancer.dsp import dbtp_a_lineal, lineal_a_dbtp

        for techo in (-6.0, -3.0, -1.0, 0.0):
            assert lineal_a_dbtp(dbtp_a_lineal(techo)) == pytest.approx(techo, abs=1e-9)

    def test_un_techo_de_menos_tres_dbtp_se_respeta(self):
        """Con techo -3 dBTP, la salida no puede pasar de -3 dBTP (+-0,1)."""
        from audio_enhancer.dsp import Enhancer
        from audio_enhancer.true_peak import TruePeakMeter

        tasa = 48000
        t = np.arange(tasa) / tasa
        senal = np.repeat((1.15 * np.sin(2 * np.pi * 12000 * t + np.deg2rad(45)))[:, None], 2, axis=1)
        enh = Enhancer()
        enh.limiter = True
        enh.true_peak = True
        enh.limiter_ceiling_dbtp = -3.0
        enh.limiter_threshold = 10.0 ** (-3.0 / 20.0)
        salida = np.concatenate([enh.process(senal[i : i + 1024]) for i in range(0, len(senal), 1024)])

        medidor = TruePeakMeter(sample_rate=tasa)
        for i in range(0, len(salida), 1024):
            medidor.process(salida[i : i + 1024])

        assert medidor.snapshot()["true_peak_dbtp"] <= -3.0 + 0.1
