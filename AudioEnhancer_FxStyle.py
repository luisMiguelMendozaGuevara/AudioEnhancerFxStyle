#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audio Enhancer FxStyle (estilo FxSound con WASAPI loopback) - lanzador fino.

La implementación vivía en un monolito; ahora reside en el paquete
``audio_enhancer``. Este módulo queda como punto de entrada para conservar la
compatibilidad con ``AudioEnhancer_activar.bat``, ``AudioEnhancer_instalar_seguro.bat``
y ``AudioEnhancerFxStyle.spec`` (PyInstaller lo usa como script de arranque).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audio_enhancer.main import main  # noqa: E402


if __name__ == "__main__":
    main()