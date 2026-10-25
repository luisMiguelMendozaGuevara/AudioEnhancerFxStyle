# -*- mode: python ; coding: utf-8 -*-

import os
import sys

# Recurso de version de Windows (Propiedades -> Detalles): se genera en cada
# build desde APP_VERSION (audio_enhancer/constants.py), sin sincronizacion
# manual. El build se lanza desde la raiz del repo.
if os.getcwd() not in sys.path:
    sys.path.insert(0, os.getcwd())
from tools.make_version_info import write_version_info  # noqa: E402

VERSION_FILE = write_version_info(os.path.join(os.getcwd(), "build", "version_info.txt"))

a = Analysis(
    ['AudioEnhancer_FxStyle.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=['pyaudiowpatch', 'audio_enhancer.dsp_process'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    # -O: quita asserts del bytecode. No hay asserts en produccion, asi que no
    # se pierde ninguna red de seguridad (ver tests: grep de asserts = 0).
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AudioEnhancerFxStyle',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX desactivado a proposito: comprimir DLLs de Qt provoca fallos de carga
    # y falsos positivos de antivirus; el tamano ya es aceptable en onedir.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/app.ico'],
    version=VERSION_FILE,
)
# Modo onedir: sin extraccion a %TEMP% en cada arranque (arranque mas rapido
# y escaneo antivirus incremental en lugar de 100 MB por ejecucion).
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='AudioEnhancerFxStyle',
)
