# -*- coding: utf-8 -*-
"""Persistencia: carga/guardado del config.json en %APPDATA%."""

import json
import os
from typing import Any

from .constants import CONFIG_PATH


def load_config(path: str = CONFIG_PATH) -> dict[str, Any]:
    """Carga la configuración. Devuelve {} si no existe o está dañada."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return {}
    return cfg if isinstance(cfg, dict) else {}


def save_config(cfg: dict[str, Any], path: str = CONFIG_PATH) -> bool:
    """Guarda la configuración creando el directorio padre si hace falta."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False