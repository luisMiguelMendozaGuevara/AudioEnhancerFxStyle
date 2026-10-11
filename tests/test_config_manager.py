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
    assert ok is not None  # v1 migrado a v2 (dict)
    assert ok["volume"] == 0.8 and ok["bass"] == 2.0 and ok["treble"] == 1.0
    assert ok["eq_gains"] == [0.0] * 9
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
    assert out["bueno"]["volume"] == 0.9 and out["bueno"]["eq_gains"] == [0.0] * 9
    # No-dict no lanza y devuelve vacío (JSON importado hostil).
    assert mgr.sanitize_presets(None) == {}
    assert mgr.sanitize_presets([1, 2, 3]) == {}


def test_watchdog_y_techo_seguridad_defaults_y_coercion(tmp_path):
    """Opciones nuevas (watchdog y techo de seguridad): default True y coerción
    estricta (un valor no-bool cae al default)."""
    import json

    p = tmp_path / "c.json"
    mgr = ConfigManager(eq_band_count=9, path=str(p))
    cfg = mgr.load()
    assert cfg["watchdog"] is True and cfg["safety_ceiling"] is True and cfg["final_clip"] is True
    # (PRUEBA) captura nativa: OFF por defecto.
    assert cfg["native_capture"] is False

    p.write_text(json.dumps({"watchdog": False, "safety_ceiling": "no", "final_clip": False}), encoding="utf-8")
    cfg2 = mgr.load()
    assert cfg2["watchdog"] is False
    assert cfg2["final_clip"] is False
    assert cfg2["safety_ceiling"] is True  # "no" no es bool -> default


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
    # Preset v1 (tupla) migrado a v2 (dict) con los campos nuevos a 0.
    bestia = cfg["custom_presets"]["bestia"]
    assert bestia["volume"] == 2.0 and bestia["bass"] == 12.0 and bestia["treble"] == 0.0
    assert bestia["eq_gains"] == [12.0] * 9
    assert bestia["effects"] == {}


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
    noche = cfg["custom_presets"]["Noche"]
    assert noche["volume"] == 0.9 and noche["bass"] == 1.0 and noche["treble"] == 0.0
    assert noche["eq_gains"] == [0.0] * 9


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


# ---------- presets v2 (Fase 0) ----------


def test_sanitize_preset_migra_v1_a_v2(tmp_path):
    """Un preset v1 (tupla) se migra a v2 (dict) con los campos nuevos a 0."""
    mgr = ConfigManager(eq_band_count=9, path=str(tmp_path / "x.json"))
    v2 = mgr.sanitize_preset((1.2, 3.0, 1.5, [1, 2, 3, 4, 5, 6, 7, 8, 9]))
    assert v2 is not None
    assert v2["volume"] == 1.2 and v2["bass"] == 3.0 and v2["treble"] == 1.5
    assert v2["eq_gains"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    assert v2["effects"] == {}  # migrado: nada activado
    assert v2["balance_db"] == 0.0 and v2["master_gain_db"] == 0.0 and v2["leveling"] == 0.0


def test_sanitize_preset_v2_acota_y_sanea(tmp_path):
    """Un preset v2 se acota a los rangos de la UI y descarta claves raras."""
    mgr = ConfigManager(eq_band_count=9, path=str(tmp_path / "x.json"))
    v2 = mgr.sanitize_preset(
        {
            "volume": 9.0,
            "bass": -5.0,
            "treble": 99.0,
            "eq_gains": [0] * 9,
            "effects": {"bass": 99.0, "fidelity": -3.0, "raro": 5.0},
            "balance_db": 50.0,
            "master_gain_db": -50.0,
            "leveling": 99.0,
        }
    )
    assert v2 is not None
    assert v2["volume"] == 2.0  # tope
    assert v2["bass"] == 0.0  # suelo
    assert v2["treble"] == 12.0  # tope
    assert v2["effects"] == {"bass": 10.0, "fidelity": 0.0}  # acotado y sin "raro"
    assert v2["balance_db"] == 20.0 and v2["master_gain_db"] == -20.0
    assert v2["leveling"] == 10.0


def test_sanitize_presets_acepta_mezcla_v1_y_v2(tmp_path):
    """Un mapa con presets v1 y v2 se normaliza entero a v2."""
    mgr = ConfigManager(eq_band_count=9, path=str(tmp_path / "x.json"))
    out = mgr.sanitize_presets(
        {
            "viejo": (1.0, 1.0, 1.0, [0] * 9),
            "nuevo": {"volume": 1.0, "eq_gains": [0] * 9, "effects": {"surround": 4.0}},
            "malo": (1.0, 1.0),  # longitud inválida -> fuera
        }
    )
    assert set(out) == {"viejo", "nuevo"}
    assert out["viejo"]["effects"] == {}
    assert out["nuevo"]["effects"] == {"surround": 4.0}


# ---------- EQ avanzado (Fase 5): bandas y Q ----------


def test_preset_con_bandas_y_q_round_trip(tmp_path):
    mgr = ConfigManager(eq_band_count=9, path=str(tmp_path / "x.json"))
    p = mgr.sanitize_preset(
        {
            "volume": 1.0,
            "eq_gains": [0.0] * 5,
            "eq_bands": [100.0, 400.0, 1000.0, 4000.0, 10000.0],
            "eq_q": 3.0,
        }
    )
    assert p is not None
    assert p["eq_bands"] == [100.0, 400.0, 1000.0, 4000.0, 10000.0]
    assert p["eq_q"] == 3.0


def test_preset_con_longitud_no_soportada_se_rechaza(tmp_path):
    mgr = ConfigManager(eq_band_count=9, path=str(tmp_path / "x.json"))
    assert mgr.sanitize_preset([1.0, 0.0, 0.0, [0.0] * 8]) is None
    assert mgr.sanitize_preset({"eq_gains": [0.0] * 8}) is None


def test_preset_sin_bandas_usa_el_set_por_defecto(tmp_path):
    mgr = ConfigManager(eq_band_count=9, path=str(tmp_path / "x.json"))
    p = mgr.sanitize_preset({"eq_gains": [0.0] * 5})
    assert p is not None and len(p["eq_bands"]) == 5
    assert p["eq_bands"] == sorted(p["eq_bands"])


# ---------- capa enhancer en el config (Fase 6) ----------


def test_load_efectos_y_balance_master(tmp_path):
    import json

    p = tmp_path / "fx.json"
    p.write_text(
        json.dumps(
            {
                "effects": {"dynamic_boost": 5.0, "fidelity": 99.0, "raro": 3.0},
                "balance_db": -5.0,
                "master_gain_db": 3.0,
                "leveling": 4.0,
            }
        ),
        encoding="utf-8",
    )
    cfg = ConfigManager(eq_band_count=9, path=str(p)).load()
    # Claves conocidas y acotadas; "raro" fuera.
    assert cfg["effects"] == {"dynamic_boost": 5.0, "fidelity": 10.0}
    assert cfg["balance_db"] == -5.0
    assert cfg["master_gain_db"] == 3.0
    assert cfg["leveling"] == 4.0


def test_load_efectos_por_defecto_vacios(tmp_path):
    cfg = ConfigManager(eq_band_count=9, path=str(tmp_path / "no.json")).load()
    assert cfg["effects"] == {}
    assert cfg["balance_db"] == 0.0 and cfg["master_gain_db"] == 0.0
    assert cfg["leveling"] == 0.0
