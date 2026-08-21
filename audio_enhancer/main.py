"""Punto de entrada: instancia única + ventana Qt + bandeja + logging rotativo.

La lógica de instancia única (detección por título + mutex nombrado) cubre
también instancias viejas de builds que no usan mutex.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .constants import APP_NAME
from .single_instance import acquire_single_instance, setup_logging
from .ui.qt_main_window import QtMainWindow


def main() -> int:
    setup_logging()
    logger = logging.getLogger("audio_enhancer.main")
    logger.warning("Arranque de %s", APP_NAME)
    if acquire_single_instance() is None:
        return 0  # ya hay otra instancia corriendo
    app = QApplication.instance() or QApplication([])
    # Ocultar a la bandeja no debe terminar la aplicación.
    app.setQuitOnLastWindowClosed(False)
    window = QtMainWindow()
    window.show()
    # Permite que Qt pinte el shell antes de construir la jerarquía completa.
    QTimer.singleShot(0, window.build_content)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
