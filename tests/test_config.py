# -*- coding: utf-8 -*-
"""Tests de persistencia (audio_enhancer.config): round-trip, creacion de
directorios y tolerancia a archivos corruptos."""

import json

from audio_enhancer.config import load_config, save_config


def test_round_trip(tmp_path):
    cfg = {"volume": 0.8, "bass": 4.0, "eq": [0] * 9, "language": "es"}
    p = tmp_path / "sub" / "config.json"  # el directorio se crea solo
    assert save_config(cfg, str(p)) is True
    assert load_config(str(p)) == cfg


def test_ausente_devuelve_vacio(tmp_path):
    assert load_config(str(tmp_path / "no_existe.json")) == {}


def test_json_corrupto_devuelve_vacio(tmp_path):
    p = tmp_path / "roto.json"
    p.write_text("{esto no es json", encoding="utf-8")
    assert load_config(str(p)) == {}


def test_lista_no_diccionario_devuelve_vacio(tmp_path):
    p = tmp_path / "lista.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_config(str(p)) == {}


def test_preserva_caracteres_unicode(tmp_path):
    p = tmp_path / "cfg.json"
    save_config({"preset": "Noche (vol. baja)"}, str(p))
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["preset"] == "Noche (vol. baja)"