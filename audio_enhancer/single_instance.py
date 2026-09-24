"""Instancia única y logging compartidos entre entradas (Tk/Qt).

La lógica de instancia única (detección por título + mutex nombrado) cubre
también instancias viejas de builds que no usan mutex.
"""

import logging
import logging.handlers
import os

from .constants import WINDOW_TITLE

LOG_FILE = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "AudioEnhancerFxStyle",
    "audio_enhancer.log",
)


def setup_logging() -> None:
    """Logging a archivo rotativo (256 KB x 2 backups) nivel INFO.

    INFO (no WARNING): el R3 reclasifico arranque/parada/metricas como info;
    con el nivel en WARNING esos mensajes se perdian y el log quedaba mudo.
    El tamano esta acotado por la rotacion, asi que INFO es seguro."""
    log_dir = os.path.dirname(LOG_FILE)
    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=256 * 1024, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    root.setLevel(logging.INFO)


def _bring_existing_to_front() -> bool:
    """Busca una ventana principal de otra instancia por título y la restaura.

    Devuelve True si existe (esta copia debe salir). Cubre también instancias
    viejas que no usan mutex (p. ej. autostart de una build anterior)."""
    try:
        import ctypes
        from ctypes import wintypes

        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32

        u32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
        u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u32.GetWindowTextW.restype = ctypes.c_int
        u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u32.GetWindowTextLengthW.restype = ctypes.c_int
        u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u32.SetForegroundWindow.argtypes = [wintypes.HWND]
        u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
        u32.GetWindowThreadProcessId.restype = wintypes.DWORD

        my_pid = k32.GetCurrentProcessId()
        expected = WINDOW_TITLE
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, lparam):
            if u32.GetWindowTextLengthW(hwnd) <= 0:
                return True
            buf = ctypes.create_unicode_buffer(256)
            n = u32.GetWindowTextW(hwnd, buf, 256)
            if n > 0 and buf.value == expected:
                pid = wintypes.DWORD()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value and pid.value != my_pid:
                    found.append(hwnd)
                    return False
            return True

        u32.EnumWindows(_cb, 0)
        if found:
            hwnd = found[0]
            u32.ShowWindow(hwnd, 9)  # SW_RESTORE
            u32.SetForegroundWindow(hwnd)
            return True
        return False
    except Exception:
        return False


# Handle del mutex de instancia única. DEBE quedar referenciado durante toda la
# vida del proceso: si el objeto HANDLE se recolecta, Windows CIERRA el mutex y
# una segunda instancia lo recrea (permitiendo ventanas duplicadas → 2 iconos
# de bandeja → crash intermitente en pyside6).
_MUTEX_HANDLE = None


def acquire_single_instance():
    """Candado de instancia única: mutex nombrado + detección por título.

    Si ya hay otra instancia (antigua sin mutex o nueva), la trae al frente y
    devuelve None para que esta salga sin abrir una segunda ventana/bandeja."""
    global _MUTEX_HANDLE
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE

        # 1) Cualquier ventana existente de otra instancia (incluidas builds
        #    viejas sin mutex): restaurar y salir.
        if _bring_existing_to_front():
            return None

        # 2) Mutex para bloquear instancias nuevas. Se guarda el handle en una
        #    referencia de módulo para que NO se libere durante el proceso.
        mutex_name = "Local\\AudioEnhancerFxStyle_SingleInstance"
        handle = k32.CreateMutexW(None, False, mutex_name)
        err = k32.GetLastError()
        if err == 183:  # ERROR_ALREADY_EXISTS
            _bring_existing_to_front()
            return None
        _MUTEX_HANDLE = handle  # mantiene vivo el mutex
        return handle
    except Exception:
        return object()  # sin guarda: dejar pasar
