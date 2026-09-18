"""Configuracion compartida de pytest: asegura que el paquete sea importable
desde la raiz del proyecto (sin necesidad de instalarlo) y fija el backend
grafico de Qt a offscreen (sin ventana/GPU real: evita access violations)."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


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
