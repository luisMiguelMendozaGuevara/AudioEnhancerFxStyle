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

Cubre desde el sondeo de disponibilidad hasta la captura real: activación
asíncrona con el ``IActivateAudioInterfaceCompletionHandler`` implementado en
ctypes (vtable a mano, sin ``comtypes``), inicialización en modo compartido con
loopback y lectura de paquetes desde ``IAudioCaptureClient``.

Nota de diseño: todo es ``ctypes`` y sin dependencias nuevas. ``comtypes`` no
está en el entorno y añadirlo por una PoC no se justifica; las vtable de COM se
llaman por índice, que es lo único que hace falta.

Uso:
    python -m audio_enhancer.process_loopback --probe
    python -m audio_enhancer.process_loopback --pid 1234 --segundos 3
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

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
#: ``IAgileObject``: marca de objeto *free-threaded*. Windows pregunta por ella
#: al handler de finalización; si no se implementa, intenta marshalling entre
#: apartamentos y la activación termina en ``E_ILLEGAL_METHOD_CALL``
#: (0x8000000E). Medido: con el handler "no ágil" la activación nunca llega a
#: ``ActivateCompleted``.
IID_IAgileObject = guid_de_texto("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")


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


# --------------------------------------------------------------------------- #
# Activación asíncrona y captura
# --------------------------------------------------------------------------- #
#: ``HRESULT`` es un ``long`` de 32 bits con signo; ``REFERENCE_TIME`` un
#: ``long long`` en unidades de 100 ns.
HRESULT = ctypes.c_long
REFERENCE_TIME = ctypes.c_longlong


def _vtbl(ptr: int | None, indice: int, restype, argtypes, *args):  # noqa: ANN001, ANN401
    """Llama al método ``indice`` de la vtable COM de ``ptr``.

    Las interfaces de COM son un puntero a un array de punteros a función: el
    primer hueco es ``QueryInterface``, el segundo ``AddRef`` y el tercero
    ``Release``. Se llama por índice porque declarar las 15 entradas de
    ``IAudioClient`` en ctypes no aporta nada aquí.
    """
    if not ptr:
        raise ProcessLoopbackError("puntero COM nulo")
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    if not vtable:
        raise ProcessLoopbackError("vtable COM nula")
    funcion = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[indice])
    return funcion(ptr, *args)


def _release(ptr: int | None) -> None:
    """Suelta una referencia COM (nunca lanza: es limpieza)."""
    if not ptr:
        return
    with contextlib.suppress(Exception):  # limpieza best-effort
        _vtbl(ptr, 2, ctypes.c_ulong, [])


#: Índices de vtable (IUnknown ocupa 0-2).
_IAUDIOCLIENT_INITIALIZE = 3
_IAUDIOCLIENT_GET_BUFFER_SIZE = 4
_IAUDIOCLIENT_GET_MIX_FORMAT = 8
_IAUDIOCLIENT_START = 10
_IAUDIOCLIENT_STOP = 11
_IAUDIOCLIENT_GET_SERVICE = 14
_IAUDIOCAPTURECLIENT_GET_BUFFER = 3
_IAUDIOCAPTURECLIENT_RELEASE_BUFFER = 4
_IAUDIOCAPTURECLIENT_NEXT_PACKET = 5
_IACTIVATEOPERATION_GET_RESULT = 3

#: Banderas de ``IAudioCaptureClient::GetBuffer``.
AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY = 0x1  # se perdió audio antes del paquete
AUDCLNT_BUFFERFLAGS_SILENT = 0x2  # el paquete es silencio (no se lee memoria)


class _ComObjeto(ctypes.Structure):
    """Objeto COM mínimo: solo el puntero a la vtable."""

    _fields_ = (("lpVtbl", ctypes.POINTER(ctypes.c_void_p)),)


#: Handlers vivos por dirección del objeto COM. Los callbacks son funciones C
#: sin contexto, así que el ``this`` es lo único que permiten identificar quién
#: es quién.
_HANDLERS: dict[int, _CompletionHandler] = {}

_QUERYINTERFACE_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
_REFCOUNT_FN = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_ACTIVATE_COMPLETED_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p)


