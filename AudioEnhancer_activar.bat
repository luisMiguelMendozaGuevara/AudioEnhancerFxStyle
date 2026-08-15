@echo off
setlocal
title Audio Enhancer Personal
set "APP_DIR=%~dp0"
set "PYTHON_EXE=%APP_DIR%audio_enhancer_venv\Scripts\python.exe"
set "APP_FILE=%APP_DIR%AudioEnhancer_FxStyle.py"

if not exist "%PYTHON_EXE%" (
    echo No existe el entorno virtual. Ejecuta primero:
    echo AudioEnhancer_instalar_seguro.bat
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%APP_FILE%"
if errorlevel 1 pause
endlocal
