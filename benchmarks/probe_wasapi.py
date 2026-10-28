"""Sondeo del periodo de motor que concede WASAPI (medida, no estimación).

Responde a la incertidumbre "periodo de motor real por chipset": Windows concede
un periodo distinto según el driver y el chipset, y no hay tabla pública. Este
script mide el de la máquina donde se ejecuta y lo imprime, para poder comparar
entre equipos.

Mide dos cosas distintas, que conviene no confundir:

- ``GetDevicePeriod``: el periodo del *dispositivo* (heredado de la API antigua).
  El mínimo suele ser 10 ms/2-3 ms según driver.
- ``IAudioClient3::GetSharedModeEnginePeriod``: el periodo del *motor* en modo
  compartido, que es lo que de verdad se puede pedir con
  ``InitializeSharedAudioStream``. Su mínimo es el suelo de latencia alcanzable
  sin escribir un cliente exclusivo.

Uso:
    python benchmarks/probe_wasapi.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audio_enhancer import process_loopback as pl  # noqa: E402

HRESULT = ctypes.c_long
REFERENCE_TIME = ctypes.c_longlong
#: ``IAudioClient3`` (Windows 10 1803+): la interfaz que expone el periodo del motor.
IID_IAudioClient3 = pl.guid_de_texto("{7ED4EE07-8E67-4CD4-8C1A-2B7A5987AD42}")


def medir() -> dict[str, float | str]:
    """Devuelve los periodos medidos (en ms y en frames) del endpoint por defecto."""
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx(None, 0)
    enumerador = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        ctypes.byref(pl.CLSID_MMDeviceEnumerator),
        None,
        0x17,  # CLSCTX_ALL
        ctypes.byref(pl.IID_IMMDeviceEnumerator),
        ctypes.byref(enumerador),
    )
    if hr < 0 or not enumerador.value:
        raise SystemExit(f"no se pudo crear el enumerador (0x{hr & 0xFFFFFFFF:08X})")
    dispositivo = ctypes.c_void_p()
    pl._vtbl(
        enumerador.value,
        4,  # IMMDeviceEnumerator::GetDefaultAudioEndpoint
        HRESULT,
        [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
        0,  # eRender
        0,  # eConsole
        ctypes.byref(dispositivo),
    )
    cliente = ctypes.c_void_p()
    hr = pl._vtbl(
        dispositivo.value,
        3,  # IMMDevice::Activate
        HRESULT,
        [ctypes.POINTER(pl.GUID), wt.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
        ctypes.byref(IID_IAudioClient3),
        0x17,
        None,
        ctypes.byref(cliente),
    )
    resultado: dict[str, float | str] = {
        "iaudioclient3": "si" if hr >= 0 and cliente.value else f"no (0x{hr & 0xFFFFFFFF:08X})"
    }
    if not cliente.value:
        return resultado
    formato = ctypes.POINTER(pl.WAVEFORMATEX)()
    pl._vtbl(
        cliente.value,
        8,  # GetMixFormat
        HRESULT,
        [ctypes.POINTER(ctypes.POINTER(pl.WAVEFORMATEX))],
        ctypes.byref(formato),
    )
    wf = formato.contents
    tasa = int(wf.nSamplesPerSec)
    resultado["formato"] = wf.resumen()
    defecto, minimo = REFERENCE_TIME(0), REFERENCE_TIME(0)
    pl._vtbl(
        cliente.value,
        9,  # GetDevicePeriod
        HRESULT,
        [ctypes.POINTER(REFERENCE_TIME), ctypes.POINTER(REFERENCE_TIME)],
        ctypes.byref(defecto),
        ctypes.byref(minimo),
    )
    resultado["dispositivo_default_ms"] = defecto.value / 1e4
    resultado["dispositivo_min_ms"] = minimo.value / 1e4
    d, f, mn, mx = (ctypes.c_uint32(0) for _ in range(4))
    hr = pl._vtbl(
        cliente.value,
        18,  # IAudioClient3::GetSharedModeEnginePeriod
        HRESULT,
        [
            ctypes.POINTER(pl.WAVEFORMATEX),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
        ],
        ctypes.byref(wf),
        ctypes.byref(d),
        ctypes.byref(f),
        ctypes.byref(mn),
        ctypes.byref(mx),
    )
    if hr < 0:
        resultado["motor"] = f"no disponible (0x{hr & 0xFFFFFFFF:08X})"
    else:
        resultado["motor_default_ms"] = d.value / tasa * 1000
        resultado["motor_fundamental_ms"] = f.value / tasa * 1000
        resultado["motor_min_ms"] = mn.value / tasa * 1000
        resultado["motor_max_ms"] = mx.value / tasa * 1000
        resultado["motor_min_frames"] = int(mn.value)
    pl._release(cliente.value)
    pl._release(dispositivo.value)
    pl._release(enumerador.value)
    return resultado


if __name__ == "__main__":
    for clave, valor in medir().items():
        if isinstance(valor, float):
            print(f"  {clave:24s} {valor:6.2f} ms")
        else:
            print(f"  {clave:24s} {valor}")
