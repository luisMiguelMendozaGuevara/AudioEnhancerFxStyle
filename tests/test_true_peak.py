"""Medidor de true-peak: verdad teórica y equivalencia con la norma.

Los números esperados NO se copian de este módulo: salen de la definición (una
señal senoidal de amplitud 1 tiene pico real 0 dBTP, se muestree como se
muestree) y de la aritmética del muestreo (un seno a fs/4 con fase de 45° tiene
pico de muestra −3,01 dBFS, es decir, 3,01 dB por debajo de su pico real).
"""

import time

import numpy as np
import pytest

from audio_enhancer.true_peak import (
    FACTOR_SOBREMUESTREO,
    SIN_MEDIDA,
    TruePeakMeter,
    disenar_filtro,
    medir_true_peak,
)

FS = 48000


def _seno(freq: float, fase_grados: float = 0.0, segundos: float = 1.0, amp: float = 1.0) -> np.ndarray:
    t = np.arange(int(FS * segundos)) / FS
    return amp * np.sin(2 * np.pi * freq * t + np.deg2rad(fase_grados))


def _medir_bloques(señal: np.ndarray, bloque: int = 1024, canales: int = 2) -> dict:
    """Pasa la señal por el medidor como lo haría el motor, bloque a bloque."""
    medidor = TruePeakMeter(sample_rate=FS, canales=canales)
    datos = señal if señal.ndim == 2 else np.repeat(señal[:, None], canales, axis=1)
    for inicio in range(0, len(datos), bloque):
        medidor.process(datos[inicio : inicio + bloque])
    return medidor.snapshot()


class TestFiltro:
    def test_ganancia_en_continua_igual_al_factor(self):
        """Con ceros insertados, la ganancia tiene que ser `factor` o la señal
        saldría atenuada por el propio interpolador."""
        for factor in (4, 8):
            h = disenar_filtro(factor, 24)
            assert abs(np.sum(h) - factor) < 1e-6, f"factor {factor}"

    def test_una_continua_sale_plana(self):
        """Una señal constante no debe rizar al interpolar (ganancia unitaria)."""
        medidor = TruePeakMeter(sample_rate=FS)
        medidor.process(np.full((2048, 2), 0.5))
        medidor.process(np.full((2048, 2), 0.5))
        medidas = medidor.snapshot()

        assert abs(medidas["true_peak_dbtp"] - 20 * np.log10(0.5)) < 0.001
        assert abs(medidas["difference_db"]) < 0.001

    def test_el_filtro_rechaza_las_imagenes_del_sobremuestreo(self):
        """Sin rechazo, el medidor vería picos que no existen en el audio.

        Se mide la respuesta del filtro por encima del Nyquist original (donde
        caen las imágenes) y se exige atenuación de al menos 60 dB.
        """
        from scipy.signal import freqz

        for factor in (4, 8):
            h = disenar_filtro(factor, 24)
            w, respuesta = freqz(h, worN=4096, fs=factor * FS)
            # Desde 0,6·fs: por debajo empieza la banda de transición, que ningún
            # filtro finito puede cortar en seco (ahí sí hay fuga, medida −30 dB).
            imagen = np.abs(respuesta[(w >= FS * 0.60) & (w <= FS * 0.95)])
            atenuacion = 20 * np.log10(np.max(imagen) / factor) if imagen.size else -999
            assert atenuacion < -60, f"factor {factor}: atenuación {atenuacion:.1f} dB"

    def test_parametros_invalidos(self):
        with pytest.raises(ValueError):
            disenar_filtro(factor=1)
        with pytest.raises(ValueError):
            disenar_filtro(taps_por_fase=2)
        with pytest.raises(ValueError):
            TruePeakMeter(sample_rate=0)
        with pytest.raises(ValueError):
            TruePeakMeter(canales=0)


