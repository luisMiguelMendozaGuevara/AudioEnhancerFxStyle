"""Capa de interfaz gráfica.

UI activa y única: ``ui.new.NewMainWindow`` (navegación por páginas,
sistema de temas dark/white). La UI Qt heredada (``QtMainWindow``) fue
purgada: era código muerto desde la migración (commit aa239e7) y solo
sumaba superficie de mantenimiento.
"""

from .new import NewMainWindow

__all__ = ["NewMainWindow"]
