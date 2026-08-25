"""Capa de interfaz gráfica.

UI activa: ``ui.new.NewMainWindow`` (rediseño con navegación por páginas y
sistema de temas dark/white). La UI anterior (``QtMainWindow``) se conserva
como ruta de rollback; no recibe nuevas funciones.
"""

from .new import NewMainWindow
from .qt_main_window import QtMainWindow

__all__ = ["NewMainWindow", "QtMainWindow"]
