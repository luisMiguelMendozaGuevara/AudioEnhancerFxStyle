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


def test_guardado_atomico_no_deja_tmp_ni_corrupta(tmp_path):
    """R3-B3: el guardado escribe a .tmp + fsync + os.replace.

    Tras un guardado correcto no queda residuo .tmp; el contenido del
    archivo siempre es un JSON completo (nunca una escritura a medias)."""
    p = tmp_path / "config.json"
    assert save_config({"volume": 0.5}, str(p)) is True
    assert not list(tmp_path.glob("*.tmp"))  # el .tmp fue renombrado, no copiado
    assert json.loads(p.read_text(encoding="utf-8")) == {"volume": 0.5}

    # Segundo guardado sobre el mismo archivo: replace atómico, sin mezcla.
    assert save_config({"volume": 0.9, "eq": [0] * 9}, str(p)) is True
    assert load_config(str(p)) == {"volume": 0.9, "eq": [0] * 9}
    assert not list(tmp_path.glob("*.tmp"))
