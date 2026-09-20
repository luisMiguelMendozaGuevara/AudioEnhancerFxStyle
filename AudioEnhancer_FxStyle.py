#!/usr/bin/env python3
"""Audio Enhancer FxStyle (estilo FxSound con WASAPI loopback) - lanzador fino.

La implementación vivía en un monolito; ahora reside en el paquete
``audio_enhancer``. Este módulo queda como punto de entrada para conservar la
compatibilidad con ``AudioEnhancer_activar.bat`` y
``AudioEnhancer_instalar_seguro.bat``.

PyInstaller lo usa como script de arranque de los dos specs del repo:
``AudioEnhancerFxStyle.spec`` (onedir + instalador Inno Setup) y
``AudioEnhancerFxStyle-onefile.spec`` (portable de un solo .exe).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audio_enhancer.main import main  # noqa: E402

if __name__ == "__main__":
    main()
