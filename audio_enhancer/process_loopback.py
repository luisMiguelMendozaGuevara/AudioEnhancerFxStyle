"""PoC de captura por aplicación (Process Loopback API, Windows 10 2004+).

El objetivo es dejar de depender de VB-CABLE: en vez de capturar el "cable
virtual" del sistema, pedirle a WASAPI el audio de *un proceso concreto* (o de su
árbol, p. ej. un navegador con sus procesos hijos).

Cómo funciona la API (documentación de Microsoft, no de este módulo):

1. ``ActivateAudioInterfaceAsync`` con la ruta del dispositivo virtual
   ``VAD\\Process_Loopback`` y un ``PROPVARIANT`` que lleva un
   ``AUDIOCLIENT_ACTIVATION_PARAMS`` con ``ActivationType = 1``
   (``AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK``) y el PID objetivo.
   A diferencia del resto de interfaces de audio, esta activación es
   **asíncrona**: se registra un ``IActivateAudioInterfaceCompletionHandler`` y
   se espera a que llegue el ``IAudioClient``.
2. Ese ``IAudioClient`` se inicializa en modo compartido con
   ``AUDCLNT_STREAMFLAGS_LOOPBACK`` y el formato que impone el motor, y de él se
   pide ``IAudioCaptureClient``, que entrega los frames del proceso.

Esta primera parte cubre lo verificable sin reproducir audio: las estructuras
(tamaño y desplazamiento comprobados contra la cabecera de Windows), la
conversión de GUIDs, el sondeo de disponibilidad y la resolución de PID. La
captura en sí llega después, porque su verificación depende de un proceso que
esté sonando de verdad.

Nota de diseño: todo es ``ctypes`` y sin dependencias nuevas. ``comtypes`` no
está en el entorno y añadirlo por una PoC no se justifica; las vtable de COM se
llaman por índice, que es lo único que hace falta.

Uso:
    python -m audio_enhancer.process_loopback --probe
    python -m audio_enhancer.process_loopback --pid 1234 --segundos 3
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - solo para tipado
    pass

# --------------------------------------------------------------------------- #
# Constantes (audioclientactivationparams.h, audioclient.h, mmdeviceapi.h)
# --------------------------------------------------------------------------- #

#: Tipo de activación: audio por defecto del sistema (0) o por proceso (1).
AUDIOCLIENT_ACTIVATION_TYPE_DEFAULT = 0
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1

#: Modo del Process Loopback: incluir el árbol del PID (0) o excluirlo (1).
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1

#: Ruta del dispositivo virtual de activación por proceso (no es un endpoint
#: real: no aparece en los listados de dispositivos).
VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"

#: Modo compartido de ``IAudioClient::Initialize``.
AUDCLNT_SHAREMODE_SHARED = 0

#: Banderas de stream.
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000

#: Referencia temporal de 100 ns usada por WASAPI.
HNS_PER_SECOND = 10_000_000

#: Tipos de PROPVARIANT que necesitamos.
VT_BLOB = 65

#: Códigos de error propios de WASAPI que conviene nombrar en los mensajes.
_AUDCLNT_E_DEVICE_INVALIDATED = 0x88890004
_AUDCLNT_E_UNSUPPORTED_FORMAT = 0x88890008
_AUDCLNT_E_EXCLUSIVE_MODE_NOT_ALLOWED = 0x8889000E
_E_NOTFOUND = 0x80070490
_E_INVALIDARG = 0x80070057

#: Códigos de Windows donde ni siquiera existe la API (antes de 2004).
_RPC_E_DISCONNECTED = 0x80010108
_E_NOINTERFACE = 0x80004002

#: WAVE_FORMAT_IEEE_FLOAT, el formato que entrega el motor en modo compartido.
WAVE_FORMAT_IEEE_FLOAT = 0x0003


# --------------------------------------------------------------------------- #
# Estructuras
# --------------------------------------------------------------------------- #
class GUID(ctypes.Structure):
    """GUID de COM (16 bytes: Data1 u32, Data2/3 u16, Data4 8 bytes)."""

    _fields_ = (
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    )

    def __str__(self) -> str:
        d4 = "".join(f"{b:02x}" for b in self.Data4)
        return f"{{{self.Data1:08x}-{self.Data2:04x}-{self.Data3:04x}-{d4[:4]}-{d4[4:]}}}"


def guid_de_texto(texto: str) -> GUID:
    """Convierte ``{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}`` en un ``GUID``.

    Acepta el texto con o sin llaves y con cualquier capitalización; lanza
    ``ValueError`` si no son 32 dígitos hexadecimales (evita GUIDs a medio
    escribir que fallarían con un error opaco de COM).
    """
    limpio = texto.strip().strip("{}").replace("-", "")
    if len(limpio) != 32:
        raise ValueError(f"GUID con longitud incorrecta: {texto!r}")
    try:
        crudo = bytes.fromhex(limpio)
    except ValueError as exc:
        raise ValueError(f"GUID con caracteres no hexadecimales: {texto!r}") from exc
    # El texto del GUID es la representación "numérica" (Data1 en orden normal);
    # es ctypes quien lo deja en little-endian en memoria al pasarlo a Windows.
    d1 = int.from_bytes(crudo[0:4], "big")
    d2 = int.from_bytes(crudo[4:6], "big")
    d3 = int.from_bytes(crudo[6:8], "big")
    d4 = (ctypes.c_ubyte * 8)(*crudo[8:16])
    return GUID(d1, d2, d3, d4)


class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    """``AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS``: PID objetivo y modo.

    La cabecera de Windows lo declara como ``{ DWORD TargetProcessId;
    PROCESS_LOOPBACK_MODE ProcessLoopbackMode; }``, ambos de 4 bytes → 8.
    """

    _fields_ = (
        ("TargetProcessId", wt.DWORD),
        ("ProcessLoopbackMode", ctypes.c_int),
    )


class _PROCESS_LOOPBACK_UNION(ctypes.Union):
    # Anónima: así ``params.TargetProcessId`` escribe de verdad en la unión y no
    # en un atributo nuevo del objeto (ctypes deja crear atributos sueltos y el
    # valor se perdería sin avisar).
    _anonymous_ = ("ProcessLoopbackParams",)
    _fields_ = (("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS),)


class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    """``AUDIOCLIENT_ACTIVATION_PARAMS``: tipo de activación + parámetros.

    ``{ AUDIOCLIENT_ACTIVATION_TYPE ActivationType; union {
    AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS ProcessLoopbackParams; }; }``
    → 4 bytes de tipo + 8 de la unión = 12, con alineación 4.
    """

    _anonymous_ = ("u",)
    _fields_ = (
        ("ActivationType", ctypes.c_int),
        ("u", _PROCESS_LOOPBACK_UNION),
    )


def parametros_activacion(
    pid: int,
    modo: int = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE,
) -> AUDIOCLIENT_ACTIVATION_PARAMS:
    """Construye y rellena el ``AUDIOCLIENT_ACTIVATION_PARAMS`` de un PID.

    Un PID 0 no incluye nada (Windows lo ignora), así que se rechaza aquí en vez
    de dejar que la activación falle con un ``E_INVALIDARG`` genérico.
    """
    if pid <= 0:
        raise ValueError(f"PID invalido: {pid}")
    if modo not in (
        PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE,
        PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE,
    ):
        raise ValueError(f"modo de loopback invalido: {modo}")
    params = AUDIOCLIENT_ACTIVATION_PARAMS()
    params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
    params.TargetProcessId = int(pid)
    params.ProcessLoopbackMode = int(modo)
    return params


class BLOB(ctypes.Structure):
    """``BLOB`` de ``PROPVARIANT``: tamaño + puntero a los datos."""

    _fields_ = (
        ("cbSize", wt.ULONG),
        ("pBlobData", ctypes.POINTER(ctypes.c_byte)),
    )


class _PROPVARIANT_UNION(ctypes.Union):
    # La unión real de PROPVARIANT son 8 bytes en x64; el blob entra holgado.
    _fields_ = (
        ("blob", BLOB),
        ("_relleno", ctypes.c_byte * 16),
    )


class PROPVARIANT(ctypes.Structure):
    """Subconjunto de ``PROPVARIANT`` suficiente para ``VT_BLOB``.

    La cabecera son ``vt`` + tres ``SHORT`` reservados (8 bytes) y la unión va
    alineada a 8; con el caso ``VT_BLOB`` (``ULONG cbSize`` + puntero = 16 en
    x64) el total es 24 bytes. Son los números que se comprueban en los tests.
    """

    _anonymous_ = ("u",)
    _fields_ = (
        ("vt", wt.WORD),
        ("wReserved1", wt.WORD),
        ("wReserved2", wt.WORD),
        ("wReserved3", wt.WORD),
        ("u", _PROPVARIANT_UNION),
    )


class WAVEFORMATEX(ctypes.Structure):
    """``WAVEFORMATEX``: formato de audio de WASAPI (18 bytes empaquetados)."""

    _pack_ = 1
    _fields_ = (
        ("wFormatTag", wt.WORD),
        ("nChannels", wt.WORD),
        ("nSamplesPerSec", wt.DWORD),
        ("nAvgBytesPerSec", wt.DWORD),
        ("nBlockAlign", wt.WORD),
        ("wBitsPerSample", wt.WORD),
        ("cbSize", wt.WORD),
    )

    def resumen(self) -> str:
        """Descripción corta para logs (formato, canales, tasa, bits)."""
        return f"tag={self.wFormatTag} ch={self.nChannels} {self.nSamplesPerSec} Hz {self.wBitsPerSample} bits"


def formato_float32(tasa: int = 48000, canales: int = 2) -> WAVEFORMATEX:
    """``WAVEFORMATEX`` de float32 con ``cbSize=0`` (el que pide WASAPI)."""
    bloque = canales * 4  # 4 bytes por muestra float32
    return WAVEFORMATEX(
        wFormatTag=WAVE_FORMAT_IEEE_FLOAT,
        nChannels=canales,
        nSamplesPerSec=tasa,
        nAvgBytesPerSec=tasa * bloque,
        nBlockAlign=bloque,
        wBitsPerSample=32,
        cbSize=0,
    )


def propvariant_blob(datos: ctypes.Structure) -> tuple[PROPVARIANT, ctypes.Array]:
    """``PROPVARIANT`` con ``vt=VT_BLOB`` apuntando a ``datos``.

    Devuelve también el buffer intermedio: el ``PROPVARIANT`` guarda un puntero,
    así que si ese buffer se recolecta la activación leería basura. El llamador
    tiene que mantener viva la tupla hasta que termine la llamada.
    """
    tamano = ctypes.sizeof(datos)
    buffer = ctypes.create_string_buffer(bytes(datos), tamano)
    variante = PROPVARIANT()
    variante.vt = VT_BLOB
    variante.blob.cbSize = tamano
    variante.blob.pBlobData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    return variante, buffer


# --------------------------------------------------------------------------- #
# Interfaces (solo los IID que hacen falta)
# --------------------------------------------------------------------------- #
#: ``IAudioClient``: el cliente de audio que se inicializa y entrega la captura.
IID_IAudioClient = guid_de_texto("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
#: ``IAudioCaptureClient``: entrega los frames capturados.
IID_IAudioCaptureClient = guid_de_texto("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
#: ``IActivateAudioInterfaceCompletionHandler``: callback de la activación.
IID_IActivateAudioInterfaceCompletionHandler = guid_de_texto("{41D949AB-9862-444a-80F6-C261334DA5EB}")
#: ``IUnknown``: para ``QueryInterface`` del handler y del objeto de activación.
IID_IUnknown = guid_de_texto("{00000000-0000-0000-C000-000000000046}")
#: ``IActivateAudioInterfaceAsyncOperation``: resultado de la activación.
IID_IActivateAudioInterfaceAsyncOperation = guid_de_texto("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")


# --------------------------------------------------------------------------- #
# Errores
# --------------------------------------------------------------------------- #
class ProcessLoopbackError(RuntimeError):
    """Fallo de la API de audio por proceso, con el ``HRESULT`` a la vista."""

    def __init__(self, mensaje: str, hresult: int | None = None) -> None:
        self.hresult = hresult
        if hresult is not None:
            mensaje = f"{mensaje} (HRESULT 0x{hresult & 0xFFFFFFFF:08X}: {significado(hresult)})"
        super().__init__(mensaje)


def significado(hresult: int) -> str:
    """Traduce los ``HRESULT`` que de verdad aparecen en esta API.

    Los códigos salen de la documentación de WASAPI y de ``winerror.h``; el
    resto se devuelve como "código desconocido" en vez de inventar una causa.
    """
    h = hresult & 0xFFFFFFFF
    tabla = {
        _E_INVALIDARG: "argumento invalido",
        _E_NOTFOUND: "proceso o dispositivo no encontrado",
        _E_NOINTERFACE: "la interfaz no existe en este Windows",
        _RPC_E_DISCONNECTED: "el objeto COM se desconecto",
        _AUDCLNT_E_DEVICE_INVALIDATED: "el endpoint de audio se invalido",
        _AUDCLNT_E_UNSUPPORTED_FORMAT: "formato no soportado por el motor",
        _AUDCLNT_E_EXCLUSIVE_MODE_NOT_ALLOWED: "modo exclusivo no permitido",
    }
    return tabla.get(h, "codigo desconocido")


# --------------------------------------------------------------------------- #
# Sondeo (sin capturar nada)
# --------------------------------------------------------------------------- #
@dataclass
class Disponibilidad:
    """Resultado del sondeo: ¿puede esta máquina hacer Process Loopback?"""

    windows_ok: bool
    build: int
    mmdevapi: bool
    funcion_presente: bool
    motivo: str = ""
    notas: list[str] = field(default_factory=list)

    @property
    def disponible(self) -> bool:
        """True solo si todo lo necesario está presente."""
        return self.windows_ok and self.mmdevapi and self.funcion_presente

    def resumen(self) -> str:
        """Una línea para el log o el informe de diagnóstico."""
        estado = "disponible" if self.disponible else f"no disponible ({self.motivo})"
        return f"Process Loopback {estado}; build={self.build} mmdevapi={self.mmdevapi}"


def sondeo() -> Disponibilidad:
    """Comprueba si existe ``ActivateAudioInterfaceAsync`` en esta máquina.

    Process Loopback necesita Windows 10 2004 (build 19041) o superior: la
    función no existe antes, así que no se intenta siquiera activar (fallaría
    con un error opaco de "procedimiento no encontrado").
    """
    if sys.platform != "win32":  # pragma: no cover - el proyecto es Windows
        return Disponibilidad(False, 0, False, False, "no es Windows")

    version = sys.getwindowsversion()
    build = int(getattr(version, "build", 0))
    notas: list[str] = []
    mmdevapi = True
    funcion = True
    motivo = ""
    try:
        dll = ctypes.WinDLL("mmdevapi.dll")
    except OSError as exc:
        mmdevapi = False
        funcion = False
        motivo = f"no se pudo cargar mmdevapi.dll: {exc}"
        dll = None
    if dll is not None:
        if not hasattr(dll, "ActivateAudioInterfaceAsync"):
            funcion = False
            motivo = "mmdevapi.dll no exporta ActivateAudioInterfaceAsync"
        if build and build < 19041:
            # El build es la referencia documentada por Microsoft; se informa
            # aunque la función exista, porque el comportamiento no está probado.
            notas.append(f"build {build} < 19041 (2004): soporte no documentado")
    return Disponibilidad(
        windows_ok=sys.platform == "win32",
        build=build,
        mmdevapi=mmdevapi,
        funcion_presente=funcion,
        motivo=motivo,
        notas=notas,
    )


def _probe_argv(argv: list[str]) -> int:
    """CLI de la PoC: ``--probe`` informa; ``--pid`` aún no captura."""
    disp = sondeo()
    print(disp.resumen())
    for nota in disp.notas:
        print(f"  nota: {nota}")
    if not disp.disponible:
        return 1
    if "--pid" in argv:
        print("captura por proceso: pendiente (esta entrega es solo el sondeo)")
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - entrada manual
    raise SystemExit(_probe_argv(sys.argv[1:]))
