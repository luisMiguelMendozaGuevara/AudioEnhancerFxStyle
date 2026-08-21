"""Constantes globales de Audio Enhancer FxStyle.

Este modulo no depende de tkinter, numpy ni scipy: cualquier capa puede
importarlo sin arrastrar dependencias pesadas.
"""

import os
import sys

APP_NAME = "Audio Enhancer FxStyle"
APP_VERSION = "1.2.0"
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

# Título de ventana/bandeja. Se usa también en "traer al frente" de instancia
# única para que ambas fuentes coincidan.
WINDOW_TITLE = "Audio Enhancer - FxStyle"

# Audio
SAMPLE_RATE = 48000
CHUNK = 1024
RING_SECONDS = 0.2

# Rutas
EXE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
ASSETS_DIR = os.path.join(EXE_DIR, "assets")


def resource_path(name):
    """Ruta a un asset embebido: valida para .exe (PyInstaller) y fuente .py."""
    base = getattr(sys, "_MEIPASS", "")
    dirs = []
    if base:
        dirs.append(os.path.join(base, "assets"))
        dirs.append(base)
    dirs.append(ASSETS_DIR)
    dirs.append(EXE_DIR)
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return name


CONFIG_PATH = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "AudioEnhancerFxStyle",
    "config.json",
)

# Colores Fluent
ACCENT = "#0078D4"
DANGER = "#d13438"
OK = "#107c10"
WARN = "#9d5d00"

# Preset "sin efectos": nombre canónico, también usado como valor por defecto
# de los combos y de la persistencia.
DEFAULT_PRESET = "Plano (sin efectos)"

# Palabras clave para reconocer dispositivos virtuales / cables VB-Audio.
VIRTUAL_CABLE_KEYWORDS = ("cable", "vb-audio", "voicemeeter", "virtual")
CABLE_KEYWORDS = ("cable", "vb-audio", "voicemeeter")
