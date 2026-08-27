"""Lanzador alternativo de la UI (NewMainWindow).

Equivalente a ``python -m audio_enhancer.main`` pero sin candado de
instancia única ni precarga de scipy: útil para desarrollo y para ejecutar
dos instancias en paralelo mientras se compara configuraciones.

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
