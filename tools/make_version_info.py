"""Genera el recurso de version de Windows para PyInstaller desde constants.py.

Lo usan los dos specs del repo (onedir y onefile): la version sale de
``APP_VERSION`` (fuente unica), sin un cuarto punto de sincronizacion manual.
"""

from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONSTANTS = os.path.join(ROOT, "audio_enhancer", "constants.py")

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vers4},
    prodvers={vers4},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'Luis Miguel Mendoza Guevara'),
         StringStruct('FileDescription', 'Audio Enhancer FxStyle'),
         StringStruct('FileVersion', '{version}'),
         StringStruct('InternalName', 'AudioEnhancerFxStyle'),
         StringStruct('LegalCopyright', 'MIT License'),
         StringStruct('OriginalFilename', 'AudioEnhancerFxStyle.exe'),
         StringStruct('ProductName', 'Audio Enhancer FxStyle'),
         StringStruct('ProductVersion', '{version}')]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def app_version() -> str:
    """Lee APP_VERSION de constants.py (sin importar el paquete)."""
    with open(CONSTANTS, encoding="utf-8") as f:
        text = f.read()
    match = re.search(r'APP_VERSION = "([^"]+)"', text)
    if not match:
        raise RuntimeError("APP_VERSION no encontrada en audio_enhancer/constants.py")
    return match.group(1)


def write_version_info(dest: str) -> str:
    """Escribe el archivo de version y devuelve su ruta (la que usa EXE)."""
    version = app_version()
    parts = [int(p) for p in re.findall(r"\d+", version)[:4]]
    while len(parts) < 4:
        parts.append(0)
    content = TEMPLATE.format(vers4=tuple(parts), version=version)
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    return dest
