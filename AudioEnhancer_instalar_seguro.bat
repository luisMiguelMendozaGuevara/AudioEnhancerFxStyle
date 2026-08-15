@echo off
setlocal
chcp 65001 >nul
title Audio Enhancer Personal - Instalador seguro
set "APP_DIR=%~dp0"
set "VENV_DIR=%APP_DIR%audio_enhancer_venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "APP_FILE=%APP_DIR%AudioEnhancer_FxStyle.py"

echo === Audio Enhancer Personal ===
echo.

if not exist "%APP_FILE%" (
    echo [ERROR] No se encuentra el archivo Python:
    echo %APP_FILE%
    pause
    exit /b 1
)

if not exist "%PYTHON_EXE%" (
    echo [INFO] Creando entorno virtual aislado...
    where py >nul 2>&1
    if %errorlevel% equ 0 (
        py -3 -m venv "%VENV_DIR%"
    ) else (
        python -m venv "%VENV_DIR%"
    )
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
)

echo [INFO] Instalando o actualizando dependencias dentro del entorno aislado...
"%PYTHON_EXE%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [INFO] pip no disponible; reparandolo con ensurepip...
    "%PYTHON_EXE%" -m ensurepip --upgrade
    if errorlevel 1 (
        echo [ERROR] No se pudo reparar pip.
        pause
        exit /b 1
    )
)
"%PYTHON_EXE%" -m pip install --upgrade numpy scipy customtkinter PyAudioWPatch pystray Pillow
if errorlevel 1 (
    echo [ERROR] No se pudieron instalar las dependencias.
    pause
    exit /b 1
)

echo [OK] Instalacion completada.
echo [INFO] Iniciando aplicacion...
"%PYTHON_EXE%" "%APP_FILE%"
if errorlevel 1 (
    echo.
    echo [ERROR] La aplicacion termino con un error.
    pause
)
endlocal
