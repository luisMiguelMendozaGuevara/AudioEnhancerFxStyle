# -*- mode: python ; coding: utf-8 -*-
# Build ONEFILE: un unico AudioEnhancerFxStyle.exe (sin instalador). Para quien
# no quiere instalar nada. El build principal (instalador) usa
# AudioEnhancerFxStyle.spec (onedir) + installer/AudioEnhancerFxStyle.iss.

import os
import sys

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
    runtime_hooks=['onefile_disable_dsp_process.py'],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AudioEnhancerFxStyle',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX desactivado: mismo motivo que en el spec onedir (DLLs de Qt + AV).
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/app.ico'],
    version=VERSION_FILE,
)
