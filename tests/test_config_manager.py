"""R3-C2: ConfigManager — esquema, coerción y defaults del config.json.

Todo lo que antes vivía disperso en la ventana (_cfg_float/_cfg_bool/
_sanitize_preset_value + validaciones sueltas en _apply_config) ahora es
testeable sin Qt. Ninguna entrada hostil debe tumbar load()."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_enhancer.config_manager import ConfigManager


def test_as_float_coercion_segura():
    assert ConfigManager.as_float("0.7", 1.0) == 0.7
    assert ConfigManager.as_float(None, 1.0) == 1.0
    assert ConfigManager.as_float(["x"], 1.0) == 1.0
    assert ConfigManager.as_float("abc", 0.5) == 0.5


def test_as_bool_estricto():
    assert ConfigManager.as_bool(True, False) is True
    assert ConfigManager.as_bool(False, True) is False
    assert ConfigManager.as_bool(1, False) is False  # JSON: 1 no es bool
    assert ConfigManager.as_bool("true", False) is False
    assert ConfigManager.as_bool(None, True) is True


def test_sanitize_preset_valido_e_invalido():
    mgr = ConfigManager(eq_band_count=9)
    ok = mgr.sanitize_preset([0.8, 2.0, 1.0, [0] * 9])
    assert ok == (0.8, 2.0, 1.0, [0.0] * 9)
    # Longitud de ganancias equivocada: fuera.
    assert mgr.sanitize_preset([0.8, 2.0, 1.0, [0] * 8]) is None
    # Tipos basura: fuera.
    assert mgr.sanitize_preset(" preset ") is None
    assert mgr.sanitize_preset([0.8, 2.0, 1.0, "nueve"]) is None
    assert mgr.sanitize_preset([0.8, 2.0, 1.0, ["a"] * 9]) is None


def test_sanitize_presets_filtra_invalidos():
    """Import/load: un mapa con entradas basura conserva solo las válidas."""
    mgr = ConfigManager(eq_band_count=9)
    out = mgr.sanitize_presets(
        {
            "bueno": [0.9, 2.0, 1.0, [0] * 9],
            "corto": [0.9, 2.0, 1.0, [0] * 8],  # ganancias desalineadas
            "basura": "no soy un preset",
        }
    )
    assert list(out) == ["bueno"]
    assert out["bueno"] == (0.9, 2.0, 1.0, [0.0] * 9)
    # No-dict no lanza y devuelve vacío (JSON importado hostil).
    assert mgr.sanitize_presets(None) == {}
    assert mgr.sanitize_presets([1, 2, 3]) == {}


def test_load_acota_valores_al_rango_de_la_ui(tmp_path):
    """Un config editado a mano no puede colar boosts desmedidos al DSP.

    Regresion: volume/bass/treble/eq_gains se cargaban sin acotar; un
    'volume': 10 o 'bass': 50 llegaba tal cual al DSP (saturaba/estatica)."""
    import json

    p = tmp_path / "extremos.json"
    p.write_text(
        json.dumps(
            {
                "volume": 10.0,
                "bass": 50.0,
                "treble": -3.0,
                "eq_gains": [99, -99, 0, 0, 0, 0, 0, 0, 0],
                "custom_presets": {"bestia": [9.0, 99.0, -9.0, [50] * 9]},
            }
        ),
        encoding="utf-8",
    )
    mgr = ConfigManager(eq_band_count=9, path=str(p))
    cfg = mgr.load()
    assert cfg["volume"] == 2.0  # tope del slider
    assert cfg["bass"] == 12.0  # tope del slider
    assert cfg["treble"] == 0.0  # el slider no baja de 0
    assert cfg["eq_gains"][0] == 12.0 and cfg["eq_gains"][1] == -12.0
    assert cfg["custom_presets"]["bestia"] == (2.0, 12.0, 0.0, [12.0] * 9)


def test_load_defaults_sin_archivo(tmp_path):
    mgr = ConfigManager(eq_band_count=9, path=str(tmp_path / "no.json"))
    cfg = mgr.load()
    assert cfg["volume"] == 1.0 and cfg["bass"] == 0.0 and cfg["treble"] == 0.0
    assert cfg["eq_gains"] is None and cfg["custom_presets"] == {}
    assert cfg["latency_pref"] == 60
    assert cfg["limiter"] is True and cfg["compressor"] is True
    assert cfg["preset"]  # DEFAULT_PRESET, nunca vacío


def test_load_coacciona_valores_hostiles(tmp_path):
    """Config editado a mano con tipos absurdos: defaults, no crash."""
    p = tmp_path / "hostil.json"
    p.write_text(
        '{"volume": "loud", "bass": [1], "limiter": "sí", "latency_pref": "mañana", '
        '"eq_gains": [0, 1, 2], "custom_presets": {"roto": [1, 2]}}',
        encoding="utf-8",
    )
    mgr = ConfigManager(eq_band_count=9, path=str(p))
    cfg = mgr.load()
    assert cfg["volume"] == 1.0 and cfg["bass"] == 0.0
    assert cfg["limiter"] is True
    assert cfg["latency_pref"] == 60
    assert cfg["eq_gains"] is None  # longitud incorrecta -> se conservan las del DSP
    assert cfg["custom_presets"] == {}  # preset malformado filtrado


def test_load_presets_validos_pasran(tmp_path):
    import json

    p = tmp_path / "ok.json"
    p.write_text(
        json.dumps(
            {
                "volume": 1.25,
                "eq_gains": [1, 2, 3, 4, 5, 6, 7, 8, 9],
                "latency_pref": 40,
                "language": "en",
                "theme": "dark",
                "custom_presets": {"Noche": [0.9, 1, 0, [0] * 9]},
            }
        ),
        encoding="utf-8",
    )
    mgr = ConfigManager(eq_band_count=9, path=str(p))
    cfg = mgr.load()
    assert cfg["volume"] == 1.25
    assert cfg["eq_gains"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    assert cfg["latency_pref"] == 40
    assert cfg["language"] == "en" and cfg["theme"] == "dark"
    assert cfg["custom_presets"]["Noche"] == (0.9, 1.0, 0.0, [0.0] * 9)


def test_migrate_es_punto_de_extensión(tmp_path):
    """Hoy la migración es identidad; existe como punto único de extensión."""
    assert ConfigManager._migrate({"a": 1}) == {"a": 1}


def test_true_peak_default_y_explicito(tmp_path):
    """true_peak: ON por defecto; un false explícito se respeta."""
    import json

    sin = ConfigManager(eq_band_count=9, path=str(tmp_path / "no.json"))
    assert sin.load()["true_peak"] is True

    p = tmp_path / "tp.json"
    p.write_text(json.dumps({"true_peak": False}), encoding="utf-8")
    con = ConfigManager(eq_band_count=9, path=str(p))
    assert con.load()["true_peak"] is False
    # valor no-bool -> default
    p.write_text(json.dumps({"true_peak": "no"}), encoding="utf-8")
    assert ConfigManager(eq_band_count=9, path=str(p)).load()["true_peak"] is True
