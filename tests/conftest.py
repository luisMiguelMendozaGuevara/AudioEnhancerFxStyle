"""Configuracion compartida de pytest: asegura que el paquete sea importable
desde la raiz del proyecto (sin necesidad de instalarlo)."""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
