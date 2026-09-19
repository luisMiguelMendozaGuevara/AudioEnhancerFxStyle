"""Punto de entrada: instancia única + ventana Qt + bandeja + logging rotativo.

La lógica de instancia única (detección por título + mutex nombrado) cubre
también instancias viejas de builds que no usan mutex.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .constants import APP_NAME
from .single_instance import acquire_single_instance, setup_logging
from .ui.new import NewMainWindow


def _preload_scipy() -> None:
    """Precarga scipy.signal fuera del hilo de UI.

    El import de scipy cuesta ~2,3 s: hacerlo aquí (tras mostrar la ventana)
    lo oculta tras el arranque y garantiza que no se pague dentro del callback
    de audio la primera vez que se activa una sección de filtro."""
    try:
        from audio_enhancer.dsp import _scipy_signal

        _scipy_signal()
    except Exception:
        logger = logging.getLogger("audio_enhancer.main")
        logger.debug("Precarga de scipy fallida", exc_info=True)


def main() -> int:
    setup_logging()
    logger = logging.getLogger("audio_enhancer.main")
    logger.info("Arranque de %s", APP_NAME)
    if acquire_single_instance() is None:
        return 0  # ya hay otra instancia corriendo
    # Escalado DPI: PassThrough respeta el factor REAL del monitor (125%,
    # 150%, ...) sin redondear a 1x/2x. Sin esto, en muchas laptops el layout
    # se ve borroso o desproporcionado. Debe fijarse ANTES de crear la app.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication([])
    # Ocultar a la bandeja no debe terminar la aplicación.
    app.setQuitOnLastWindowClosed(False)
    window = NewMainWindow()
    window.show()
    threading.Thread(target=_preload_scipy, name="scipy-preload", daemon=True).start()
    # Permite que Qt pinte el shell antes de construir la jerarquía completa.
    QTimer.singleShot(0, window.build_content)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