class TestBancoDeReferencia:
    """Casos con verdad analítica, no con la salida de nuestra implementación."""

    def test_fs4_con_fase_de_45_grados(self):
        """El caso clásico: −3,01 dBFS de muestra, 0 dBTP real, 3,01 dB de diferencia."""
        medidas = _medir_bloques(_seno(FS / 4, 45))

        assert abs(medidas["sample_peak_dbfs"] - (-3.0103)) < 0.01
        assert abs(medidas["true_peak_dbtp"]) < 0.05, "un seno de amplitud 1 tiene 0 dBTP"
        assert abs(medidas["difference_db"] - 3.0106) < 0.02

    @pytest.mark.parametrize(
        ("freq", "fase"),
        [(6000.0, 0.0), (997.0, 0.0), (10000.0, 30.0), (15000.0, 20.0), (60.0, 0.0)],
    )
    def test_senos_de_amplitud_uno_dan_cero_dbtp(self, freq, fase):
        """Cualquier seno de amplitud 1 tiene pico real 0 dBTP, claro."""
        medidas = _medir_bloques(_seno(freq, fase))

        assert abs(medidas["true_peak_dbtp"]) < 0.05, f"{freq} Hz"
        assert medidas["true_peak_dbtp"] >= medidas["sample_peak_dbfs"] - 0.01

    def test_cerca_de_nyquist_el_error_sigue_dentro_de_la_tolerancia(self):
        """A 20 kHz el filtro empieza a recortar: se exige ±0,5 dB (EBU 3341)."""
        medidas = _medir_bloques(_seno(20000.0, 45))

        assert abs(medidas["true_peak_dbtp"]) < 0.5

    def test_el_ruido_no_inventa_picos_grandes(self):
        """Con ruido la diferencia real es pequeña; el medidor no debe exagerarla."""
        rng = np.random.default_rng(7)
        ruido = np.clip(rng.normal(0, 0.25, (FS, 2)), -1, 1)
        medidas = _medir_bloques(ruido)

        assert 0.0 <= medidas["difference_db"] < 1.5, medidas

    def test_un_seno_bajito_da_el_nivel_correcto(self):
        """A -40 dBFS (nivel real de platos) el medidor no pierde escala."""
        medidas = _medir_bloques(_seno(1000.0, 0.0, amp=0.01))

        assert abs(medidas["true_peak_dbtp"] - (-40.0)) < 0.05


class TestEquivalenciaEnBloques:
    """El medidor por bloques tiene que dar lo mismo que medir el archivo entero."""

    @pytest.mark.parametrize("bloque", [480, 1024, 2880])
    @pytest.mark.parametrize("freq,fase", [(12000.0, 45.0), (997.0, 0.0), (15000.0, 20.0)])
    def test_el_tamano_de_bloque_no_cambia_la_medida(self, bloque, freq, fase):
        señal = _seno(freq, fase)
        _, referencia = medir_true_peak(señal)
        medidas = _medir_bloques(señal, bloque=bloque)

        assert abs(medidas["true_peak_dbtp"] - 20 * np.log10(referencia)) < 0.01

    def test_el_medidor_con_estado_empareja_con_la_medida_sin_estado(self):
        rng = np.random.default_rng(3)
        señal = np.clip(rng.normal(0, 0.2, (FS, 2)), -1, 1)
        _, referencia = medir_true_peak(señal)
        medidas = _medir_bloques(señal)

        assert abs(medidas["true_peak_dbtp"] - 20 * np.log10(referencia)) < 0.01


class TestRobustez:
    def test_mono_y_estereo(self):
        señal = _seno(1000.0)
        mono = _medir_bloques(señal, canales=1)
        estereo = _medir_bloques(np.repeat(señal[:, None], 2, axis=1), canales=2)

        assert abs(mono["true_peak_dbtp"] - estereo["true_peak_dbtp"]) < 0.001

    def test_nan_e_infinitos_no_envenenan_la_retencion(self):
        """Un NaN en una muestra no puede dejar el medidor inservible para siempre."""
        medidor = TruePeakMeter(sample_rate=FS)
        medidor.process(np.array([[0.5, 0.5], [np.nan, np.inf], [0.5, 0.5]]))
        medidor.process(np.array([[0.25, 0.25]]))

        medidas = medidor.snapshot()
        assert np.isfinite(medidas["true_peak_dbtp"])
        assert medidas["true_peak_dbtp"] < 0.0, "el NaN no debe aparecer como pico infinito"

    def test_bloques_vacios_se_ignoran(self):
        medidor = TruePeakMeter(sample_rate=FS)
        medidor.process(np.zeros((0, 2)))

        assert medidor.snapshot()["true_peak_dbtp"] == SIN_MEDIDA

    def test_numero_de_canales_incorrecto(self):
        medidor = TruePeakMeter(sample_rate=FS, canales=2)
        with pytest.raises(ValueError):
            medidor.process(np.zeros((64, 1)))

    def test_reset_vuelve_al_centinela(self):
        medidor = TruePeakMeter(sample_rate=FS)
        # Dos bloques: el primero solo carga el filtro (ver process()).
        medidor.process(np.full((512, 2), 0.9))
        medidor.process(np.full((512, 2), 0.9))
        assert medidor.snapshot()["true_peak_dbtp"] > -1.0

        medidor.reset()

        assert medidor.snapshot()["true_peak_dbtp"] == SIN_MEDIDA