def _cb_query_interface(this, iid, salida) -> int:  # noqa: ANN001
    """``IUnknown::QueryInterface`` del handler de finalización."""
    handler = _HANDLERS.get(int(this) if this else 0)
    if handler is None:
        return _E_NOINTERFACE
    if not iid:
        return _E_INVALIDARG
    pedido = str(iid.contents)
    # IAgileObject se acepta aunque no haya nada que implementar: es una marca
    # que le dice a COM que el objeto se puede usar desde cualquier apartamento.
    if pedido not in (
        str(IID_IUnknown),
        str(IID_IActivateAudioInterfaceCompletionHandler),
        str(IID_IAgileObject),
    ):
        salida[0] = None
        return _E_NOINTERFACE
    handler.refs += 1
    salida[0] = ctypes.c_void_p(this)
    return 0


def _cb_add_ref(this) -> int:  # noqa: ANN001
    handler = _HANDLERS.get(int(this) if this else 0)
    if handler is None:
        return 0
    handler.refs += 1
    return handler.refs


def _cb_release(this) -> int:  # noqa: ANN001
    handler = _HANDLERS.get(int(this) if this else 0)
    if handler is None:
        return 0
    handler.refs -= 1
    if handler.refs <= 0:
        _HANDLERS.pop(int(this), None)
        handler.evento.set()  # nadie nos va a llamar ya: no dejar colgado
    return max(handler.refs, 0)


def _cb_activate_completed(this, operacion) -> int:  # noqa: ANN001
    """``IActivateAudioInterfaceCompletionHandler::ActivateCompleted``.

    Windows llama aquí desde un hilo del pool de COM. Solo se guarda la
    operación y se avisa al hilo que espera: el ``GetActivateResult`` se hace
    fuera, que es donde se puede informar del error con su ``HRESULT``.
    """
    handler = _HANDLERS.get(int(this) if this else 0)
    if handler is None:
        return _E_INVALIDARG
    handler.operacion = int(operacion) if operacion else 0
    handler.evento.set()
    return 0


# Las referencias a los callbacks tienen que vivir todo el proceso: si se
# recolectan, Windows salta a memoria liberada.
_CALLBACKS = (
    _QUERYINTERFACE_FN(_cb_query_interface),
    _REFCOUNT_FN(_cb_add_ref),
    _REFCOUNT_FN(_cb_release),
    _ACTIVATE_COMPLETED_FN(_cb_activate_completed),
)


class _CompletionHandler:
    """Implementación en ctypes de ``IActivateAudioInterfaceCompletionHandler``."""

    def __init__(self) -> None:
        self.refs = 1
        self.evento = threading.Event()
        self.operacion = 0
        vtable = (ctypes.c_void_p * len(_CALLBACKS))(*[ctypes.cast(cb, ctypes.c_void_p) for cb in _CALLBACKS])
        self._vtable = vtable  # hay que mantenerla viva
        self.objeto = _ComObjeto(lpVtbl=ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p)))
        self.ptr = ctypes.cast(ctypes.byref(self.objeto), ctypes.c_void_p)
        _HANDLERS[int(self.ptr.value or 0)] = self

    def liberar(self) -> None:
        """Suelta la referencia inicial y limpia el registro."""
        _HANDLERS.pop(int(self.ptr.value or 0), None)


def _asegurar_com_mta() -> None:
    """``CoInitializeEx`` en modo multihilo, tolerando que ya estuviera.

    ``ActivateAudioInterfaceAsync`` exige que el hilo llamador esté en un
    apartamento multihilo; repetir la llamada devuelve ``S_FALSE`` (1) o
    ``RPC_E_CHANGED_MODE``, ninguno de los cuales es un error aquí.
    """
    try:
        ole32 = ctypes.WinDLL("ole32")
    except OSError as exc:  # pragma: no cover - Windows sin ole32
        raise ProcessLoopbackError(f"no se pudo cargar ole32: {exc}") from exc
    ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED


