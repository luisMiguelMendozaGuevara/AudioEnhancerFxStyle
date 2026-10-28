# Avisos de terceros — Audio Enhancer FxStyle

Este programa se distribuye bajo licencia **MIT** (ver `LICENSE`). Además incluye
o enlaza los siguientes componentes de terceros, cuyas licencias se respetan y se
reproducen más abajo.

## Qt / PySide6 — LGPL-3.0-only

- Componente: **Qt 6** y **PySide6** (`PySide6`, `PySide6_Essentials`,
  `PySide6_Addons`, `shiboken6`), versión 6.8.3.
- Titular: The Qt Company Ltd. y contribuyentes de Qt; PySide6 es de The Qt
  Company Ltd.
- Licencia elegida: **GNU Lesser General Public License v3.0** (texto completo en
  `LICENSES/LGPL-3.0.txt`). PySide6 también se ofrece bajo GPLv2/GPLv3 y bajo
  licencia comercial; este proyecto usa la opción LGPLv3.
- **Cómo se cumple el requisito de reenlazado (LGPL §4)**: la distribución
  principal es *onedir*, en la que las bibliotecas de Qt viajan como archivos
  separados y sustituibles (`_internal/PySide6/Qt6*.dll` y los plugins de
  `_internal/PySide6/plugins/`). El usuario puede reemplazarlas por su propia
  versión de Qt sin recompilar este programa.
- **Sin modificaciones**: no se ha modificado el código de Qt ni de PySide6.
- Código fuente de Qt: <https://code.qt.io/cgit/qt/> · de PySide6:
  <https://code.qt.io/cgit/pyside/pyside-setup.git/>

## PyAudioWPatch — MIT

- Componente: `PyAudioWPatch` 0.2.12.8 (PortAudio con soporte WASAPI loopback).
- PortAudio: <https://www.portaudio.com/> (MIT).
- Uso: captura y reproducción de audio del sistema.

## NumPy — BSD-3-Clause

- Componente: `numpy` (versión indicada en `pyproject.toml`).
- Uso: todo el procesado DSP y el manejo de buffers de audio.

## SciPy — BSD-3-Clause

- Componente: `scipy`.
- Uso: diseño de filtros y utilidades numéricas del DSP.

## Python — PSF License

- Componente: CPython (el ejecutable incluye su propio intérprete).
- Uso: plataforma de ejecución.

---

Si distribuyes una versión modificada de este programa, mantén estos avisos y la
posibilidad de sustituir las bibliotecas de Qt, tal como exige la LGPLv3.
