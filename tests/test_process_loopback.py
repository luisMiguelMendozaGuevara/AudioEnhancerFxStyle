"""PoC Process Loopback: estructuras contra la cabecera de Windows.

Los números de estos tests NO salen de este módulo: salen de las cabeceras
públicas de Windows (``audioclientactivationparams.h``, ``mmreg.h``,
``propidlbase.h``) y de la representación documentada de los GUID. Si alguno
falla, el que está mal es el módulo, no el test.
"""

import ctypes
import time

import pytest

from audio_enhancer import process_loopback as pl


class TestGUID:
    def test_el_texto_del_guid_se_escribe_como_en_windows(self):
        """{1CB9AD4C-...} debe imprimirse igual, no con los bytes invertidos."""
        assert str(pl.IID_IAudioClient) == "{1cb9ad4c-dbfa-4c32-b178-c2f568a703b2}"
        assert str(pl.IID_IAudioCaptureClient) == "{c8adbd64-e71e-48a0-a4de-185c395cd317}"

    def test_la_memoria_queda_en_little_endian(self):
        """Lo que Windows lee es la memoria: Data1 va al revés que el texto."""
        crudo = bytes(ctypes.string_at(ctypes.byref(pl.IID_IAudioClient), 16))

        assert crudo[:4] == bytes([0x4C, 0xAD, 0xB9, 0x1C])  # 0x1CB9AD4C LE
        assert crudo[4:6] == bytes([0xFA, 0xDB])  # 0xDBFA
        assert crudo[8:] == bytes([0xB1, 0x78, 0xC2, 0xF5, 0x68, 0xA7, 0x03, 0xB2])

    def test_round_trip_de_texto(self):
        for guid in (pl.IID_IAudioClient, pl.IID_IUnknown, pl.IID_IActivateAudioInterfaceCompletionHandler):
            assert guid_de_texto_igual(guid)

    @pytest.mark.parametrize("malo", ["", "{123}", "no-es-un-guid", "{ZZZZZZZZ-0000-0000-0000-000000000000}"])
    def test_texto_invalido_lanza(self, malo):
        with pytest.raises(ValueError):
            pl.guid_de_texto(malo)


def guid_de_texto_igual(guid) -> bool:
    """Compara el GUID con el resultado de reparsear su propio texto."""
    return str(pl.guid_de_texto(str(guid))) == str(guid)


