"""Lanzador experimental de la UI rediseñada (NewMainWindow).

La UI activa del proyecto (main.py) sigue usando ``ui.qt_main_window``; esta
entrada permite probar el rediseño de ``audio_enhancer/ui/new`` sin tocar la
UI activa. No usa candado de instancia única a propósito: así puede ejecutarse
en paralelo con la app publicada para comparar.

Uso:  python run_new_ui.py
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from audio_enhancer.ui.new.main_window import NewMainWindow


def main() -> int:
    app = QApplication.instance() or QApplication([])
    # Ocultar a la bandeja no debe terminar la aplicación.
    app.setQuitOnLastWindowClosed(False)
    window = NewMainWindow()
    window.show()
    # Permite que Qt pinte el shell antes de construir la jerarquía completa.
    QTimer.singleShot(0, window.build_content)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
