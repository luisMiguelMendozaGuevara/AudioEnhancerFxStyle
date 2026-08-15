# Audio Enhancer FxStyle

Mejorador de audio para Windows inspirado en FxSound. Captura audio renderizado mediante **WASAPI loopback**, aplica procesamiento DSP en tiempo real y lo reproduce en una salida física seleccionada.

> Proyecto experimental de código abierto. La latencia, estabilidad y compatibilidad dependen del hardware, los controladores y la ruta de audio elegida.

## Funciones

- Captura de audio del sistema mediante PyAudioWPatch/WASAPI loopback.
- Ecualizador de 9 bandas.
- Refuerzo de graves y agudos.
- Control de volumen con rampas suaves.
- Compresor RMS y limitador suave.
- Analizador de espectro.
- Ring buffer de baja latencia con compensación de deriva entre relojes.
- Fundidos anti-chasquidos ante underruns.
- Presets incorporados y presets personalizados.
- Interfaz en español con modo A/B para comparar audio procesado y directo.

## Requisitos

- Windows 10/11.
- Python 3.10 o superior.
- Un dispositivo de captura loopback WASAPI y una salida física.
- Para una ruta completa del audio del sistema puede ser necesario VB-CABLE u otro dispositivo virtual.

## Idioma de la interfaz

La aplicación detecta automáticamente el idioma de Windows al iniciar:

- Windows en español: interfaz en español.
- Windows en inglés: interfaz en inglés.
- Cualquier otro idioma: español como respaldo.

No hace falta configurar nada. Si cambias el idioma de Windows, la aplicación lo detectará en el siguiente inicio.

## Para usarlo sin saber programar

Si solo quieres utilizar la aplicación, no necesitas instalar Python ni abrir una consola:

1. En GitHub abre la sección **Releases** del proyecto y descarga el archivo `.exe` de la versión más reciente:
   [Descargar Audio Enhancer desde Releases](../../releases/latest)
2. Descarga VB-CABLE desde la página oficial de VB-Audio:
   [Descargar VB-CABLE oficialmente](https://vb-audio.com/Cable/)
3. Descomprime VB-CABLE, ejecuta el instalador correspondiente a tu sistema y reinicia Windows si el instalador lo solicita.
4. Abre **Configuración de sonido de Windows** y envía el audio que quieras procesar a **CABLE Input**.
5. Abre Audio Enhancer y selecciona como **Captura (loopback)** el dispositivo que contenga `CABLE Input`.
6. Selecciona como **Salida (física)** tus parlantes o auriculares reales.
7. Pulsa **Iniciar audio del sistema**.

### ¿Qué hace VB-CABLE?

VB-CABLE es un cable de audio virtual. Funciona como un puente:

```text
Aplicaciones → CABLE Input → Audio Enhancer → Parlantes/Auriculares
```

No es malware ni un reproductor. Solo crea una entrada y una salida virtual para que Audio Enhancer pueda recibir el sonido de Windows, procesarlo y enviarlo a tu dispositivo físico.

> Importante: no selecciones `CABLE Input` como salida física dentro de Audio Enhancer. La salida debe ser el dispositivo real donde quieres escuchar el audio. Si seleccionas el mismo dispositivo como captura y salida puedes crear eco o realimentación.

> El enlace de Releases funciona automáticamente cuando el proyecto ya esté publicado en GitHub. Si GitHub muestra varias descargas, elige el `.exe` y no los archivos `Source code`.

## Instalación desde código fuente

```bat
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python AudioEnhancer_FxStyle.py
```

También se puede usar `AudioEnhancer_instalar_seguro.bat`, que crea el entorno virtual local e instala las dependencias.

## Ruta de audio recomendada

1. Instala y configura VB-CABLE si necesitas capturar todo el audio del sistema.
2. Envía el audio de Windows o de las aplicaciones a `CABLE Input`.
3. En la aplicación selecciona el loopback de `CABLE Input` como captura.
4. Selecciona como salida unos parlantes o auriculares físicos.
5. No selecciones el mismo dispositivo como captura y salida: produciría eco o realimentación.
6. Desactiva FxSound u otros procesadores duplicados durante las pruebas.

## Compilar un ejecutable

```bat
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller.exe --clean --noconfirm --onefile --windowed ^
  --name AudioEnhancerFxStyle ^
  --icon assets\app.ico ^
  --add-data "assets;assets" ^
  AudioEnhancer_FxStyle.py
```

El ejecutable se generará en `dist\AudioEnhancerFxStyle.exe`. Los binarios, entornos virtuales y artefactos de compilación no forman parte del repositorio fuente.

## Validación rápida

```bat
python -m py_compile AudioEnhancer_FxStyle.py
python -c "import numpy, scipy, customtkinter, pyaudiowpatch; print('Dependencias OK')"
```

## Limitaciones conocidas

- Es una aplicación de usuario, no un driver de audio ni un APO global de Windows.
- La ruta loopback → procesamiento → salida añade latencia.
- Captura y salida pueden usar relojes de hardware diferentes; el ring buffer intenta compensar esa deriva.
- Bluetooth, USB y controladores con buffers grandes pueden aumentar la latencia.
- Debe probarse a volumen bajo para evitar picos inesperados.
- El procesamiento de audio real depende de los dispositivos instalados y no se puede garantizar solo con una prueba sintética.

## Contribuir

Las contribuciones son bienvenidas. Antes de abrir un issue incluye:

- Versión de Windows.
- Dispositivo de captura y salida.
- Frecuencia de muestreo.
- Si se usa VB-CABLE, FxSound, Bluetooth o USB.
- Pasos exactos para reproducir el problema.
- Mensajes de error completos, sin incluir datos personales.

Consulta `LICENSE` para los términos de uso.
