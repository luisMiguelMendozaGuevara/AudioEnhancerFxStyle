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
from typing import Any

from .config import load_config, save_config
from .constants import CONFIG_PATH, DEFAULT_PRESET, LATENCY_CHOICES_MS

logger = logging.getLogger("audio_enhancer.config_manager")

# Rangos de la UI (única fuente de verdad de lo que el usuario puede pedir).
# Se ACOTAN al cargar: un config.json editado a mano no puede colar un boost
# desmedido al DSP (p. ej. volume=10, bass=50), que sonaría a estática/
# saturación. Coinciden con los sliders: volumen 0..2x, bass/treble 0..+12 dB,
# bandas EQ -12..+12 dB.
VOLUME_MIN, VOLUME_MAX = 0.0, 2.0
BASS_MIN, BASS_MAX = 0.0, 12.0
TREBLE_MIN, TREBLE_MAX = 0.0, 12.0
EQ_GAIN_MIN, EQ_GAIN_MAX = -12.0, 12.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class ConfigManager:
    """Esquema del config.json + coerción segura + defaults."""

    def __init__(self, eq_band_count: int, path: str | None = None) -> None:
        self._eq_count = int(eq_band_count)
        # None = resolver CONFIG_PATH en CADA operación (no al importar): así
        # los tests pueden redirigir la ruta parcheando el módulo.
        self.path = path

    def _resolve_path(self) -> str:
        return self.path or CONFIG_PATH

    # ---------- coerciones (antes _cfg_float/_cfg_bool de la ventana) ----------

    @staticmethod
    def as_float(value: Any, default: float) -> float:
        """float con red de seguridad (C2): config editado a mano no tumba el arranque."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def as_bool(value: Any, default: bool) -> bool:
        """bool estricto: solo JSON true/false; cualquier otra cosa -> default."""
        return value if isinstance(value, bool) else default

    # ---------- presets ----------

    def sanitize_preset(self, value: Any) -> tuple[float, float, float, list[float]] | None:
        """Valida (vol, bass, treble, gains) con coerción segura; None si inválido.

        Un preset con ganancias de otra longitud desalinearía _c_eq del DSP
        (broadcasting roto dentro del callback); mejor fuera en la puerta."""
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 4
            or not isinstance(value[3], (list, tuple))
            or len(value[3]) != self._eq_count
        ):
            return None
        try:
            return (
                _clamp(float(value[0]), VOLUME_MIN, VOLUME_MAX),
                _clamp(float(value[1]), BASS_MIN, BASS_MAX),
                _clamp(float(value[2]), TREBLE_MIN, TREBLE_MAX),
                [_clamp(float(g), EQ_GAIN_MIN, EQ_GAIN_MAX) for g in value[3]],
            )
        except (TypeError, ValueError):
            return None

    def sanitize_presets(self, raw: Any) -> dict[str, tuple[float, float, float, list[float]]]:
        """Sanea un mapa {nombre: preset}; descarta los inválidos.

        Se usa al cargar el config y al IMPORTAR un JSON de presets: un archivo
        externo editado a mano no puede colar ganancias de longitud incorrecta
        (desalinearía el EQ del DSP) ni valores fuera de rango."""
        out: dict[str, tuple[float, float, float, list[float]]] = {}
        if isinstance(raw, dict):
            for name, value in raw.items():
                preset = self.sanitize_preset(value)
                if preset is not None:
                    out[str(name)] = preset
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

        # EQ: lista de la longitud exacta y floats; si no, None (el caller
        # conserva las ganancias actuales del DSP).
        eq_gains = None
        candidate = raw.get("eq_gains")
        if isinstance(candidate, list) and len(candidate) == self._eq_count:
            try:
                eq_gains = [_clamp(float(g), EQ_GAIN_MIN, EQ_GAIN_MAX) for g in candidate]
            except (TypeError, ValueError):
                eq_gains = None

        # Latencia: solo los valores ofrecidos por la UI (40/60/100).
        try:
            latency = int(raw.get("latency_pref", 60) or 60)
        except (TypeError, ValueError):
            latency = 60
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

        return {
            "source": str(raw.get("source", "") or ""),
            "output": str(raw.get("output", "") or ""),
            "preset": str(raw.get("preset", "") or "") or DEFAULT_PRESET,
            "language": language,
            "theme": theme,
            "volume": _clamp(self.as_float(raw.get("volume"), 1.0), VOLUME_MIN, VOLUME_MAX),
            "bass": _clamp(self.as_float(raw.get("bass"), 0.0), BASS_MIN, BASS_MAX),
            "treble": _clamp(self.as_float(raw.get("treble"), 0.0), TREBLE_MIN, TREBLE_MAX),
            "eq_gains": eq_gains,
            "limiter": self.as_bool(raw.get("limiter"), True),
            "compressor": self.as_bool(raw.get("compressor"), True),
            "true_peak": self.as_bool(raw.get("true_peak"), True),
            "safety_ceiling": self.as_bool(raw.get("safety_ceiling"), True),
            "final_clip": self.as_bool(raw.get("final_clip"), True),
            "crossfeed": self.as_bool(raw.get("crossfeed"), False),
            "crossfeed_preset": str(raw.get("crossfeed_preset", "Natural") or "Natural"),
            "crossfeed_cut_hz": int(_clamp(self.as_float(raw.get("crossfeed_cut_hz"), 700.0), 300.0, 2000.0)),
            "crossfeed_feed_db": _clamp(self.as_float(raw.get("crossfeed_feed_db"), 4.5), 1.0, 15.0),
            "watchdog": self.as_bool(raw.get("watchdog"), True),
            "latency_pref": latency,
            "minimize_to_tray": self.as_bool(raw.get("minimize_to_tray"), True),
            "autostart_audio": self.as_bool(raw.get("autostart_audio"), True),
            "notifications": self.as_bool(raw.get("notifications"), True),
            "custom_presets": custom,
        }

    def save(self, cfg: dict[str, Any]) -> bool:
        """Guarda la configuración (I/O atómica en config.py). False si falló."""
        if not save_config(cfg, self._resolve_path()):
            logger.warning("No se pudo persistir la configuración en %s", self.path)
            return False
        return True