class TestEstructuras:
    def test_parametros_del_proceso_son_dos_dwords(self):
        """{ DWORD TargetProcessId; PROCESS_LOOPBACK_MODE ProcessLoopbackMode; }."""
        params = pl.AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS

        assert ctypes.sizeof(params) == 8
        assert params.TargetProcessId.offset == 0
        assert params.ProcessLoopbackMode.offset == 4

    def test_parametros_de_activacion_son_doce_bytes(self):
        """Tipo (4) + unión (8); el PID cae en el offset 4 y el modo en el 8."""
        params = pl.AUDIOCLIENT_ACTIVATION_PARAMS
        union = pl._PROCESS_LOOPBACK_UNION.ProcessLoopbackParams.offset

        assert ctypes.sizeof(params) == 12
        assert params.ActivationType.offset == 0
        assert params.u.offset + union == 4
        assert params.u.offset + pl.AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS.ProcessLoopbackMode.offset == 8

    def test_propvariant_del_blob_es_24_bytes(self):
        """x64: 8 de cabecera + 16 de la unión (BLOB = ULONG + puntero)."""
        assert ctypes.sizeof(pl.PROPVARIANT) == 24
        assert pl.PROPVARIANT.u.offset == 8
        assert ctypes.sizeof(pl.BLOB) == 16

    def test_waveformatex_es_18_bytes_empaquetado(self):
        """Sin ``#pragma pack`` el WORD final se alinearía y serían 20."""
        wf = pl.WAVEFORMATEX

        assert ctypes.sizeof(wf) == 18
        assert wf.nSamplesPerSec.offset == 4
        assert wf.nAvgBytesPerSec.offset == 8
        assert wf.nBlockAlign.offset == 12
        assert wf.wBitsPerSample.offset == 14
        assert wf.cbSize.offset == 16

    def test_el_formato_float32_cuadra_con_la_aritmetica_de_msdn(self):
        wf = pl.formato_float32(48000, 2)

        assert wf.wFormatTag == pl.WAVE_FORMAT_IEEE_FLOAT
        assert wf.nBlockAlign == 2 * 4  # canales x bytes por muestra
        assert wf.nAvgBytesPerSec == 48000 * 8
        assert wf.cbSize == 0  # sin extra: lo que espera WASAPI en compartido
        assert wf.resumen() == "tag=3 ch=2 48000 Hz 32 bits"

    def test_la_fabrica_rellena_tipo_pid_y_modo(self):
        params = pl.parametros_activacion(4242)

        assert params.ActivationType == 1  # AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
        assert params.TargetProcessId == 4242  # unión anónima: campo directo
        assert params.ProcessLoopbackMode == 0  # incluir el árbol del proceso
        # Los mismos valores vistos como bytes: PID en el offset 4, modo en el 8.
        crudo = bytes(ctypes.string_at(ctypes.byref(params), 12))
        assert int.from_bytes(crudo[4:8], "little") == 4242
        assert int.from_bytes(crudo[8:12], "little") == 0

    def test_la_fabrica_rechaza_pid_y_modo_invalidos(self):
        for pid in (0, -1):
            with pytest.raises(ValueError, match="PID"):
                pl.parametros_activacion(pid)
        with pytest.raises(ValueError, match="modo"):
            pl.parametros_activacion(10, modo=7)

    def test_el_propvariant_lleva_el_blob_del_tamano_exacto(self):
        params = pl.parametros_activacion(1234)
        variante, buffer = pl.propvariant_blob(params)

        assert variante.vt == 65  # VT_BLOB
        assert variante.blob.cbSize == 12  # sizeof(AUDIOCLIENT_ACTIVATION_PARAMS)
        assert bool(variante.blob.pBlobData) is True
        # El puntero apunta al contenido, no a una copia vacía.
        assert bytes(buffer.raw) == bytes(ctypes.string_at(ctypes.byref(params), 12))


class TestErrores:
    @pytest.mark.parametrize(
        ("codigo", "texto"),
        [
            (0x80070057, "argumento invalido"),
            (0x80070490, "proceso o dispositivo no encontrado"),
            (0x88890004, "el endpoint de audio se invalido"),
            (0x88890008, "formato no soportado por el motor"),
            (0x80004002, "la interfaz no existe en este Windows"),
        ],
    )
    def test_los_hresult_documentados_se_traducen(self, codigo, texto):
        assert pl.significado(codigo) == texto

    def test_un_hresult_desconocido_no_se_inventa(self):
        assert pl.significado(0x8004FFFF) == "codigo desconocido"

    def test_el_error_lleva_el_hresult_en_hexadecimal(self):
        exc = pl.ProcessLoopbackError("no se pudo activar", 0x80070490)

        texto = str(exc)
        assert "no se pudo activar" in texto
        assert "0x80070490" in texto  # mismo formato que usa Windows (8 dígitos)
        assert "no encontrado" in texto
        assert exc.hresult == 0x80070490

    def test_el_error_sin_hresult_no_añade_ruido(self):
        assert str(pl.ProcessLoopbackError("solo el mensaje")) == "solo el mensaje"


