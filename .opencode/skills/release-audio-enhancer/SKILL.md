---
name: release-audio-enhancer
description: Publica una release de AudioEnhancerFxStyle de forma reproducible: versiona, valida, compila el exe con PyInstaller, prueba el arranque, arregla el acceso directo del escritorio, commitea, sube a GitHub y crea la release con gh. Usar cuando el usuario pida "crear el release", "compilar el exe", "publicar", "hacer el acceso directo", "release", "build" o "subir versión" del proyecto AudioEnhancerFxStyle.
---

# Release AudioEnhancerFxStyle

Flujo idempotente para publicar una nueva versión del proyecto en
`C:\Users\asus\Documents\PROYECTOS IA\AudioEnhancerFxStyle`.
Ejecutar **siempre desde la raíz del repo** (usa el parámetro `workdir`).

## 0. Precondiciones

- `gh` autenticado como `luisMiguelMendozaGuevara` (verificar con `gh auth status`).
- venv local `audio_enhancer_venv\Scripts\python.exe` con pytest, ruff y PyInstaller.
- El acceso directo de escritorio apunta al exe:
  `C:\Users\asus\Desktop\AudioEnhancerFxStyle.exe - Acceso directo.lnk`.

## 1. Determinar la versión

- Ver el último tag remoto: `git ls-remote --tags origin`.
- La nueva versión es la siguiente según semver (v1.1.0 -> v1.1.1 para bugfix).
- Editar `audio_enhancer/constants.py` -> `APP_VERSION = "x.y.z"`.
- `README.md` si menciona la versión.

## 2. Validar (antes de commitear)

```
audio_enhancer_venv\Scripts\python.exe -m pytest -q
audio_enhancer_venv\Scripts\python.exe -m ruff check .
audio_enhancer_venv\Scripts\python.exe -m ruff format --check .
```

## 3. Commit

Dos commits lógicos con estilo convencional (es, igual que el historial):
separar los que NO son de la release (p. ej. un fix de audio) de la subida de
versión:

```
git add <archivos del fix> && git commit -m "fix: descripcion breve del fallo"
git add audio_enhancer/constants.py README.md && git commit -m "chore: bump version a x.y.z"
```

Nunca commitear `AGENTS.md` (gitignored), exes, `actual/`, `dist/` ni `*.spec`.

## 4. Compilar el exe

```
audio_enhancer_venv\Scripts\python.exe -m PyInstaller --clean --noconfirm --onefile --windowed ^
  --name AudioEnhancerFxStyle --icon assets\app.ico --add-data "assets;assets" ^
  --hidden-import pyaudiowpatch AudioEnhancer_FxStyle.py
```

Resultado: `dist\AudioEnhancerFxStyle.exe`.

## 5. Smoke test del exe

```
$p = Start-Process "dist\AudioEnhancerFxStyle.exe" -PassThru
Start-Sleep 6
if (Get-Process -Id $p.Id) { Write-Output OK; Stop-Process -Id $p.Id -Force } else { Write-Output FAIL }
```

Si FALLA, no publiques: revisa el todo o el warning de PyInstaller en `build\`.

## 6. Arreglar el acceso directo

Repuntar el `.lnk` del escritorio al exe recién compilado:

```
$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut("C:\Users\asus\Desktop\AudioEnhancerFxStyle.exe - Acceso directo.lnk")
$lnk.TargetPath  = "C:\Users\asus\Documents\PROYECTOS IA\AudioEnhancerFxStyle\dist\AudioEnhancerFxStyle.exe"
$lnk.WorkingDirectory = "C:\Users\asus\Documents\PROYECTOS IA\AudioEnhancerFxStyle\dist"
$lnk.IconLocation = "$($lnk.TargetPath),0"
$lnk.Save()
```

Verificar con `$sh.CreateShortcut(...)` que `ExistsTarget` es `True`.

## 7. Push + release

```
git push origin main
gh release create vX.Y.Z "dist/AudioEnhancerFxStyle.exe" --title "vX.Y.Z" --generate-notes
```

El push del tag dispara el workflow `.github/workflows/pyinstaller-build.yml`
que recompila en CI y adjunta otro exe a la misma release (normal, no es un
conflito). Verificar: `gh release view vX.Y.Z`.

## 8. Informar al usuario

Resumen breve con: versión publicada, URL de la release, exe local
(`dist\AudioEnhancerFxStyle.exe`), acceso directo corregido, y aviso de que el
workflow de CI sigue compilando en GitHub Actions.

## Tenga en cuenta

- El control de deriva del ring (el "lagazo a los ~20 s") está en
  `audio_enhancer/engine.py` (`_drift_gain`, `_drift_deadband`, `_max_drift_frames`);
  al tocarlo, añade una prueba sintética en `tests/test_engine.py`.
- Los cambios hechos por este skill son dependientes de Windows y de la ruta
  local del repo; si el repo se clona en otra máquina, ajustar las rutas del
  paso 6 (usar la ruta de esta máquina).