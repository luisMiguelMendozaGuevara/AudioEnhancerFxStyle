"""Importador de AutoEQ: parseo, interpolación y límites.

Los formatos que se prueban son los documentados por AutoEQ (no inventados aquí):
`ParametricEQ.txt` con líneas `Filter N: ON PK Fc … Hz Gain … dB Q …` y
`GraphicEQ.txt` con `f1 g1; f2 g2; …`.
"""

import pytest

from audio_enhancer.autoeq import (
    GANANCIA_MAX_DB,
    Perfil,
    curva_en_bandas,
    detectar_formato,
    leer_archivo,
    parsear_grafico,
    parsear_parametrico,
    preparar_importacion,
)

PARAMETRICO = """# AutoEQ profile
Filter 1: ON PK Fc 105 Hz Gain 5.5 dB Q 0.70
Filter 2: ON PK Fc 2500 Hz Gain -3.0 dB Q 1.41
Filter 3: OFF PK Fc 6000 Hz Gain 4.0 dB Q 2.00
Filter 4: ON LS Fc 60 Hz Gain 2.0 dB Q 0.70
Filter 5: ON HP Fc 30 Hz Gain 0.0 dB Q 0.70
"""

GRAFICO = """Preamp: -6.0 dB
100 4.5; 1000 -2.0; 3000 -3.5; 8000 2.0; 12000 3.0
"""


class TestParseo:
    def test_parametrizado_solo_cuenta_los_filtros_activos(self):
        perfil = parsear_parametrico(PARAMETRICO)

        # El OFF y el HP se descartan: uno está apagado, el otro no ecualiza.
        assert perfil.formato == "parametrico"
        assert len(perfil.puntos) == 3
        assert (105.0, 5.5) in perfil.puntos
        assert (2500.0, -3.0) in perfil.puntos

    def test_grafico_lee_pares_frecuencia_ganancia(self):
        perfil = parsear_grafico(GRAFICO)

        assert perfil.formato == "grafico"
        assert perfil.puntos == [(100.0, 4.5), (1000.0, -2.0), (3000.0, -3.5), (8000.0, 2.0), (12000.0, 3.0)]

    def test_se_detecta_el_formato_sin_decirselo(self):
        assert detectar_formato(PARAMETRICO) == "parametrico"
        assert detectar_formato(GRAFICO) == "grafico"
        assert detectar_formato("hola, esto no es un perfil") == ""

    def test_frecuencias_absurdas_se_descartan(self):
        perfil = parsear_parametrico("Filter 1: ON PK Fc 500000 Hz Gain 3.0 dB Q 1.0\n")
        assert perfil.puntos == []

    def test_archivo_con_formato_desconocido_lanza_con_mensaje(self, tmp_path):
        ruta = tmp_path / "loquesea.txt"
        ruta.write_text("esto no es un perfil de AutoEQ", encoding="utf-8")

        with pytest.raises(ValueError, match="no parece un perfil"):
            leer_archivo(ruta)

    def test_archivo_sin_filtros_activos_lanza(self, tmp_path):
        ruta = tmp_path / "vacio.txt"
        ruta.write_text("Filter 1: OFF PK Fc 100 Hz Gain 3.0 dB Q 1.0\n", encoding="utf-8")

        with pytest.raises(ValueError):
            leer_archivo(ruta)

    def test_archivo_que_no_existe_lanza_con_mensaje(self):
        with pytest.raises(ValueError, match="No se pudo leer"):
            leer_archivo("no_existe_este_archivo.txt")

    def test_leer_archivo_anota_la_procedencia(self, tmp_path):
        ruta = tmp_path / "Sennheiser HD600.txt"
        ruta.write_text(GRAFICO, encoding="utf-8")

        perfil = leer_archivo(ruta)

        assert perfil.nombre == "Sennheiser HD600"
        assert perfil.fuente == str(ruta)


class TestInterpolacion:
    def test_en_un_punto_del_archivo_devuelve_su_ganancia(self):
        perfil = parsear_grafico(GRAFICO)

        assert perfil.ganancia_en(1000.0) == pytest.approx(-2.0)
        assert perfil.ganancia_en(8000.0) == pytest.approx(2.0)

    def test_entre_dos_puntos_interpola_en_escala_logaritmica(self):
        perfil = Perfil(nombre="x", puntos=[(100.0, 0.0), (10000.0, 10.0)])

        # 1000 Hz es el punto medio logarítmico: la mitad de la ganancia.
        assert perfil.ganancia_en(1000.0) == pytest.approx(5.0, abs=0.01)

    def test_fuera_del_rango_no_extrapola(self):
        """Extrapolar hacia arriba inventaría un realce que el perfil no pide."""
        perfil = Perfil(nombre="x", puntos=[(100.0, 2.0), (4000.0, -2.0)])

        assert perfil.ganancia_en(20.0) == pytest.approx(2.0)
        assert perfil.ganancia_en(20000.0) == pytest.approx(-2.0)

    def test_el_recorte_a_doce_db_se_aplica(self):
        perfil = Perfil(nombre="x", puntos=[(1000.0, 30.0)])

        assert perfil.ganancia_en(1000.0) == pytest.approx(GANANCIA_MAX_DB)

    def test_curva_en_bandas_respeta_el_orden_de_las_bandas(self):
        perfil = parsear_grafico(GRAFICO)
        bandas = [60.0, 150.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 12000.0]

        ganancias = curva_en_bandas(perfil, bandas)

        assert len(ganancias) == len(bandas)
        assert ganancias[4] == pytest.approx(-2.0)  # 1000 Hz está en el archivo
        assert ganancias[8] == pytest.approx(3.0)  # 12000 Hz, también

    def test_sin_bandas_lanza(self):
        with pytest.raises(ValueError):
            curva_en_bandas(parsear_grafico(GRAFICO), [])


class TestAvisos:
    def test_avisa_de_la_perdida_al_pasar_de_muchas_bandas_a_pocas(self):
        perfil = parsear_grafico(GRAFICO)  # 5 puntos
        resultado = preparar_importacion(perfil, [60.0, 250.0, 1000.0])  # 3 bandas

        assert any("aproximación" in aviso for aviso in resultado.avisos)

    def test_avisa_si_el_perfil_no_cubre_toda_la_banda(self):
        perfil = Perfil(nombre="x", puntos=[(1000.0, 1.0), (2000.0, 2.0)], formato="grafico")

        resultado = preparar_importacion(perfil, [60.0, 1000.0, 12000.0])

        assert any("empieza en" in aviso for aviso in resultado.avisos)
        assert any("acaba en" in aviso for aviso in resultado.avisos)

    def test_avisa_del_recorte_de_ganancia(self):
        perfil = Perfil(nombre="x", puntos=[(1000.0, 40.0)], formato="grafico")

        resultado = preparar_importacion(perfil, [1000.0])

        assert any("recortó" in aviso for aviso in resultado.avisos)
        assert resultado.ganancia_maxima == pytest.approx(GANANCIA_MAX_DB)

    def test_el_resumen_dice_lo_que_se_va_a_aplicar(self):
        perfil = parsear_grafico(GRAFICO)
        resultado = preparar_importacion(perfil, [60.0, 1000.0, 12000.0])

        resumen = resultado.resumen()
        assert "5 puntos" in resumen
        assert "Ganancia máxima" in resumen
