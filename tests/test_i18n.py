# -*- coding: utf-8 -*-
"""Tests de localizacion (audio_enhancer.i18n): traducciones es->en por defecto,
deteccion de idioma y la integridad de presets y textos de ayuda."""

import pytest

from audio_enhancer.dsp import Enhancer
from audio_enhancer.i18n import (
    EXPLAIN,
    EXPLAIN_EN,
    EQ_EXPLAIN,
    PRESETS,
    TRANSLATIONS,
    detect_system_language,
    translate,
)
from audio_enhancer.constants import DEFAULT_PRESET


def test_translate_es_devuelve_el_mismo_texto():
    assert translate("Volumen", "es") == "Volumen"


def test_translate_en_usa_diccionario():
    assert translate("Volumen", "en") == "Volume"
    assert translate("Guardar", "en") == "Save"


def test_translate_desconocido_passthrough():
    assert translate("Frase que no existe", "en") == "Frase que no existe"


def test_translate_con_formato_conserva_placeholder():
    assert "%s" in translate("Limitador suave: %s", "en")
    assert "%s" in translate("Compresor RMS: %s", "en")
    assert "%s" in translate("Activo (ring buffer): %s → %s", "en")


def test_detect_language_retorna_es_o_en():
    lang = detect_system_language()
    assert lang in ("es", "en")


# ---------- presets ----------

def test_presets_incluyen_el_predeterminado():
    assert DEFAULT_PRESET in PRESETS


def test_presets_tienen_formato_valido():
    eq_bands = len(Enhancer().eq_bands)
    for nombre, cfg in PRESETS.items():
        assert isinstance(nombre, str) and nombre
        vol, bass, treble, eq = cfg
        assert isinstance(vol, (int, float))
        assert isinstance(bass, (int, float))
        assert isinstance(treble, (int, float))
        assert isinstance(eq, (list, tuple)) and len(eq) == eq_bands
        assert all(isinstance(g, (int, float)) for g in eq)


def test_preset_plano_esta_plano():
    vol, bass, treble, eq = PRESETS[DEFAULT_PRESET]
    assert vol == 1.0 and bass == 0.0 and treble == 0.0
    assert all(g == 0 for g in eq)


# ---------- textos de ayuda ----------

@pytest.mark.parametrize("clave", ["volumen", "bass", "treble", "eq", "limiter", "compressor"])
def test_explain_cubre_todos_los_controles(clave):
    assert clave in EXPLAIN and clave in EXPLAIN_EN


def test_eq_explain_cubre_las_bandas():
    for freq in Enhancer().eq_bands:
        assert freq in EQ_EXPLAIN


def test_traducciones_tienen_valores_no_vacios():
    for clave, valor in TRANSLATIONS.items():
        assert isinstance(clave, str) and clave
        assert isinstance(valor, str) and valor