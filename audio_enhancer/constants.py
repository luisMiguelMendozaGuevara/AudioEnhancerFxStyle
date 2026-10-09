"""Constantes globales de Audio Enhancer FxStyle.

Este modulo no depende de tkinter, numpy ni scipy: cualquier capa puede
importarlo sin arrastrar dependencias pesadas.
"""

import logging
import os
import sys

logger = logging.getLogger("audio_enhancer.constants")

APP_NAME = "Audio Enhancer FxStyle"
APP_VERSION = "1.5.13"
# (C4) APP_DIR eliminado: duplicaba EXE_DIR y nadie lo usaba.

# Título de ventana/bandeja. Se usa también en "traer al frente" de instancia
# única para que ambas fuentes coincidan.
WINDOW_TITLE = "Audio Enhancer - FxStyle"

# Audio
SAMPLE_RATE = 48000
CHUNK = 1024
# (La captura usa el mismo CHUNK: el callback solo copia al ring crudo y el
# DSP va en hilo dedicado, así que el flujo es suave bloque a bloque.)
# 0.5 s de ring con consigna de 100 ms deja ~400 ms de margen por encima para
# absorber ráfagas del reloj/carga sin desbordar (antes 0.35 s -> ~250 ms).
RING_SECONDS = 0.5
# Latencia objetivo del ring (ms): el punto de consigna del control de deriva.
# Antes era RING_SECONDS/2 = 100 ms fijos. 60 ms es el compromiso
# estabilidad/latencia; la UI ofrece 40/60/100 ms.
DRIFT_TARGET_MS = 60
LATENCY_CHOICES_MS = (40, 60, 100)
# Al abrir la salida, el dispositivo físico tarda en alcanzar su reloj real
# (~20 s en algunos drivers): durante ese lapso la captura va más rápido y el
# motor descartaría audio a golpes (glitches audibles). Se silencia y descarta
# explícitamente este tiempo para que el inicio sea limpio.
STARTUP_MUTE_S = 2.0

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
    # No encontrado: se registra (antes se devolvía el nombre pelado en
    # silencio y el fallo aparecía luego como icono/imagen vacía sin pista).
    logger.warning("Asset no encontrado: %s (buscado en %s)", name, dirs)
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
