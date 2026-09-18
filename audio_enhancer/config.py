"""Persistencia: carga/guardado ATÓMICO del config.json en %APPDATA%.

El guardado escribe a un ``.tmp`` hermano, fuerza el volcado a disco
(fsync) y renombra con ``os.replace``: o queda el config anterior completo
o el nuevo completo, jamás un JSON a medias. Antes, un corte a mitad de
``json.dump`` dejaba el archivo corrupto y con él se perdían los presets
personalizados del usuario.
"""

import contextlib
import json
import logging
import os
from typing import Any

from .constants import CONFIG_PATH

logger = logging.getLogger("audio_enhancer.config")


def load_config(path: str = CONFIG_PATH) -> dict[str, Any]:
    """Carga la configuración. Devuelve {} si no existe o está dañada."""
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return {}  # primera ejecución: estado normal, sin ruido en el log
    except Exception:
        # JSON corrupto (edición a mano, corte de luz con el guardado viejo
        # no atómico): se deja rastro y se sigue con defaults; el próximo
        # save_config repara el archivo.
        logger.warning("Config ilegible (%s): se usan valores por defecto", path, exc_info=True)
        return {}
    return cfg if isinstance(cfg, dict) else {}


def save_config(cfg: dict[str, Any], path: str = CONFIG_PATH) -> bool:
    """Guarda la configuración de forma atómica (tmp + fsync + os.replace)."""
    tmp = f"{path}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception:
        logger.warning("No se pudo guardar la configuración en %s", path, exc_info=True)
        with contextlib.suppress(OSError):
            os.remove(tmp)  # no dejar basura .tmp si la escritura falló a medias
        return False
