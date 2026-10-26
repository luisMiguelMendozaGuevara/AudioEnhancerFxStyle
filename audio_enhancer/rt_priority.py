"""Prioridad de tiempo real para los hilos que procesan audio.

PortAudio registra sus propios callbacks en MMCSS (MultiMedia Class Scheduler
Service) bajo la tarea "Pro Audio", pero los hilos que hacen el trabajo real de
esta aplicacion (la bomba de ``AudioEngine`` y el bucle del proceso hijo del
DSP) corren como hilos normales: cualquier repintado de la interfaz, barrido del
GC o arranque de otro proceso les roba CPU y el ring de salida se vacia ->
cortes audibles bajo carga. Este modulo expone las dos herramientas de Win32
que lo evitan:

* ``AvSetMmThreadCharacteristicsW`` (``avrt.dll``): registra el hilo ACTUAL en
  MMCSS con la tarea "Pro Audio" (prioridades de scheduler 23-26) y
  ``AvSetMmThreadPriority`` fija su prioridad relativa dentro de la clase.
* ``SetThreadPriority`` (``kernel32``): sube la prioridad clasica del hilo por
  encima de lo normal, como ya hacia ``AudioEngine._set_thread_priority``.

Contrato: NADA de aqui lanza. Todo es *best effort* y degrada en silencio
dejando rastro solo en ``logger.debug``. En Linux (el CI corre tambien alli) no
existe ``ctypes.WinDLL`` ni ``avrt.dll``, asi que el registro simplemente no
ocurre.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from typing import Any

logger = logging.getLogger("audio_enhancer.rt_priority")

# Valores de AVRT_PRIORITY_* (``avrt.h``): prioridad relativa dentro de MMCSS.
AVRT_PRIORITY_LOW = -1
AVRT_PRIORITY_NORMAL = 0
AVRT_PRIORITY_HIGH = 1
AVRT_PRIORITY_CRITICAL = 2

# Win32: THREAD_SET_INFORMATION y THREAD_PRIORITY_ABOVE_NORMAL.
THREAD_SET_INFORMATION = 0x0020
THREAD_PRIORITY_ABOVE_NORMAL = 1

DEFAULT_TASK_NAME = "Pro Audio"


def _load_avrt() -> Any:
    """Carga ``avrt.dll`` (solo Windows) y declara los prototipos de MMCSS.

    La carga es perezosa y ocurre DENTRO de la funcion a proposito: en Linux
    ``ctypes.WinDLL`` no existe y cargar la DLL a nivel de modulo romperia el
    import del paquete (el CI ejecuta la suite en Linux). Devuelve ``None`` si
    la plataforma o la DLL no permiten usarla; nunca lanza.
    """
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        logger.debug("ctypes.WinDLL no existe en esta plataforma: sin MMCSS")
        return None
    try:
        avrt = win_dll("avrt", use_last_error=True)
        # AvSetMmThreadCharacteristicsW(LPCWSTR, LPDWORD) -> HANDLE
        avrt.AvSetMmThreadCharacteristicsW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
        avrt.AvSetMmThreadCharacteristicsW.restype = ctypes.c_void_p
        # AvSetMmThreadPriority(HANDLE, int) -> BOOL
        avrt.AvSetMmThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        avrt.AvSetMmThreadPriority.restype = ctypes.c_int
        # AvRevertMmThreadCharacteristics(HANDLE) -> BOOL
        avrt.AvRevertMmThreadCharacteristics.argtypes = [ctypes.c_void_p]
        avrt.AvRevertMmThreadCharacteristics.restype = ctypes.c_int
    except Exception:
        logger.debug("No se pudo cargar avrt.dll: MMCSS no disponible", exc_info=True)
        return None
    return avrt


def mmcss_available() -> bool:
    """True si se puede usar MMCSS (Windows y ``avrt.dll`` cargable). No lanza nunca."""
    if os.name != "nt":
        return False
    try:
        return _load_avrt() is not None
    except Exception:  # pragma: no cover - defensivo: _load_avrt ya captura todo
        logger.debug("Fallo inesperado comprobando MMCSS", exc_info=True)
        return False


class ProAudioThread:
    """Registra el hilo ACTUAL en MMCSS bajo la tarea indicada.

    Uso::

        with ProAudioThread("Pro Audio"):
            ...trabajo de audio...

    Contrato:

    * En plataformas no-Windows o si ``avrt.dll`` falla no hace nada y no lanza
      (degrada en silencio, dejando rastro solo con ``logger.debug``).
    * Al salir revierte el registro con ``AvRevertMmThreadCharacteristics``.
    * Es idempotente ante doble ``__enter__``/``__exit__``: solo registra una
      vez y revierte una vez.
    """

    def __init__(self, task_name: str = DEFAULT_TASK_NAME, priority: int = AVRT_PRIORITY_CRITICAL) -> None:
        self.task_name = task_name
        self.priority = priority
        self._avrt: Any = None
        self._handle: int | None = None
        self._task_index = 0

    @property
    def registered(self) -> bool:
        """True si ``AvSetMmThreadCharacteristicsW`` devolvio un handle valido."""
        return self._handle is not None

    @property
    def task_index(self) -> int:
        """Indice de tarea que asigno MMCSS (0 si no se registro)."""
        return self._task_index

    def __enter__(self) -> ProAudioThread:
        if self._handle is not None:
            return self  # ya registrado: idempotente
        avrt = _load_avrt()
        if avrt is None:
            return self
        try:
            index = ctypes.c_ulong(0)
            handle = avrt.AvSetMmThreadCharacteristicsW(self.task_name, ctypes.byref(index))
            if not handle:
                logger.debug("MMCSS rechazo la tarea %r (handle nulo)", self.task_name)
                return self
            self._avrt = avrt
            self._handle = int(handle)
            self._task_index = int(index.value)
        except Exception:
            logger.debug("No se pudo registrar el hilo en MMCSS", exc_info=True)
            self._avrt = None
            self._handle = None
            self._task_index = 0
            return self
        # La prioridad relativa es un extra: si falla se mantiene el registro
        # (que __exit__ revertira igualmente).
        try:
            if not avrt.AvSetMmThreadPriority(handle, self.priority):
                logger.debug("AvSetMmThreadPriority fallo (prioridad %d)", self.priority)
        except Exception:
            logger.debug("No se pudo fijar la prioridad relativa en MMCSS", exc_info=True)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        handle = self._handle
        avrt = self._avrt
        # Se limpia antes de revertir: una segunda salida (o una salida tras un
        # fallo) no vuelve a llamar a la API ni explota.
        self._handle = None
        self._task_index = 0
        self._avrt = None
        if handle is None or avrt is None:
            return
        try:
            avrt.AvRevertMmThreadCharacteristics(handle)
        except Exception:
            logger.debug("No se pudo revertir el registro MMCSS", exc_info=True)


def mmcss_pro_audio(
    task_name: str = DEFAULT_TASK_NAME,
    priority: int = AVRT_PRIORITY_CRITICAL,
) -> ProAudioThread:
    """Azucar: ``ProAudioThread(...)`` listo para usar con ``with``."""
    return ProAudioThread(task_name, priority)


def elevate_thread_priority(priority: int = THREAD_PRIORITY_ABOVE_NORMAL) -> bool:
    """Sube la prioridad del hilo ACTUAL con ``SetThreadPriority`` (1 = ABOVE_NORMAL).

    Devuelve True si se aplico. No lanza nunca. Mantiene la misma semantica que
    ``AudioEngine._set_thread_priority``: OpenThread con THREAD_SET_INFORMATION,
    SetThreadPriority y CloseHandle del handle obtenido.
    """
    try:
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            logger.debug("ctypes.windll no existe en esta plataforma: sin prioridad de hilo")
            return False
        kernel32 = getattr(windll, "kernel32", None)
        if kernel32 is None:  # pragma: no cover - defensivo
            return False
        handle = kernel32.OpenThread(THREAD_SET_INFORMATION, False, threading.get_ident())
        if not handle:
            logger.debug("OpenThread no devolvio handle: prioridad de hilo sin cambios")
            return False
        try:
            return bool(kernel32.SetThreadPriority(handle, priority))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        logger.debug("No se pudo subir la prioridad del hilo actual", exc_info=True)
        return False


def describe() -> dict[str, object]:
    """Informe minimo del soporte de prioridad de tiempo real (diagnostico)."""
    avrt = _load_avrt()
    return {
        "platform": os.name,
        "mmcss": os.name == "nt" and avrt is not None,
        "avrt_loaded": avrt is not None,
    }
