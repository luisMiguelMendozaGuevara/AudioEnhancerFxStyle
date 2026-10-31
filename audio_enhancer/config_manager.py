"""Gestor de configuración: esquema, saneo, defaults y persistencia.

(R3-C2) Extraído de NewMainWindow: toda conversión de tipos y validación
del config.json vive aquí. La ventana pide ``load()`` y recibe un
diccionario SANEADO con tipos garantizados; un config.json editado a mano
no puede tumbar el arranque ni colar presets malformados al DSP.

Capas (de abajo arriba):
    config.py         — I/O atómico del JSON (sin semántica)
    ConfigManager     — esquema, coerción, defaults y migración (este módulo)
    NewMainWindow     — consume el diccionario saneado; pinta la interfaz
"""

from __future__ import annotations

import logging
import math
from typing import Any

from .config import load_config, save_config
from .constants import CONFIG_PATH, DEFAULT_PRESET, LATENCY_CHOICES_MS
from .dsp import (
    BALANCE_DB_MAX,
    BALANCE_DB_MIN,
    EFFECT_KEYS,
    EFFECT_MAX,
    EFFECT_MIN,
    EQ_BAND_SETS,
    EQ_FREQ_MAX,
    EQ_FREQ_MIN,
    MASTER_DB_MAX,
    MASTER_DB_MIN,
)

logger = logging.getLogger("audio_enhancer.config_manager")

# Paginas de la sidebar (ids estables): la ultima visitada se reabre al
# reiniciar. Debe coincidir con los ids de widgets/sidebar.py.
VALID_PAGES = ("home", "equalizer", "effects", "audio", "presets", "settings")

# Rangos de la UI (única fuente de verdad de lo que el usuario puede pedir).
# Se ACOTAN al cargar: un config.json editado a mano no puede colar un boost
# desmedido al DSP (p. ej. volume=10, bass=50), que sonaría a estática/
# saturación. Coinciden con los sliders: volumen 0..2x, bass/treble 0..+12 dB,
# bandas EQ -12..+12 dB, efectos 0..10, balance/master ±20 dB, leveling 0..10.
VOLUME_MIN, VOLUME_MAX = 0.0, 2.0
BASS_MIN, BASS_MAX = 0.0, 12.0
TREBLE_MIN, TREBLE_MAX = 0.0, 12.0
EQ_GAIN_MIN, EQ_GAIN_MAX = -12.0, 12.0
LEVELING_MIN, LEVELING_MAX = 0.0, 10.0


def _clamp(value: float, lo: float, hi: float) -> float:
    """Acota al rango. Un NaN se resuelve al MÍNIMO del rango.

    ``max(lo, min(hi, nan))`` devolvía ``hi`` (las comparaciones con NaN son
    falsas), y acotar al máximo es justo lo contrario de lo prudente en
    controles de audio: volumen 2x o boost 10 en vez de silencio/apagado.
    Defensa en profundidad: ``as_float`` ya descarta los no finitos."""
    if value != value:  # NaN
        return lo
    return max(lo, min(hi, value))


