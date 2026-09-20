# -*- mode: python ; coding: utf-8 -*-
# Build ONEFILE: un unico AudioEnhancerFxStyle.exe (sin instalador). Para quien
# no quiere instalar nada. El build principal (instalador) usa
# AudioEnhancerFxStyle.spec (onedir) + installer/AudioEnhancerFxStyle.iss.


a = Analysis(
    ['AudioEnhancer_FxStyle.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=['pyaudiowpatch'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/app.ico'],
)
