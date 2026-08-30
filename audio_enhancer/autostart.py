"""Autoarranque con Windows (HKCU\\Software\\...\\Run).

Aislado en un módulo propio (SRP): la ventana solo consulta y conmuta; aquí
vive todo el acceso a winreg. Antes la lógica residía dentro de
``NewMainWindow`` y la cadena del comando llegó corrupta al registro: las
comillas se perdieron en una edición y el valor escrito era el LITERAL
``' + sys.executable + "" "" + os.path.abspath(sys.argv[0]) + '`` (el código
quedó dentro de un string). Con rutas con espacios ese valor jamás podría
ejecutarse; ahora el comando se construye con una función pura testeable y
entrecomillado explícito.

Notas de robustez:
- Todos los accesos al registro usan context managers (PyHKEY se cierra solo
  incluso si QueryValueEx/SetValueEx lanzan).
- Cualquier fallo de winreg se registra en el log y se degrada a False: el
  autoarranque es una comodidad, jamás debe tumbar la app.
- En plataformas sin winreg (Linux/macOS, p. ej. la suite de tests) las
  funciones devuelven False/None sin lanzar.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys

logger = logging.getLogger("audio_enhancer.autostart")

AUTOSTART_KEY = "AudioEnhancerFxStyle"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def build_autostart_command() -> str:
    """Comando a registrar: ``"python.exe" "script.py"``.

    Cada ruta va entrecomillada por separado: sin comillas, ``Program Files``
    o cualquier carpeta con espacios parte el comando en dos tokens y el
    Run key ejecuta otra cosa (o nada)."""
    return f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'


def is_enabled() -> bool:
    """True si existe la entrada de autoarranque (HKCU)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_KEY)
            return bool(value)
    except FileNotFoundError:
        return False  # entrada ausente: estado normal, no es un error
    except Exception:
        logger.debug("No se pudo consultar el autoarranque", exc_info=True)
        return False


def set_enabled(enable: bool) -> bool:
    """Crea o elimina la entrada de autoarranque. Devuelve True si tuvo éxito."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, AUTOSTART_KEY, 0, winreg.REG_SZ, build_autostart_command())
            else:
                # Borrar dos veces es inofensivo: FileNotFound => ya estaba fuera.
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, AUTOSTART_KEY)
        return True
    except Exception:
        logger.warning("No se pudo %s el autoarranque", "activar" if enable else "desactivar", exc_info=True)
        return False
