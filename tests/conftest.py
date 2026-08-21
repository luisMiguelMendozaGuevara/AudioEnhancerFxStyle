"""Configuracion compartida de pytest: asegura que el paquete sea importable
desde la raiz del proyecto (sin necesidad de instalarlo)."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
