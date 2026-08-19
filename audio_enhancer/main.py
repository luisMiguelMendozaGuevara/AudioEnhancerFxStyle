"""Punto de entrada: instancia única + ventana + tray + logging rotativo.

La lógica de instancia única (detección por título + mutex nombrado) cubre
también instancias viejas de builds que no usan mutex.
"""

import logging
import logging.handlers
import os

import customtkinter as ctk

from .app import App
from .constants import APP_NAME, WINDOW_TITLE
from .startup_metrics import StartupMetrics

LOG_FILE = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "AudioEnhancerFxStyle",
    "audio_enhancer.log",
)


def setup_logging() -> None:
    """Logging a archivo rotativo (256 KB x 2 backups) nivel WARNING."""
    log_dir = os.path.dirname(LOG_FILE)
    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=256 * 1024, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    root.setLevel(logging.WARNING)


def _bring_existing_to_front() -> bool:
    """Busca una ventana principal de otra instancia por título y la restaura.

    Devuelve True si existe (esta copia debe salir). Cubre también instancias
    viejas que no usan mutex (p.ej. autostart de una build anterior)."""
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


def _acquire_single_instance():
    """Candado de instancia única: mutex nombrado + detección por título de
    ventana. Si ya hay otra instancia (antigua o nueva), la trae al frente y
    devuelve None para que esta salga sin abrir otra ventana."""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE

        # 1) cualquier ventana existente de otra instancia (incluidas viejas
        #    builds que no tienen mutex): restaurar y salir.
        if _bring_existing_to_front():
            return None

        # 2) mutex para bloquear instancias nuevas.
        mutex_name = "Local\\AudioEnhancerFxStyle_SingleInstance"
        handle = k32.CreateMutexW(None, False, mutex_name)
        err = k32.GetLastError()
        if err == 183:  # ERROR_ALREADY_EXISTS
            _bring_existing_to_front()
            return None
        return handle
    except Exception:
        return object()  # sin guarda: dejar pasar


def main() -> None:
    setup_logging()
    logger = logging.getLogger("audio_enhancer.main")
    logger.warning("Arranque de %s", APP_NAME)
    mutex = _acquire_single_instance()
    if mutex is None:
        return  # ya hay otra instancia corriendo
    root = ctk.CTk()
    metrics = StartupMetrics()
    metrics.mark("root_created")
    app = App(root, startup_metrics=metrics)
    root.protocol("WM_DELETE_WINDOW", app._on_close)
    # icono de bandeja desde el arranque (no solo al cerrar la ventana)
    app.root.after(600, app._start_tray)
    root.mainloop()


if __name__ == "__main__":
    main()
