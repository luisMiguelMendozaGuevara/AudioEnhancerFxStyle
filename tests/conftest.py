"""Configuracion compartida de pytest: asegura que el paquete sea importable
desde la raiz del proyecto (sin necesidad de instalarlo) y fija el backend
grafico de Qt a offscreen (sin ventana/GPU real: evita access violations)."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Los tests no deben spawnear el proceso hijo del DSP (audio_controller lo
# activa en producción): se desactiva por variable de entorno.
os.environ.setdefault("AUDIO_ENHANCER_NO_DSP_PROCESS", "1")

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def _sin_hardware_de_audio(monkeypatch):
    """Contrato: la suite NO toca dispositivos de audio reales.

    Un test que instancie PyAudio o abra un stream pasa en una maquina con
    dispositivos y revienta (o tumba el proceso con un access violation de
    PortAudio) en el runner del CI, que no tiene ninguno. Se bloquean los dos
    puntos de entrada para que el fallo sea un mensaje claro en vez de un
    cuelgue: asi se cazo un test que abria la captura real creyendo que solo
    probaba la idempotencia de start()."""
    try:
        import pyaudiowpatch as pyaudio
    except Exception:  # noqa: BLE001 - modulo solo-Windows (en Linux no existe)
        return

    def _bloqueado(*args, **kwargs):
        raise AssertionError(
            "el test toco hardware de audio real (el CI no tiene dispositivos): "
            "sustituye el stream por un doble (ver test_audio_controller)"
        )

    monkeypatch.setattr(pyaudio, "PyAudio", _bloqueado)
    monkeypatch.setattr(pyaudio.PyAudio, "open", _bloqueado, raising=False)


def _quiet_device_discovery() -> None:
    """Neutraliza la enumeracion WASAPI real (PortAudio) en los tests.

    Inicializar/terminar PyAudio varias veces en hilos distintos dentro del
    mismo proceso dispara un access violation de PortAudio (su API de
    enumeracion no es thread-safe): la suite crea varios NewMainWindow y cada
    uno lanza su QThread de descubrimiento. Se parchea A NIVEL DE IMPORT (no
    con el fixture monkeypatch, que es function-scoped y correria DESPUES del
    fixture module-scoped `window` que ya construyo la ventana). La
    enumeracion real se valida con el smoke test del exe."""
    try:
        from audio_enhancer.ui.new import main_window as mw

        mw.NewMainWindow._start_discovery = lambda self: None
    except Exception:  # pragma: no cover - defensivo: nunca romper la suite
        import logging

        logging.getLogger(__name__).debug("No se pudo silenciar el discovery", exc_info=True)


_quiet_device_discovery()
