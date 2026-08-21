"""Configuracion compartida de pytest: asegura que el paquete sea importable
desde la raiz del proyecto (sin necesidad de instalarlo)."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def _quiet_device_discovery(monkeypatch):
    """Aísla los tests de la enumeración WASAPI real (PortAudio).

    Inicializar/terminar PyAudio varias veces en el mismo proceso dispara un
    assert de PortAudio y deja hilos de descubrimiento huérfanos; además hace
    los tests deterministas y rápidos. La enumeración real se valida con el
    smoke test de la ventana (fuera de pytest) y con el arranque del exe.
    """
    from audio_enhancer.ui import qt_main_window as qmw

    monkeypatch.setattr(qmw.QtMainWindow, "_start_discovery", lambda self: None)