def _funcion_activacion():
    """Devuelve ``ActivateAudioInterfaceAsync`` tipada de ``mmdevapi.dll``."""
    try:
        mmdevapi = ctypes.WinDLL("mmdevapi")
    except OSError as exc:
        raise ProcessLoopbackError(f"no se pudo cargar mmdevapi.dll: {exc}") from exc
    try:
        prototipo = ctypes.WINFUNCTYPE(
            HRESULT,
            wt.LPCWSTR,
            ctypes.POINTER(GUID),
            ctypes.POINTER(PROPVARIANT),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        return prototipo(("ActivateAudioInterfaceAsync", mmdevapi))
    except AttributeError as exc:
        raise ProcessLoopbackError("mmdevapi.dll no exporta ActivateAudioInterfaceAsync") from exc


@dataclass
class Resultado:
    """Lo capturado de un proceso: muestras, formato y contadores."""

    datos: Any  # numpy.ndarray (frames, canales)
    formato: WAVEFORMATEX
    paquetes: int = 0
    frames_silenciosos: int = 0
    frames_descartados: int = 0

    def resumen(self) -> str:
        """Línea de diagnóstico con lo que de verdad importa."""
        canales = self.datos.shape[1] if getattr(self.datos, "ndim", 0) == 2 else 1
        frames = self.datos.shape[0] if getattr(self.datos, "ndim", 0) else 0
        return (
            f"{frames} frames / {canales} canales @ {self.formato.nSamplesPerSec} Hz; "
            f"paquetes={self.paquetes} silencio={self.frames_silenciosos} "
            f"descartados={self.frames_descartados}"
        )


def activar(pid: int, timeout: float = 5.0) -> int:
    """Obtiene el ``IAudioClient`` del proceso ``pid`` (activación asíncrona).

    Devuelve el puntero a la interfaz; el llamador es responsable de soltarlo
    con ``_release``. Un ``timeout`` evita quedarse colgado si Windows nunca
    llama a ``ActivateCompleted`` (por ejemplo con un proceso protegido).
    """
    _asegurar_com_mta()
    params = parametros_activacion(pid)
    variante, buffer = propvariant_blob(params)  # noqa: F841 - el blob debe vivir
    handler = _CompletionHandler()
    operacion = ctypes.c_void_p()
    funcion = _funcion_activacion()
    hr = funcion(
        VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
        ctypes.byref(IID_IAudioClient),
        ctypes.byref(variante),
        handler.ptr,
        ctypes.byref(operacion),
    )
    if hr < 0:
        handler.liberar()
        raise ProcessLoopbackError("ActivateAudioInterfaceAsync fallo", hr)
    try:
        if not handler.evento.wait(timeout):
            raise ProcessLoopbackError(
                f"la activacion del PID {pid} no respondio en {timeout:.0f} s (proceso protegido o sin audio?)"
            )
        resultado = HRESULT(0)
        cliente = ctypes.c_void_p()
        hr = _vtbl(
            operacion.value,
            _IACTIVATEOPERATION_GET_RESULT,
            HRESULT,
            [ctypes.POINTER(HRESULT), ctypes.POINTER(ctypes.c_void_p)],
            ctypes.byref(resultado),
            ctypes.byref(cliente),
        )
        if hr < 0:
            raise ProcessLoopbackError("no se pudo leer el resultado de la activacion", hr)
        if resultado.value < 0:
            raise ProcessLoopbackError(f"la activacion del PID {pid} fue rechazada", resultado.value)
        if not cliente.value:
            raise ProcessLoopbackError("la activacion no devolvio IAudioClient")
        return cliente.value
    finally:
        _release(operacion.value)
        handler.liberar()


def preparar_cliente(cliente: int, tasa: int = 48000, canales: int = 2) -> tuple[int, WAVEFORMATEX]:
    """Inicializa ``IAudioClient`` en compartido con loopback y pide la captura.

    Se pide float32 estéreo a la tasa indicada; si el motor no lo acepta se
    reintenta con su formato de mezcla (``GetMixFormat``), porque en loopback el
    formato lo impone el endpoint y no hay negociación posible.
    """
    formato = formato_float32(tasa, canales)
    hr = _inicializar(cliente, formato)
    if hr < 0:
        mezcla = _mix_format(cliente)
        if mezcla is None:
            raise ProcessLoopbackError("Initialize del cliente de proceso fallo", hr)
        formato = mezcla
        hr = _inicializar(cliente, formato)
        if hr < 0:
            raise ProcessLoopbackError("Initialize con el formato de mezcla tambien fallo", hr)
    captura = ctypes.c_void_p()
    hr = _vtbl(
        cliente,
        _IAUDIOCLIENT_GET_SERVICE,
        HRESULT,
        [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
        ctypes.byref(IID_IAudioCaptureClient),
        ctypes.byref(captura),
    )
    if hr < 0 or not captura.value:
        raise ProcessLoopbackError("no se pudo obtener IAudioCaptureClient", hr)
    return captura.value, formato


def _inicializar(cliente: int, formato: WAVEFORMATEX) -> int:
    """Llamada a ``IAudioClient::Initialize`` para loopback (200 ms de buffer)."""
    return int(
        _vtbl(
            cliente,
            _IAUDIOCLIENT_INITIALIZE,
            HRESULT,
            [
                ctypes.c_int,  # AUDCLNT_SHAREMODE ShareMode
                wt.DWORD,  # DWORD StreamFlags
                REFERENCE_TIME,  # hnsBufferDuration
                REFERENCE_TIME,  # hnsPeriodicity
                ctypes.POINTER(WAVEFORMATEX),
                ctypes.c_void_p,  # LPCGUID AudioSessionGuid
            ],
            AUDCLNT_SHAREMODE_SHARED,
            AUDCLNT_STREAMFLAGS_LOOPBACK,
            REFERENCE_TIME(2 * HNS_PER_SECOND // 10),  # 200 ms
            REFERENCE_TIME(0),
            ctypes.byref(formato),
            None,
        )
    )


def _mix_format(cliente: int) -> WAVEFORMATEX | None:
    """``GetMixFormat``: el formato que el motor sí acepta. ``None`` si falla."""
    puntero = ctypes.POINTER(WAVEFORMATEX)()
    hr = _vtbl(
        cliente,
        _IAUDIOCLIENT_GET_MIX_FORMAT,
        HRESULT,
        [ctypes.POINTER(ctypes.POINTER(WAVEFORMATEX))],
        ctypes.byref(puntero),
    )
    if hr < 0 or not puntero:
        return None
    return puntero.contents


def _tamano_buffer(cliente: int) -> int:
    """``GetBufferSize``: frames que caben en el buffer compartido."""
    tamano = ctypes.c_uint32(0)
    _vtbl(
        cliente,
        _IAUDIOCLIENT_GET_BUFFER_SIZE,
        HRESULT,
        [ctypes.POINTER(ctypes.c_uint32)],
        ctypes.byref(tamano),
    )
    return int(tamano.value)


def capturar(
    pid: int,
    segundos: float = 3.0,
    tasa: int = 48000,
    canales: int = 2,
    timeout_activacion: float = 5.0,
) -> Resultado:
    """Captura ``segundos`` de audio del proceso ``pid``.

    Es una PoC: sondea ``GetNextPacketSize`` en vez de usar ``SetEventHandle``
    (un evento por paquete obligaría a un bucle con ``WaitForSingleObject`` y no
    cambia lo que se quiere comprobar aquí). Si el proceso deja de sonar, la
    captura devuelve menos frames de los pedidos; no es un error.
    """
    import numpy as np  # local: el módulo también se usa sin audio

    if segundos <= 0:
        raise ValueError("segundos debe ser > 0")
    cliente = activar(pid, timeout_activacion)
    captura = 0
    try:
        captura, formato = preparar_cliente(cliente, tasa, canales)
        canales = int(formato.nChannels)
        objetivo = int(segundos * formato.nSamplesPerSec)
        trozos: list[np.ndarray] = []
        paquetes = silenciosos = descartados = 0
        obtenidos = 0
        hr = _vtbl(cliente, _IAUDIOCLIENT_START, HRESULT, [])
        if hr < 0:
            raise ProcessLoopbackError("Start del cliente de proceso fallo", hr)
        try:
            while obtenidos < objetivo:
                siguiente = ctypes.c_uint32(0)
                _vtbl(
                    captura,
                    _IAUDIOCAPTURECLIENT_NEXT_PACKET,
                    HRESULT,
                    [ctypes.POINTER(ctypes.c_uint32)],
                    ctypes.byref(siguiente),
                )
                if not siguiente.value:
                    time.sleep(0.005)
                    continue
                puntero_datos = ctypes.POINTER(ctypes.c_byte)()
                frames = ctypes.c_uint32(0)
                banderas = wt.DWORD(0)
                hr = _vtbl(
                    captura,
                    _IAUDIOCAPTURECLIENT_GET_BUFFER,
                    HRESULT,
                    [
                        ctypes.POINTER(ctypes.POINTER(ctypes.c_byte)),
                        ctypes.POINTER(ctypes.c_uint32),
                        ctypes.POINTER(wt.DWORD),
                        ctypes.POINTER(ctypes.c_ulonglong),
                        ctypes.POINTER(ctypes.c_ulonglong),
                    ],
                    ctypes.byref(puntero_datos),
                    ctypes.byref(frames),
                    ctypes.byref(banderas),
                    None,
                    None,
                )
                if hr < 0:
                    raise ProcessLoopbackError("GetBuffer fallo", hr)
                paquetes += 1
                cuantos = int(frames.value)
                if banderas.value & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY:
                    # Salto en el flujo (el proceso dejó de sonar o llegó tarde):
                    # se cuenta y se descarta en vez de pegarlo al audio bueno.
                    descartados += cuantos
                    _vtbl(
                        captura,
                        _IAUDIOCAPTURECLIENT_RELEASE_BUFFER,
                        HRESULT,
                        [ctypes.c_uint32],
                        ctypes.c_uint32(cuantos),
                    )
                    continue
                if banderas.value & AUDCLNT_BUFFERFLAGS_SILENT:
                    bloque = np.zeros((cuantos, canales), dtype=np.float32)
                    silenciosos += cuantos
                else:
                    crudo = np.ctypeslib.as_array(
                        ctypes.cast(puntero_datos, ctypes.POINTER(ctypes.c_float)), shape=(cuantos * canales,)
                    )
                    bloque = crudo.reshape(cuantos, canales).copy()
                _vtbl(
                    captura, _IAUDIOCAPTURECLIENT_RELEASE_BUFFER, HRESULT, [ctypes.c_uint32], ctypes.c_uint32(cuantos)
                )
                trozos.append(bloque)
                obtenidos += cuantos
        finally:
            _vtbl(cliente, _IAUDIOCLIENT_STOP, HRESULT, [])
    finally:
        _release(captura)
        _release(cliente)
    if not trozos:
        raise ProcessLoopbackError(f"el PID {pid} no entrego audio en {segundos:.1f} s")
    datos = np.concatenate(trozos)[:objetivo]
    return Resultado(
        datos=datos,
        formato=formato,
        paquetes=paquetes,
        frames_silenciosos=silenciosos,
        frames_descartados=descartados,
    )


def guardar_wav(ruta: str, resultado: Resultado) -> None:
    """Escribe lo capturado a WAV 16 bits (para escucharlo o analizarlo)."""
    import wave

    import numpy as np

    datos = np.clip(resultado.datos, -1.0, 1.0)
    entero = (datos * 32767.0).astype("<i2")
    with wave.open(ruta, "wb") as salida:
        salida.setnchannels(int(resultado.formato.nChannels))
        salida.setsampwidth(2)
        salida.setframerate(int(resultado.formato.nSamplesPerSec))
        salida.writeframes(entero.tobytes())


def _cli(argv: list[str]) -> int:
    """CLI de la PoC: sondeo o captura de un PID a consola/WAV."""
    import argparse

    parser = argparse.ArgumentParser(description="PoC de captura por aplicacion (Process Loopback)")
    parser.add_argument("--probe", action="store_true", help="solo comprobar si la API existe")
    parser.add_argument("--pid", type=int, default=0, help="PID del proceso a capturar")
    parser.add_argument("--segundos", type=float, default=3.0, help="duracion de la captura")
    parser.add_argument("--wav", default="", help="ruta donde guardar el WAV")
    args = parser.parse_args(argv)

    disp = sondeo()
    print(disp.resumen())
    for nota in disp.notas:
        print(f"  nota: {nota}")
    if not disp.disponible:
        return 1
    if args.probe or not args.pid:
        return 0
    try:
        resultado = capturar(args.pid, args.segundos)
    except (ProcessLoopbackError, ValueError) as exc:
        print(f"error: {exc}")
        return 2
    import numpy as np

    pico = float(np.max(np.abs(resultado.datos))) if resultado.datos.size else 0.0
    rms = float(np.sqrt(np.mean(resultado.datos**2))) if resultado.datos.size else 0.0
    print(resultado.resumen())
    print(f"pico={pico:.4f} ({20 * np.log10(max(pico, 1e-9)):+.1f} dBFS) rms={rms:.4f}")
    if args.wav:
        guardar_wav(args.wav, resultado)
        print(f"WAV: {args.wav}")
    return 0
