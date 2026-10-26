"""Tests de ``audio_enhancer.rt_priority``: registro del hilo en MMCSS ("Pro
Audio") y subida de prioridad del hilo actual.

``avrt.dll`` y ``kernel32`` se simulan siempre con dobles, para no depender del
sistema real: el CI tambien corre esta suite en Linux, donde ninguna de las dos
APIs existe (el modulo debe degradar en silencio y no lanzar).
"""

from __future__ import annotations

import ctypes
import os
from types import SimpleNamespace

from audio_enhancer import rt_priority


def _fake_win_dll(avrt):
    """Sustituto de ``ctypes.WinDLL`` que devuelve la ``avrt`` simulada."""

    def _win_dll(path, *args, **kwargs):
        assert path == "avrt"
        return avrt

    return _win_dll


def _dword_apuntado(ptr: object) -> int:
    """Lee el DWORD apuntado por ``ptr`` (``byref()`` guarda el objeto en ``_obj``)."""
    obj = getattr(ptr, "_obj", ptr)
    return int(getattr(obj, "value", 0))


class FakeAvrt:
    """Doble de ``avrt.dll`` que cuenta llamadas a la API de MMCSS."""

    def __init__(self, handle: int = 1) -> None:
        self.handle = handle
        self.revert_calls = 0
        self.priority_calls: list[tuple[int, int]] = []
        self.task_name = ""
        self.task_index = 0

        def set_characteristics(name: str, index_ptr: object) -> int:
            self.task_name = str(name)
            self.task_index = _dword_apuntado(index_ptr)
            return self.handle

        def set_priority(hnd: int, priority: int) -> int:
            self.priority_calls.append((hnd, priority))
            return 1

        def revert(hnd: int) -> int:
            self.revert_calls += 1
            return 1

        # Funciones reales (no metodos ligados): la implementacion les asigna
        # ``argtypes``/``restype`` igual que a las de ctypes y los objetos
        # funcion aceptan atributos sin problema.
        self.AvSetMmThreadCharacteristicsW = set_characteristics
        self.AvSetMmThreadPriority = set_priority
        self.AvRevertMmThreadCharacteristics = revert


class FakeKernel32:
    """Doble de ``kernel32`` para ``elevate_thread_priority``."""

    def __init__(self, handle: int = 1) -> None:
        self.handle = handle
        self.priority_calls: list[tuple[int, int]] = []
        self.closed = 0
        self.open_args: tuple[int, bool, int] | None = None

    def OpenThread(self, access: int, inherit: bool, thread_id: int) -> int:
        self.open_args = (access, inherit, thread_id)
        return self.handle

    def SetThreadPriority(self, handle: int, priority: int) -> int:
        self.priority_calls.append((handle, priority))
        return 1

    def CloseHandle(self, handle: int) -> int:
        self.closed += 1
        return 1


def test_describe_devuelve_las_tres_claves():
    info = rt_priority.describe()

    assert isinstance(info, dict)
    assert set(info) == {"platform", "mmcss", "avrt_loaded"}
    assert info["platform"] == os.name
    assert isinstance(info["mmcss"], bool)
    assert isinstance(info["avrt_loaded"], bool)


def test_context_manager_real_no_lanza_y_expone_las_propiedades():
    hilo = rt_priority.ProAudioThread()

    with hilo as activo:
        assert activo is hilo
        assert isinstance(hilo.registered, bool)
        assert isinstance(hilo.task_index, int)

    # Al salir el registro queda revertido (en Linux nunca llego a existir).
    assert hilo.registered is False


def test_registra_y_revierte_con_avrt_simulada(monkeypatch):
    fake = FakeAvrt(handle=1)
    monkeypatch.setattr(ctypes, "WinDLL", _fake_win_dll(fake), raising=False)

    hilo = rt_priority.ProAudioThread("Pro Audio")
    with hilo:
        assert hilo.registered is True
        assert isinstance(hilo.task_index, int)
        assert fake.task_name == "Pro Audio"

    assert fake.priority_calls == [(1, rt_priority.AVRT_PRIORITY_CRITICAL)]
    assert fake.revert_calls == 1
    assert hilo.registered is False


def test_handle_invalido_no_registra_ni_revierte(monkeypatch):
    fake = FakeAvrt(handle=0)
    monkeypatch.setattr(ctypes, "WinDLL", _fake_win_dll(fake), raising=False)

    with rt_priority.ProAudioThread() as hilo:
        assert hilo.registered is False
        assert isinstance(hilo.task_index, int)

    assert fake.revert_calls == 0
    assert fake.priority_calls == []


def test_load_avrt_que_falla_degrada_en_silencio(monkeypatch):
    def _win_dll_rota(path, *args, **kwargs):
        raise OSError("avrt.dll no disponible")

    monkeypatch.setattr(ctypes, "WinDLL", _win_dll_rota, raising=False)

    assert rt_priority.mmcss_available() is False
    with rt_priority.ProAudioThread() as hilo:
        assert hilo.registered is False


def test_mmcss_pro_audio_devuelve_instancia():
    hilo = rt_priority.mmcss_pro_audio()

    assert isinstance(hilo, rt_priority.ProAudioThread)
    assert isinstance(hilo.registered, bool)


def test_elevate_thread_priority_devuelve_bool(monkeypatch):
    k32 = FakeKernel32()
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=k32), raising=False)

    resultado = rt_priority.elevate_thread_priority()

    assert isinstance(resultado, bool)
    assert resultado is True
    assert k32.priority_calls == [(1, 1)]  # THREAD_PRIORITY_ABOVE_NORMAL
    assert k32.closed == 1


def test_elevate_thread_priority_sin_handle_devuelve_false(monkeypatch):
    k32 = FakeKernel32(handle=0)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=k32), raising=False)

    assert rt_priority.elevate_thread_priority() is False
    assert k32.priority_calls == []
    assert k32.closed == 0


def test_elevate_thread_priority_sin_windll_devuelve_false(monkeypatch):
    monkeypatch.delattr(ctypes, "windll", raising=False)

    assert rt_priority.elevate_thread_priority() is False


def test_enter_y_exit_son_idempotentes(monkeypatch):
    fake = FakeAvrt(handle=1)
    monkeypatch.setattr(ctypes, "WinDLL", _fake_win_dll(fake), raising=False)

    hilo = rt_priority.ProAudioThread()
    hilo.__exit__(None, None, None)  # salir sin haber entrado: no hace nada
    assert fake.revert_calls == 0

    hilo.__enter__()
    hilo.__enter__()  # segundo enter: no vuelve a registrar
    assert hilo.registered is True

    hilo.__exit__(None, None, None)
    hilo.__exit__(None, None, None)  # doble salida: revierte una sola vez
    assert fake.revert_calls == 1
    assert hilo.registered is False