class ConfigManager:
    """Esquema del config.json + coerción segura + defaults."""

    def __init__(self, eq_band_count: int, path: str | None = None) -> None:
        # `eq_band_count` se mantiene por compatibilidad de la API: desde la
        # Fase 5 la validación acepta cualquier nº de bandas soportado
        # (5/9/10/15/31, ver EQ_BAND_SETS), así que no se usa aquí.
        self.path = path

    def _resolve_path(self) -> str:
        return self.path or CONFIG_PATH

    # ---------- coerciones (antes _cfg_float/_cfg_bool de la ventana) ----------

    @staticmethod
    def as_float(value: Any, default: float) -> float:
        """float con red de seguridad (C2): config editado a mano no tumba el arranque.

        Los valores NO FINITOS se tratan como ausentes: ``json.load`` acepta
        ``NaN``/``Infinity`` (extensión de Python, no JSON estricto) y al
        acotarlos caían en el MÁXIMO del rango (volumen 2x, efectos 10)."""
        try:
            out = float(value)
        except (TypeError, ValueError):
            return default
        return out if math.isfinite(out) else default

    @staticmethod
    def as_bool(value: Any, default: bool) -> bool:
        """bool estricto: solo JSON true/false; cualquier otra cosa -> default."""
        return value if isinstance(value, bool) else default

    def sanitize_effects(self, raw: Any) -> dict[str, float]:
        """Sanea un dict de efectos enhancer (0-10, solo claves conocidas)."""
        out: dict[str, float] = {}
        if isinstance(raw, dict):
            for key in EFFECT_KEYS:
                if key in raw:
                    out[key] = _clamp(self.as_float(raw[key], 0.0), EFFECT_MIN, EFFECT_MAX)
        return out

    # ---------- presets ----------

    # Esquema de preset v2 (dict). Campos: volume, bass, treble, eq_gains,
    # eq_bands, eq_q, effects{5}, balance_db, master_gain_db, leveling. El
    # esquema v1 (tupla de 4) se migra automáticamente a v2.
    PRESET_V2_KEYS = (
        "volume",
        "bass",
        "treble",
        "eq_gains",
        "eq_bands",
        "eq_q",
        "effects",
        "balance_db",
        "master_gain_db",
        "leveling",
    )

    def _preset_v1_to_v2(self, value: Any) -> dict[str, Any] | None:
        """Migra un preset v1 (vol, bass, treble, gains) al esquema v2.

        Los campos nuevos se rellenan a 0 (apagados) y las bandas con el set
        por defecto del número de ganancias: un preset viejo conserva
        EXACTAMENTE el sonido que tenía."""
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 4
            or not isinstance(value[3], (list, tuple))
            or len(value[3]) not in EQ_BAND_SETS
        ):
            return None
        n = len(value[3])
        return {
            "volume": value[0],
            "bass": value[1],
            "treble": value[2],
            "eq_gains": list(value[3]),
            "eq_bands": list(EQ_BAND_SETS[n]),
            "eq_q": 1.4,
            "effects": {},
            "balance_db": 0.0,
            "master_gain_db": 0.0,
            "leveling": 0.0,
        }

    def sanitize_preset(self, value: Any) -> dict[str, Any] | None:
        """Valida un preset (v1 tupla o v2 dict) y lo normaliza a v2; None si inválido.

        Un preset con ganancias de longitud no soportada desalinearía _c_eq del
        DSP (broadcasting roto dentro del callback); mejor fuera en la puerta."""
        if isinstance(value, (list, tuple)):
            value = self._preset_v1_to_v2(value)
            if value is None:
                return None
        if not isinstance(value, dict):
            return None
        gains = value.get("eq_gains")
        if not isinstance(gains, (list, tuple)) or len(gains) not in EQ_BAND_SETS:
            return None
        n = len(gains)
        bands_raw = value.get("eq_bands")
        try:
            if isinstance(bands_raw, (list, tuple)) and len(bands_raw) == n:
                bands = sorted(_clamp(float(f), EQ_FREQ_MIN, EQ_FREQ_MAX) for f in bands_raw)
            else:
                bands = list(EQ_BAND_SETS[n])
        except (TypeError, ValueError):
            bands = list(EQ_BAND_SETS[n])
        try:
            effects = self.sanitize_effects(value.get("effects"))
            return {
                "volume": _clamp(float(value.get("volume", 1.0)), VOLUME_MIN, VOLUME_MAX),
                "bass": _clamp(float(value.get("bass", 0.0)), BASS_MIN, BASS_MAX),
                "treble": _clamp(float(value.get("treble", 0.0)), TREBLE_MIN, TREBLE_MAX),
                "eq_gains": [_clamp(float(g), EQ_GAIN_MIN, EQ_GAIN_MAX) for g in gains],
                "eq_bands": bands,
                "eq_q": _clamp(self.as_float(value.get("eq_q"), 1.4), 0.2, 10.0),
                "effects": effects,
                "balance_db": _clamp(self.as_float(value.get("balance_db"), 0.0), BALANCE_DB_MIN, BALANCE_DB_MAX),
                "master_gain_db": _clamp(self.as_float(value.get("master_gain_db"), 0.0), MASTER_DB_MIN, MASTER_DB_MAX),
                "leveling": _clamp(self.as_float(value.get("leveling"), 0.0), LEVELING_MIN, LEVELING_MAX),
            }
        except (TypeError, ValueError):
            return None

    def sanitize_presets(self, raw: Any) -> dict[str, dict[str, Any]]:
        """Sanea un mapa {nombre: preset}; descarta los inválidos.

        Se usa al cargar el config y al IMPORTAR un JSON de presets: un archivo
        externo editado a mano no puede colar ganancias de longitud incorrecta
        (desalinearía el EQ del DSP) ni valores fuera de rango. Acepta v1
        (tupla) y v2 (dict) y devuelve SIEMPRE v2."""
        out: dict[str, dict[str, Any]] = {}
        if isinstance(raw, dict):
            for name, value in raw.items():
                preset = self.sanitize_preset(value)
                if preset is not None:
                    out[str(name)] = preset
        return out

    @staticmethod
    def sanitize_device_presets(raw: Any) -> dict[str, str]:
        """Mapa dispositivo_salida -> nombre_preset (solo strings no vacíos)."""
        out: dict[str, str] = {}
        if isinstance(raw, dict):
            for dev, preset_name in raw.items():
                if isinstance(dev, str) and isinstance(preset_name, str):
                    d, p = dev.strip(), preset_name.strip()
                    if d and p:
                        out[d] = p
        return out

    # ---------- migración ----------

    @staticmethod
    def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
        """Migraciones de configs antiguas. Hoy: identidad.

        Punto único y testeable para cuando cambie el esquema (p. ej.
        renombrar claves, convertir listas de presets a dict, etc.): se
        añade un paso aquí y el resto del código ignora las versiones viejas.
        """
        return raw

    # ---------- alto nivel ----------

    def load(self) -> dict[str, Any]:
        """Lee, migra y SANEAD la configuración completa. Nunca lanza.

        Devuelve SIEMPRE todas las claves del esquema con tipos garantizados
        (los opcionales como None si no hay valor usable)."""
        raw = self._migrate(load_config(self._resolve_path()))
        if not raw:
            raw = {}

        # EQ: lista de una longitud soportada (5/9/10/15/31) y floats; si no,
        # None (el caller conserva las ganancias actuales del DSP). Con las
        # bandas (Fase 5): si faltan o no cuadran, se usa el set por defecto.
        eq_gains = None
        eq_bands = None
        candidate = raw.get("eq_gains")
        if isinstance(candidate, list) and len(candidate) in EQ_BAND_SETS:
            try:
                eq_gains = [_clamp(float(g), EQ_GAIN_MIN, EQ_GAIN_MAX) for g in candidate]
            except (TypeError, ValueError):
                eq_gains = None
        if eq_gains is not None:
            n = len(eq_gains)
            bands_raw = raw.get("eq_bands")
            if isinstance(bands_raw, list) and len(bands_raw) == n:
                try:
                    eq_bands = sorted(_clamp(float(f), EQ_FREQ_MIN, EQ_FREQ_MAX) for f in bands_raw)
                except (TypeError, ValueError):
                    eq_bands = None
            if eq_bands is None:
                eq_bands = list(EQ_BAND_SETS[n])

        # Latencia: solo los valores ofrecidos por la UI (40/60/100/250).
        # Se pasa por as_float: ``int(inf)`` lanzaba OverflowError y ``load()``
        # promete no lanzar nunca (reventaba el arranque de la ventana).
        latency = int(self.as_float(raw.get("latency_pref", 60) or 60, 60.0))
        if latency not in LATENCY_CHOICES_MS:
            latency = LATENCY_CHOICES_MS[1]

        # Presets personalizados: dict nombre -> tupla saneada.
        custom = self.sanitize_presets(raw.get("custom_presets"))

        language = raw.get("language")
        if language not in ("es", "en"):
            language = None
        theme = raw.get("theme")
        if theme not in ("dark", "light"):
            theme = None

        # Ultima pagina abierta: solo ids conocidos (un config editado a mano
        # no puede navegar a una pagina inexistente).
        last_page = raw.get("last_page")
        if last_page not in VALID_PAGES:
            last_page = "home"

        return {
            "source": str(raw.get("source", "") or ""),
            "output": str(raw.get("output", "") or ""),
            "preset": str(raw.get("preset", "") or "") or DEFAULT_PRESET,
            "language": language,
            "theme": theme,
            "last_page": last_page,
            "volume": _clamp(self.as_float(raw.get("volume"), 1.0), VOLUME_MIN, VOLUME_MAX),
            "bass": _clamp(self.as_float(raw.get("bass"), 0.0), BASS_MIN, BASS_MAX),
            "treble": _clamp(self.as_float(raw.get("treble"), 0.0), TREBLE_MIN, TREBLE_MAX),
            "eq_gains": eq_gains,
            "eq_bands": eq_bands,
            "eq_q": _clamp(self.as_float(raw.get("eq_q"), 1.4), 0.2, 10.0),
            # Capa enhancer (Fase 6): efectos + balance/master/leveling.
            "effects": self.sanitize_effects(raw.get("effects")),
            "balance_db": _clamp(self.as_float(raw.get("balance_db"), 0.0), BALANCE_DB_MIN, BALANCE_DB_MAX),
            "master_gain_db": _clamp(self.as_float(raw.get("master_gain_db"), 0.0), MASTER_DB_MIN, MASTER_DB_MAX),
            "leveling": _clamp(self.as_float(raw.get("leveling"), 0.0), LEVELING_MIN, LEVELING_MAX),
            "limiter": self.as_bool(raw.get("limiter"), True),
            "compressor": self.as_bool(raw.get("compressor"), True),
            "true_peak": self.as_bool(raw.get("true_peak"), True),
            # Medición de pico real (solo diagnóstico; no afecta al audio).
            "medir_true_peak": self.as_bool(raw.get("medir_true_peak"), True),
            "safety_ceiling": self.as_bool(raw.get("safety_ceiling"), True),
            "final_clip": self.as_bool(raw.get("final_clip"), True),
            "crossfeed": self.as_bool(raw.get("crossfeed"), False),
            "crossfeed_preset": str(raw.get("crossfeed_preset", "Natural") or "Natural"),
            "crossfeed_cut_hz": int(_clamp(self.as_float(raw.get("crossfeed_cut_hz"), 700.0), 300.0, 2000.0)),
            "crossfeed_feed_db": _clamp(self.as_float(raw.get("crossfeed_feed_db"), 4.5), 1.0, 15.0),
            "watchdog": self.as_bool(raw.get("watchdog"), True),
            # (PRUEBA) captura nativa sin cable virtual: OFF por defecto.
            "native_capture": self.as_bool(raw.get("native_capture"), False),
            "latency_pref": latency,
            "minimize_to_tray": self.as_bool(raw.get("minimize_to_tray"), True),
            "autostart_audio": self.as_bool(raw.get("autostart_audio"), True),
            "notifications": self.as_bool(raw.get("notifications"), True),
            "custom_presets": custom,
            "device_presets": self.sanitize_device_presets(raw.get("device_presets")),
        }

    def save(self, cfg: dict[str, Any]) -> bool:
        """Guarda la configuración (I/O atómica en config.py). False si falló."""
        if not save_config(cfg, self._resolve_path()):
            logger.warning("No se pudo persistir la configuración en %s", self.path)
            return False
        return True
