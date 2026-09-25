# Changelog

Todas las versiones notables de **Audio Enhancer FxStyle**.
Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.1.0/)
y versionado [SemVer](https://semver.org/lang/es/).

## [1.5.4] - 2026-09-24

Estabilidad de audio y de arranque, más **crossfeed para auriculares**.

### Corregido
- **Crash intermitente al arrancar** (`0xc0000005` en pyside6.abi3.dll): el menú
  del icono de bandeja se creaba sin *parent* y Python lo recolectaba (Qt no
  toma ownership del menú); el icono usaba un puntero liberado. Ahora se crea con
  parent + referencia viva, y la bandeja nace con el bucle de eventos activo.
  También se mantiene vivo el mutex de instancia única (evita 2ª ventana).
- **Saltos/descartés de audio**: un doble arranque abría DOS capturas (callbacks
  duplicados → ring desbordado). `start()` y el botón son ahora idempotentes.
- **Bucle de retroalimentación del cable** en modo estirado/underflow acotado.
- **Prueba**: intercalado de audio con captura a ráfagas (colchón de jitter) y
  estirado en underrun pequeño (menos microcortes sin subir latencia).

### Añadido
- **Crossfeed BS2B para auriculares** (OFF por defecto): perfiles Natural
  (DEFAULT), Moderate (CMOY) y Strong (JMEIER) + **modo Avanzado** (frecuencia
  300-2000 Hz, nivel 1-15 dB). Reproduce el algoritmo de libbs2b (verificado).
- **Medidor de gain reduction (GR)** del limitador en Inicio.
- **Captura nativa sin cable virtual (EXPERIMENTAL, opt-in)**: auto-selecciona
  el loopback del dispositivo de salida; permite usar la app sin instalar
  VB-CABLE. OFF por defecto hasta validarla.
- **Dos artefactos** de release: instalador (onedir + Inno Setup) y portable
  (onefile).

### Diagnóstico
- Métricas de huecos separadas por causa (descartes/overflows/fill) y log cada
  ~10 s. Gate de rendimiento del DSP por peor caso (`--check`).

### Notas
- Cobertura de tests: 246. Todas las protecciones (limitador, compresor,
  true-peak, techo de seguridad, recorte final, watchdog) son configurables.

## [1.5.3] - 2026-09-20

Corrige los **microcortes** ("pausas") y hace configurables todas las
protecciones. Dos artefactos de release: instalador y portable.

### Corregido
- **Microcortes por pico del DSP**: `scipy.signal.resample_poly` re-diseñaba el
  filtro FIR (Kaiser) en CADA llamada → picos de 20-30 ms/bloque. Ahora el FIR
  se construye una sola vez y se aplica con `upfirdn` (bit a bit idéntico).
  Pico de `full_hot` **23081 → 3787 µs (−84 %)** y `max` dentro del presupuesto
  del bloque (gate del banco de pruebas: `[OK]`).
- **Latencia que no cambiaba**: la señal emitía los **ms** pero el handler los
  trataba como índice (`itemData(100)` → None → 60). Ahora cambia de verdad y
  se aplica **en caliente** (sin reiniciar el audio).
- **Botones recortados en español**: "Restablecer todo" (EQ) y otros usaban
  ancho fijo; ahora crecen con el texto.
- **Doble clic en Iniciar**: el botón se bloquea durante el prefill (~0.5 s),
  antes un 2º clic detenía el audio recién iniciado.
- **Contraste del tema oscuro**: `TEXT_DIM` 3.0:1 → 4.6:1 (WCAG AA).
- **Autostart empaquetado**: el comando ya no registra la ruta del exe dos veces.
- **Importar/Exportar presets** (botones decorativos): conectados a JSON, con
  saneo reutilizable de `ConfigManager`.

### Añadido
- **Todas las protecciones son configurables** (página Efectos): Limitador,
  Compresor, True-peak, **Techo de seguridad** y **Recorte final (±1.0)**.
  En Config: **watchdog** ("Detener el audio si se pierde el dispositivo").
- **Tooltips explicativos** por efecto (ES/EN).
- **Medidores estéreo L/R** en Inicio.
- **Dos artefactos**: instalador (onedir + Inno Setup) y portable (onefile).
- Nota: las mejoras llegan a `main`; el instalador/portable se compilan en CI.

### Diagnóstico
- Métricas de huecos separadas por causa (`descartes`/`overflows`/`fill`), que
  permiten ver si un corte viene del DSP o de la captura.

## [1.5.2] - 2026-09-19

Ronda de mejoras de motor (auditoría DSP, fases 0–3) + rondas R2/R3 de
calidad y rendimiento, integradas sobre la UI PySide6.

### Añadido
- Limitador **brickwall con look-ahead 3 ms** (techo real) y **compresor RMS
  por muestra** (fin del zipper noise).
- **True-peak ×4** en el limitador (picos inter-muestra) y **Q por banda** en el EQ.
- **Latencia objetivo configurable** 40/60/100 ms (por defecto 60 ms) en la página Audio.
- **Downmix 5.1/7.1 → estéreo** cuando el loopback rechaza estéreo.
- **Métricas vivas** del motor en la barra de estado (underruns/huecos/deriva).
- `EnhancerParams` (instantánea inmutable) para aplicar presets/config en bloque.
- `autostart.py`, `audio_controller.py`, `config_manager.py` (separación de responsabilidades).
- Benchmarks del camino de audio (`benchmarks/bench_dsp.py`).
- CI con matriz **Windows + Ubuntu** × Python 3.11/3.12 y `ruff format --check`.

### Cambiado
- Cadena DSP reordenada a mastering: filtros → compresor → volumen → limitador.
- Histéresis Schmitt en las secciones del EQ (sin parpadeo on/off).
- Guardado de config **atómico** (tmp + fsync + os.replace).
- `SpectrumWorker` adaptativo: FFT solo cuando el espectro es visible.
- Latencia percibida ~81 ms (antes ~200 ms).

### Corregido
- **Estática por recorte duro**: techo de seguridad transparente (0.99) cuando el
  limitador está apagado (`_safety_ceiling`).
- **Ecualizador congelado**: el espectro también debe refrescarse en la página EQ.
- **Autostart roto**: el comando llegaba literal al registro; ahora entrecomillado y testeable.
- **Config ilegible por BOM UTF-8** (`utf-8-sig`).
- **Valores de config sin acotar** → se recortan al rango de la UI.
- **Log mudo**: nivel INFO para arranque/parada/métricas.
- **Escalado DPI** `PassThrough` en laptops 125%/150%.
- **Crash nativo** al cambiar tema/idioma (reconstrucción sin tocar PyAudio).

## [1.4.4] - 2026-08-24
- Auditoría completa de conexiones UI↔motor (presets, eliminar, desincronización, idioma).
- El EQ mutaba la lista in-place y no emitía `eq_changed` (el DSP no recibía cambios).
- Puente `AudioState`→`Enhancer`: sliders de EQ y efectos afectan al audio.

## [1.4.0] - 2026-08-24
- UI nueva (PySide6) con temas claro/oscuro, i18n ES/EN y páginas (Inicio, EQ, Efectos, Audio, Presets, Config).
- Espectro por banda detrás de la curva del EQ.

## [1.3.0] - 2026-08-24
- Presets incluidos y personalizados; importar/exportar.

## [1.2.0] - 2026-08-20
- Migración del monolito Tk a paquete `audio_enhancer` + UI Qt.

## [1.1.0] - 2026-08-16
- Autoarranque con Windows, bandeja del sistema y notificaciones.

## [1.0.1] - 2026-08-15
- Correcciones iniciales de estabilidad del loopback.

## [1.0.0] - 2026-08-15
- Primera versión: captura WASAPI loopback, cadena DSP (EQ/compresor/limitador) y salida física.

[1.5.2]: https://github.com/luisMiguelMendozaGuevara/AudioEnhancerFxStyle/releases/tag/v1.5.2
[1.4.4]: https://github.com/luisMiguelMendozaGuevara/AudioEnhancerFxStyle/releases/tag/v1.4.4
[1.4.0]: https://github.com/luisMiguelMendozaGuevara/AudioEnhancerFxStyle/releases/tag/v1.4.0
[1.3.0]: https://github.com/luisMiguelMendozaGuevara/AudioEnhancerFxStyle/releases/tag/v1.3.0
[1.2.0]: https://github.com/luisMiguelMendozaGuevara/AudioEnhancerFxStyle/releases/tag/v1.2.0
[1.1.0]: https://github.com/luisMiguelMendozaGuevara/AudioEnhancerFxStyle/releases/tag/v1.1.0
[1.0.1]: https://github.com/luisMiguelMendozaGuevara/AudioEnhancerFxStyle/releases/tag/v1.0.1
[1.0.0]: https://github.com/luisMiguelMendozaGuevara/AudioEnhancerFxStyle/releases/tag/v1.0.0
[1.5.3]: https://github.com/luisMiguelMendozaGuevara/AudioEnhancerFxStyle/releases/tag/v1.5.3
[1.5.4]: https://github.com/luisMiguelMendozaGuevara/AudioEnhancerFxStyle/releases/tag/v1.5.4
