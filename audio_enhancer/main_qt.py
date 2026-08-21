"""Entrada independiente de la interfaz experimental PySide6."""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .startup_metrics import StartupMetrics
from .ui.qt_main_window import QtMainWindow


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    app = QApplication.instance() or QApplication([])
    metrics = StartupMetrics()
    window = QtMainWindow(startup_metrics=metrics)
    window.show()
    # Permite que Qt pinte el shell antes de construir la jerarquía completa.
    QTimer.singleShot(0, window.build_content)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
