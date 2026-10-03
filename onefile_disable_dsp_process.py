"""Runtime hook SOLO para el build ONEFILE.

El proceso hijo del DSP (multiprocessing "spawn") provoca un access violation
(0xC0000005) dentro de un ejecutable onefile de PyInstaller (el hijo re-extrae
el exe a %TEMP% y el arranque de Qt/PortAudio colisiona). En el build onedir
(instalador + acceso directo) el proceso hijo SÍ se usa.

Aquí se desactiva el proceso hijo para el portable: el DSP vuelve a correr en
un hilo del proceso principal (funciona, aunque con la contención de GIL que
motivó el proceso hijo). El instalado/onedir es la vía recomendada.
"""

import os

os.environ.setdefault("AUDIO_ENHANCER_NO_DSP_PROCESS", "1")