class TestSondeo:
    def test_esta_maquina_puede_hacer_process_loopback(self):
        """Windows 10 22H2 (19045) y mmdevapi con la función: disponible."""
        disp = pl.sondeo()

        assert disp.build >= 19041, "Process Loopback necesita 2004 (19041) o superior"
        assert disp.mmdevapi is True
        assert disp.funcion_presente is True
        assert disp.disponible is True
        assert "disponible" in disp.resumen()

    def test_sin_build_suficiente_lo_avisa_sin_decir_que_no_funciona(self):
        """Se informa la nota del build antiguo, pero no se cambia el veredicto."""
        disp = pl.sondeo()

        if disp.build < 19041:  # pragma: no cover - depende de la máquina
            assert any("19041" in nota for nota in disp.notas)
        else:
            assert disp.notas == []


# --------------------------------------------------------------------------- #
# Captura real (necesita un dispositivo de salida; se salta si no hay)
# --------------------------------------------------------------------------- #
_JUGADOR = """
import sys
import numpy as np
import pyaudiowpatch as pyaudio

segundos, pico = float(sys.argv[1]), 10 ** (-20 / 20.0)
tasa = 48000
pa = pyaudio.PyAudio()
info = pa.get_default_output_device_info()
canales = max(1, int(info["maxOutputChannels"]))
t = np.arange(int(tasa * segundos)) / tasa
tono = (pico * np.sin(2 * np.pi * 1000.0 * t)).astype(np.float32)
datos = np.repeat(tono[:, None], canales, axis=1)
stream = pa.open(
    format=pyaudio.paFloat32, channels=canales, rate=tasa, output=True,
    output_device_index=info["index"], frames_per_buffer=1024,
)
print("PLAYING", flush=True)
for i in range(0, len(datos), 1024):
    stream.write(datos[i : i + 1024].tobytes())
stream.stop_stream()
stream.close()
pa.terminate()
"""


@pytest.mark.slow
def test_captura_por_proceso_de_un_tono_conocido():
    """Captura real de otro proceso: un tono de 1 kHz a -20 dBFS de pico.

    Es la prueba de que la API funciona de verdad (activación, inicialización en
    loopback y lectura de paquetes). Si no hay dispositivo de salida o el
    intérprete no puede abrirlo, se salta: no es un fallo del módulo.
    """
    import subprocess
    import sys

    import numpy as np

    jugador = subprocess.Popen(  # noqa: S603 - argumentos fijos, sin shell
        [sys.executable, "-c", _JUGADOR, "6"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        primera = (jugador.stdout.readline() or "").strip()
        if primera != "PLAYING":
            pytest.skip(f"no se pudo reproducir el tono de prueba: {primera[:200]}")
        time.sleep(0.8)  # dar tiempo a que el flujo esté en marcha
        resultado = pl.capturar(jugador.pid, segundos=2.0)
    finally:
        jugador.kill()
        jugador.wait(timeout=5)

    datos = resultado.datos
    assert resultado.formato.nChannels >= 1
    assert datos.shape[0] >= int(0.5 * resultado.formato.nSamplesPerSec), "se capturó muy poco audio"
    mono = datos.mean(axis=1)
    pico = float(np.max(np.abs(mono)))
    rms = float(np.sqrt(np.mean(mono**2)))
    espectro = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    frecuencias = np.fft.rfftfreq(len(mono), 1.0 / resultado.formato.nSamplesPerSec)
    dominante = float(frecuencias[int(np.argmax(espectro))])

    # Tono de pico 0.1 -> -20,00 dBFS. Tolerancia por el redondeo del motor.
    assert abs(pico - 0.1) < 0.01, f"pico {pico:.4f}, esperado 0.1000"
    # Un seno de pico 0.1 tiene RMS 0.1/raiz(2) -> -23,01 dBFS.
    assert abs(20 * np.log10(rms) + 23.01) < 0.5, f"RMS {20 * np.log10(rms):.2f} dBFS"
    assert 995.0 <= dominante <= 1005.0, f"frecuencia dominante {dominante:.1f} Hz"
    assert resultado.frames_silenciosos < datos.shape[0] // 2, "medio paquete en silencio"
