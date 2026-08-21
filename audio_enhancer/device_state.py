"""Estado visual de los selectores de dispositivos (independiente del toolkit).

Función pura extraída de la antigua UI CustomTkinter para conservar su test
tras la migración a PySide6.
"""

from __future__ import annotations


def device_controls_state(*, waiting: bool, has_loopbacks: bool, has_speakers: bool) -> str:
    """Devuelve el estado visual de los selectores durante el descubrimiento."""
    if waiting:
        return "disabled"
    return "readonly"