class TestRetencion:
    def test_el_hold_recuerda_el_maximo_y_el_maximo_se_puede_reiniciar(self):
        medidor = TruePeakMeter(sample_rate=FS, retencion_s=0.1)
        medidor.process(np.full((256, 2), 0.9))  # carga el filtro
        medidor.process(np.full((256, 2), 0.9))
        pico_alto = medidor.snapshot()["hold_dbtp"]
        medidor.process(np.full((256, 2), 0.01))

        # La retención no baja aunque la señal caiga. Puede subir: el escalón de
        # 0,9 a 0,01 tiene energía por encima de Nyquist y su reconstrucción
        # sobreoscila (medido +0,17 dBTP), que es un pico real de verdad.
        assert medidor.snapshot()["hold_dbtp"] >= pico_alto - 0.01

        medidor.reiniciar_maximo()
        assert medidor.snapshot()["true_peak_dbtp"] == SIN_MEDIDA

    def test_el_hold_cae_cuando_expira_la_retencion(self):
        """Sin esto, la aguja se quedaría clavada en un pico de hace una hora."""
        medidor = TruePeakMeter(sample_rate=FS, retencion_s=0.05)
        medidor.process(np.full((2400, 2), 0.9))  # carga el filtro
        medidor.process(np.full((2400, 2), 0.9))  # 50 ms a 0.9
        medidor.process(np.full((2400, 2), 0.9))
        alto = medidor.snapshot()["hold_dbtp"]

        for _ in range(8):  # muy por encima de la retención, a nivel bajo
            medidor.process(np.full((2400, 2), 0.001))

        assert medidor.snapshot()["hold_dbtp"] < alto - 20


class TestCoste:
    def test_el_medidor_cabe_en_el_presupuesto(self):
        """Un medidor que no cabe obliga a apagarlo: aquí se mide, no se supone."""
        medidor = TruePeakMeter(sample_rate=FS)
        bloque = np.random.default_rng(0).normal(0, 0.1, (1024, 2)).astype(np.float32)
        medidor.process(bloque)  # primar
        inicio = time.perf_counter()
        for _ in range(200):
            medidor.process(bloque)
        ms = (time.perf_counter() - inicio) / 200 * 1000

        porcentaje = ms / 21.33 * 100
        print(f"\n  coste del medidor: {ms:.3f} ms/bloque ({porcentaje:.2f} % del bloque)")
        assert porcentaje < 4.0, f"demasiado caro: {porcentaje:.2f} %"


def test_factor_por_defecto_es_el_minimo_de_la_norma():
    """BS.1770-4 anexo 2 exige al menos 4×; usar menos invalidaría la medida."""
    assert FACTOR_SOBREMUESTREO >= 4


class TestCadenciaDeMedicion:
    """¿Compensa medir varios bloques de golpe para ahorrar CPU?

    Se comprobó porque la sospecha razonable era que sí. La medida dice que no:
    el coste es por muestra procesada, no por llamada, así que juntar bloques
    ahorra ~11 % y complica el código. El test fija las dos verdades: la medida
    es idéntica y el ahorro es pequeño.
    """

    def _medir(self, señal: np.ndarray, cada: int) -> dict:
        medidor = TruePeakMeter(sample_rate=FS)
        pendientes = []
        for inicio in range(0, len(señal), 1024):
            pendientes.append(señal[inicio : inicio + 1024])
            if len(pendientes) == cada:
                medidor.process(np.concatenate(pendientes))
                pendientes = []
        if pendientes:
            medidor.process(np.concatenate(pendientes))
        return medidor.snapshot()

    def test_medir_de_dos_en_dos_da_el_mismo_numero(self):
        señal = np.repeat(_seno(12000.0, 45.0, segundos=2.0)[:, None], 2, axis=1)

        uno = self._medir(señal, 1)
        dos = self._medir(señal, 2)
        cuatro = self._medir(señal, 4)

        assert abs(dos["true_peak_dbtp"] - uno["true_peak_dbtp"]) < 0.001
        assert abs(cuatro["true_peak_dbtp"] - uno["true_peak_dbtp"]) < 0.001

    def test_el_ahorro_de_juntar_bloques_es_marginal(self):
        """Si algún día el ahorro fuera grande, este test avisaría para revisarlo."""
        señal = np.repeat(_seno(12000.0, 45.0, segundos=2.0)[:, None], 2, axis=1)

        def coste(cada: int) -> float:
            inicio = time.perf_counter()
            self._medir(señal, cada)
            return time.perf_counter() - inicio

        coste(1)  # calentar
        uno = min(coste(1) for _ in range(3))
        dos = min(coste(2) for _ in range(3))
        ahorro = (uno - dos) / uno * 100

        print(f"\n  ahorro al medir de dos en dos: {ahorro:.1f} %")
        assert ahorro < 40, f"el ahorro ({ahorro:.0f} %) justificaría cambiar la cadencia"
