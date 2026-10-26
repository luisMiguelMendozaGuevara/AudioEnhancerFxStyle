# Changelog

Todas las versiones notables de **Audio Enhancer FxStyle**.
Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.1.0/)
y versionado [SemVer](https://semver.org/lang/es/).

## [1.7.0] — en desarrollo (rama `feat/v1.7-diagnostics`)

Instrumentación y medición normativa, más un cambio de fondo en la nivelación
(ahora por sonoridad medida, no por RMS). El resto de la cadena DSP no se toca:
la mayoría de los cambios añaden lo necesario para poder afirmar y comprobar lo
que la app hace.

### Cambiado — Leveling por sonoridad (LUFS), no por RMS
- El AGC de nivelación mide ahora con el **filtro K de ITU-R BS.1770** y apunta
  a **-18 LUFS** (antes: -18 dBFS RMS), con envolvente lenta propia (ataque
  0,5 s / liberación 3 s) y el mismo tope de corrección (±9 dB) y control 0-10.
- Motivo: el RMS no es sonoridad percibida. A igual RMS, un tono de 60 Hz mide
  -29,6 LUFS y uno de 2 kHz -23,6 LUFS: el AGC antiguo trataba ambos igual.
- Efecto medido: material a -26 y a -12 LUFS converge a **-17,9 / -17,8 LUFS**
  con la nivelación a 10 (antes quedaban a ±11 LU de diferencia). Coste: +0,27 ms
  por bloque solo cuando la nivelación está activa.
- La documentación de efectos (`docs/EFECTOS.md`) explica el cambio y por qué.

### Añadido — medición de sonoridad (ITU-R BS.1770 / EBU R 128)
- `audio_enhancer/loudness.py`: filtro K (coeficientes idénticos a la tabla del
  anexo 1 de BS.1770-4 a 48 kHz), sonoridad **momentánea** (400 ms), de **corto
  plazo** (3 s), **integrada** con doble gate (−70 LUFS absoluto, −10 LU
  relativo) y **rango de sonoridad** (gate −20 LU).
- Validado contra la tabla 1 de **EBU Tech 3341-2023** (casos 1-5, tolerancia
  oficial ±0,1 LUFS): medido −22,993 / −32,993 / −23,014 / −23,014 / −22,979.
- La sonoridad de la **salida** se publica por memoria compartida (huecos 7..10
  de la telemetría) y se muestra en la barra de estado (`-16.4 LUFS`) y en el
  informe de diagnóstico (`sonoridad M=… S=… I=… LUFS (LRA … LU)`).

### Añadido — diagnóstico de latencia y de tiempo real
- `audio_enhancer/diagnostics.py`: `RollingStats` (p50/p95/p99/max con ventana
  acotada) y `LatencyBreakdown` (captura + ring + cola del hijo + salida, en ms).
- `AudioEngine.latency_breakdown()` y `AudioEngine.timing_snapshot()`: la barra
  de estado muestra latencia total y `dsp p99`; el informe exportado añade las
  líneas `latencia=`, `dsp=` y `rt=` (estado de MMCSS).
- `audio_enhancer/rt_priority.py`: registro del hilo de audio en **MMCSS**
  (`avrt.dll`, tarea "Pro Audio" para el DSP y "Audio" para la bomba), con
  reversión garantizada y degradación silenciosa fuera de Windows.

### Añadido — red de seguridad de calidad de audio
- `tests/test_audio_quality.py`: THD+N del camino neutro (**medido −151 dB**),
  ganancia del EQ frente a la teórica (**error 0,08 dB**), techo del limitador
  con picos inter-muestra (**−0,446 dBTP** con true-peak ×4 independiente),
  ausencia de NaN con parámetros extremos, causalidad/retardo de grupo
  (**0,09 muestras** en neutro) y estabilidad con la cadena completa activa.

### Corregido (encontrado por las auditorías de esta iteración)
- El gate relativo del integrado se aplicaba sobre todos los bloques y podía
  **reabrir** el gate absoluto; ahora los dos gates se acumulan (BS.1770-4 §2.4).
- `LoudnessMeter` usa estado `zi` con la forma que exige scipy para `axis=0`
  (con 1 canal reventaba; funcionaba con 2 por coincidencia).
- La medición de sonoridad ya **no puede suprimir audio**: el bloque procesado
  se publica antes y la medición va en su propio `try`.
- `_sync_telemetry` leía la sonoridad **dos veces**, la segunda fuera del lock.
- Los `try/finally` de MMCSS garantizan la reversión aunque el bucle salga por
  excepción; una excepción en la bomba ya no mata el audio en silencio.
- El recálculo del integrado está acotado (exacto con historial corto, 1 Hz a
  partir de 10 min) para no crecer con la duración de la sesión.
- La UI protege el tick de métricas y el informe de diagnóstico: un fallo de
  instrumentación ya no puede congelar la barra ni dejar al usuario sin informe.

## [1.6.1] — 2026-10-25

Versión de **corrección de fallos** sobre 1.6.0 (sin cambios de esquema de
config ni de interfaz) más la migración del entorno a **Python 3.13**.

### Corregido — audio
- **Desbordamiento del ring insertaba silencio**: al descartar audio por
  desbordamiento se ponía a cero el tramo *siguiente* (audio aún no
  reproducido) en vez del tramo descartado, así que cada desborde se oía como
  un hueco además de la pérdida. Ahora solo se descarta lo más viejo.
  Verificado con salida más lenta que la captura: 124 k frames descartados y
  **0 frames de silencio insertado**.
- **El Q del ecualizador a 0 dejaba el audio en NaN para siempre**: con
  `q = 0` el denominador del biquad peaking se anulaba, los coeficientes
  salían inf/NaN y el estado `zi` quedaba envenenado (la salida seguía en NaN
  aunque se corrigiera el control). El Q se acota ahora dentro del propio
  filtro y, como red de seguridad, si la cadena produce un pico no finito se
  reinicia el estado y se emite **un solo bloque** en silencio (el siguiente
  ya suena).
- **Espectro muerto en dispositivos de 8 a 40 kHz**: con tasas cuyo Nyquist
  cae por debajo del techo de 20 kHz (16 kHz, 22.05 kHz, 32 kHz…) el cálculo
  de las 64 barras abortaba con `IndexError` y el analizador dejaba de
  actualizarse.
- **Fallo del proceso DSP**: los errores del hijo se registran con límite de
  frecuencia (1 aviso / 5 s con el total) para no inundar el log rotativo, y
  el watchdog ya detectaba y avisaba de su muerte (desde 1.6.0).

### Corregido — arranque y configuración
- **Un `config.json` corrupto ya no impide arrancar**: `latency_pref` con
  `Infinity`/`NaN` (que `json` acepta) lanzaba `OverflowError` desde
  `ConfigManager.load()` y reventaba la construcción de la ventana.
- **Los valores no finitos ya no caen en el máximo**: un `NaN` en volumen,
  bass o efectos se acotaba al **máximo** del rango (volumen 2x). Ahora se
  tratan como ausentes y cada campo usa su valor por defecto.

### Cambiado — plataforma
- **Python 3.13**: venv, empaquetado (`PyInstaller`) e instalador se compilan
  con 3.13; el CI prueba **3.12 y 3.13** en Windows y Linux.
- **CI con `constraints.txt`**: los jobs instalan exactamente las versiones
  que se empaquetan (`requirements-dev.txt -c constraints.txt`), con una sola
  fuente de verdad para las dependencias (se eliminaron los pins duplicados
  en el YAML, que impedían a Dependabot proponer subidas).
- `requires-python >=3.12`; `pyaudiowpatch` marcado `sys_platform == "win32"`
  (Linux instala el resto sin él).

### Infraestructura y pruebas
- **La suite ya no toca hardware de audio**: fixture `autouse` que bloquea
  `PyAudio`/`PyAudio.open` en toda la suite. El CI de Windows instalaba
  `pyaudiowpatch` y un test abría un dispositivo real (el runner no tiene):
  PortAudio tumbaba el job entero. Los 372 tests pasan con el hardware
  bloqueado, así que la suite es independiente del dispositivo.
- 13 tests nuevos (ring, espectro por tasa, config no finita, Q y
  auto-curación del DSP); cobertura ≥ 85 %.
- Log de métricas más claro: `fill`, `consigna` y `derr` (el error de deriva)
  en vez de `fill=N/consigna`, que parecía una avería permanente cuando el
  control estaba perfectamente asentado.

## [1.6.0] — rama `feat-enhancer-fx`

Paridad "enhancer" estilo FxSound, por fases.

### Fase 0 — Fundaciones (2026-10-10)
- **Capa enhancer**: `EnhancerParams`/`Enhancer` ganan `effects` (5 efectos
  0-10: fidelity, ambience, surround, dynamic_boost, bass), `balance_db`,
  `master_gain_db` y `leveling`; se replican al proceso hijo (`PARAM_ATTRS` +
  copia explícita del dict). Todos a 0 por defecto.
- **DC blocker** (~10 Hz, 1 polo) al inicio de la cadena: quita la componente
  continua (más headroom, menos trabajo del compresor/exciters). Coste medido:
  **+114 µs/bloque (~0.5 % de tiempo real)**. Interruptor interno
  `dc_block_enabled` (ON en producción) para aislarlo en los tests.
- **Presets v2 (dict)** con **migración automática v1→v2**: los presets viejos
  conservan su sonido (campos nuevos a 0). Saneo extendido y aplicado a config,
  import y export; `main_window` aplica/guarda/exporta v2.
- Helper `_onepole(block_sec, tau)` (alpha de 1 polo) para las etapas siguientes.
- Tests: DC blocker (ON por defecto, quita continua, transparente >1 kHz),
  migración v1→v2 (config y JSON importado), saneo v2, y suite completa verde.

### Fase 1 — Dynamic Boost + Leveling (2026-10-10)
- **Dynamic Boost** (`effects["dynamic_boost"]`, 0-10): compresor ASCENDENTE.
  Envolvente RMS por muestra (ataque 5 ms / release 350 ms, link estéreo) →
  `boost = k·(objetivo − env)` acotado a [0, +12] dB (objetivo −18 dBFS RMS).
  Sube los pasajes bajos sin tocar los picos.
- **Leveling** (`effects["leveling"]`, 0-10): AGC lento hacia −18 dBFS con
  corrección ±9 dB, colocado ANTES del volumen del usuario (normaliza el
  material, no el ajuste manual).
- Ambos a 0 por defecto (apagados = passthrough). Coste medido: ~30-70 µs por
  bloque cada uno (~0.2 % de tiempo real).
- **UI**: tarjetas "Refuerzo dinámico" y "Nivelación (volumen)" 0-10 con toggle;
  señales `dynamic_boost_changed`/`leveling_changed` en `AudioState`, cableadas
  al dict `effects` del motor; textos ES/EN (`explain`).
- **Tests**: apagado = passthrough; sube señal débil; señal fuerte intacta;
  ganancia estable (sin pumping); silencio sin NaN; conexión UI→motor.

### Fase 2 — Exciters Clarity y Bass (2026-10-10)
- **Clarity (Fidelity)** (`effects["fidelity"]`, 0-10): paso-alto 3 kHz →
  **2º armónico** (rectificar + paso-alto 20 Hz que quita la continua) → mezcla
  (`g = amount/10·0.5`). Restaura presencia sin subir esa banda de volumen
  (contenido NUEVO, no EQ).
- **Bass exciter** (`effects["bass"]`, 0-10): paso-bajo 100 Hz → 2º armónico →
  mezcla (`g = amount/10·0.6`). "Punch" en altavoces pequeños sin retumbar.
- **Corrección de diseño**: `banda·|banda|` NO genera 2º armónico (es impar:
  1º/3º). Se rectifica la banda y se quita la continua (verificado por FFT).
- **UI**: tarjetas "Claridad (Fidelity)" y "Exciter de graves" 0-10 con toggle;
  señales `fidelity_changed`/`bass_exciter_changed`; textos ES/EN.
- **Tests**: apagado = passthrough; 2º armónico medido por FFT (5k→10k y
  80→160 Hz) ×5; sin continua añadida; sin recorte nuevo con señal normal.

### Fase 3 — Ambience + Surround (2026-10-10)
- **Ambience** (`effects["ambience"]`, 0-10): 3 reflexiones tempranas por canal
  a retardos primos distintos (L 11/23/37 ms, R 13/27/41 ms) con ganancias
  decrecientes; historial O(n) (sin allocations por tap); mezcla `amount/10·0.3`.
- **Surround** (`effects["surround"]`, 0-10): ensanchado Mid/Side
  (`S *= 1 + amount/10·0.8`) SOLO por encima de 150 Hz (**mono-bass**: los
  graves quedan al centro) y con **guard de correlación** (si la señal ya es
  ancha se reduce; factor suavizado ~50 ms). La suma mono (L+R) se preserva
  exactamente.
- **UI**: tarjetas "Ambiente (sala)" y "Surround (anchura)" 0-10 con toggle;
  señales `ambience_changed`/`surround_changed`; textos ES/EN.
- **Tests**: apagado = passthrough; ambience sube energía y decorrelaciona L/R;
  surround preserva la suma mono; no afecta contenido mono; ensancha el Side;
  mono-bass protege los graves; guard reduce con señal muy ancha.

### Fase 4 — Balance + Master (2026-10-10)
- **Balance L/R** (`balance_db`, ±20 dB, ley equal-power): centro = unidad (sin
  cambio); a ±20 el canal opuesto queda mudo y el otro sube ~+3 dB. Suavizado
  (~50 ms) para no chasquear.
- **Master** (`master_gain_db`, ±20 dB): ganancia final con rampa POR MUESTRA
  (τ ~100 ms, anti-clic), colocada antes del limitador (el techo sigue siendo
  el último eslabón).
- **UI**: sliders "Balance L/R" y "Master (ganancia)" (sin toggle: 0 dB = no
  hace nada); señales `balance_changed`/`master_gain_changed`; textos ES/EN.
- **Tests**: 0 = passthrough; ±20 mutea un canal; +6 dB duplica y −6 dB reduce a
  la mitad; un salto del master no produce escalón (rampa por muestra).

### Fase 5 — EQ avanzado (2026-10-10)
- **Bandas dinámicas**: nº de bandas 5/9/10/15/31 (`EQ_BAND_SETS`; el de 9 es el
  histórico y el de 31 es ISO 1/3 de octava). `Enhancer.set_eq_band_count`.
- **Frecuencia central editable** por banda (`set_eq_band_freq`, acotada entre
  las vecinas para que la curva siga siendo monótona).
- **Q global** expuesto en la UI (0.2-10; `eq_q` ya existía en el DSP).
- **UI**: la curva pasa a N bandas con eje X logarítmico (20 Hz-20 kHz);
  arrastrar una banda cambia ganancia (vertical) y frecuencia (horizontal, con
  zona muerta de 4 px); combo de bandas + slider de Q; etiquetas adaptativas.
- **Presets v2**: `eq_bands` y `eq_q` (migración: los presets sin bandas usan el
  set por defecto del nº de ganancias; longitudes no soportadas se rechazan).
- **Config**: `eq_bands`/`eq_q` se persisten.
- **Tests**: redimensión de bandas, ganancias conservadas por índice, frecuencia
  acotada entre vecinas, EQ de 5 bandas filtrando, round-trip de params, presets
  con bandas/Q, longitudes no soportadas rechazadas y UI→motor.

### Fase 6 — Persistencia, presets incluidos y aviso (2026-10-10)
- **Config**: `effects` (5, 0-10), `balance_db`, `master_gain_db` y `leveling`
  se persisten y se sanean (solo claves conocidas, acotadas a rango).
- **Presets incluidos nuevos** (esquema v2): "Música/Cine/Juegos/Voz/Graves
  (enhancer)" con los efectos preajustados.
- **Aviso de recorte**: la página Efectos avisa cuando hay efectos activos y
  NINGUNA protección (ni limitador ni techo) → riesgo de recorte en el driver.
- **Corrección**: `leveling` se leía del dict `effects` pero vivía en un campo
  aparte (los presets no lo aplicaban al DSP); ahora es un único campo.
- **Tests**: config con efectos/balance/master/leveling (saneo y acotado),
  presets v1 y v2 válidos, y el aviso de recorte.

### Correcciones post-validacion y anti-cortes (2026-10-11)
- **EQ**: el preset ya no machaca el EQ configurado al arrancar o cambiar de
  tema (`set_preset` silencioso); se eliminan las zonas muertas de arrastre y
  los filtros no se resetean por snapshot.
- **Preset "Recomendado"**: leveling 2 (con 4 bajaba la musica fuerte y el
  volumen alto hacia que el techo aplastara los picos).
- **Rendimiento DSP**: filtros de 1 polo en numpy (cumsum) en vez de scipy
  lfilter; capa enhancer 4795 -> 1552 us/bloque (22.5% -> 7.3%).
- **Anti-cortes**:
  - Modo **"Estabilidad" (250 ms fijo)** en el selector de latencia.
  - **Pump sin descartes**: si la cola del hijo esta llena no se tira el bloque
    (el ring crudo de 1 s hace de reserva) -> `qdrop` queda en 0.
  - **Colas mas profundas**: hijo 8 -> 128 bloques (~2.7 s), salida 8 -> 32.
  - **Umbral de gracia 0.9 -> 0.5**: mas underruns se resuelven repitiendo
    material con crossfade en vez de insertar silencio.
  - **Boost adaptativo**: tras un corte real la consigna sube a 250 ms durante
    20 s y vuelve sola (la metrica `fill=X/Y` muestra la consigna efectiva).
- 323 tests verdes; ruff y mypy limpios.

## [1.5.14] - 2026-10-10

Diagnóstico de pipeline (Paso 0) + un bug real de arranque.

### Añadido (diagnóstico)
- **Contadores de pipeline en el log de métricas**: `salida` (frames que
  consume la salida), `rdrop`/`qdrop` (descartes silenciosos del ring crudo y
  de la cola al hijo), `qemp`/`oemp` (bomba ociosa), `dsg` (ajuste de deriva
  CON signo), `hijos`/`hblk`/`hclip` (hambre del hijo, bloques procesados y
  muestras que superan ±1.0 = posible recorte del driver).
- Con `capturados` vs `salida` + `dsg` se puede comprobar si el pipeline es
  sin pérdidas; `rdrop`/`qdrop`>0 delata descartes que antes eran invisibles.

### Corregido
- **Primer bloque del hijo lento (~10 s)**: la primera llamada a `process()`
  importaba `scipy.signal`/`scipy.ndimage` DENTRO del bucle de audio del hijo,
  retrasando el primer bloque varios segundos (microcortes al iniciar). Ahora
  se precargan al arrancar el hijo, antes del bucle.
- El test del hijo ya no se cuelga (usa `queue.Queue` en vez de `mp.Queue`) y
  precarga scipy.

## [1.5.13] - 2026-10-08

Control de deriva con más rango (arregla los episodios de cortes/lag con
relojes muy desajustados, p. ej. media app a velocidad distinta).

### Corregido
- **Autoridad de deriva adaptativa**: con error grande el control drena/rellena
  al doble de velocidad. El rango soportado pasa de **±1.5 % a ~±2.5-3 %** de
  desajuste de reloj (los episodios reales medidos eran ~1.9 %: el ring se
  sobrellenaba -> descartes + huecos + latencia creciente).
- **Anti-windup del acumulador de deriva**: un error grande sostenido ya no
  hace que al recuperar el ring se vacíe de golpe (hueco).
- **Ring 0.35 -> 0.5 s**: más margen para absorber ráfagas del reloj/dispositivo
  sin desbordar (especialmente a 100 ms de consigna, que sonaba peor por esto).

### Medido (simulacion de reloj)
- Skew de reloj soportado: **±1.5 % -> ±2.5-3 %** sin huecos ni descartes.
- Test de deriva ampliado a ±3 % (`tests/test_engine.py`).

## [1.5.12] - 2026-10-07

Optimización del DSP (menos CPU -> menos riesgo de cortes bajo carga).

### Cambiado
- **Caché de coeficientes biquad**: el EQ (bass/treble/9 bandas) ya no
  recalcula `_shelf`/`_peaking` en cada bloque; solo al mover un slider. En
  reposo, cero recálculo.
- **Medición de niveles en una sola pasada**: peak/rms de ambos canales con 2
  reducciones en vez de 4+ por canal.
- **Volumen en reposo sin rampa ni array**: con el volumen estable (p. ej.
  1.0) se multiplica por escalar (o no se toca).

### Medido (misma sesión, A/B)
- `process` con EQ+volumen: **1024 -> 475 us/bloque** (~2x más rápido, de
  ~4.8 % a ~2.2 % de tiempo real).
- Test de regresión de la caché (`tests/test_dsp.py`).

## [1.5.11] - 2026-10-07

La gráfica ya no puede degradar el audio (audio primero).

### Cambiado
- **El FFT del espectro sale del bucle de audio del hijo**: ahora lo calcula un
  hilo de visualización aparte, que si va lento se salta refrescos (la gráfica
  lag) sin retrasar nunca el DSP.
- **Solo se calcula cuando la gráfica está visible**: la bandera `needed` (UI)
  se propaga al hijo por memoria compartida; si nadie mira el espectro, el
  camino de audio no paga ningún coste visual.
- **Prioridad del hilo que alimenta la salida (bomba)**: subida a above-normal
  para que el repintado de la UI no lo adule y vacíe el ring.
- Tests nuevos del hilo visual (`tests/test_dsp_process.py`).

## [1.5.10] - 2026-10-07

Arregla el "se cambia muchísimo el audio" (modulación de tono).

### Corregido
- **El tono ya no se modula en los underruns parciales**: antes, si al ring le
  faltaba un poco para el bloque, el motor ESTIRABA el bloque (hasta −10 % de
  tono ≈ 1.8 semitonos, muy audible). Ahora rellena el hueco REPITIENDO el
  material disponible con un crossfade: mantiene el tono y evita el silencio.
- **Autoridad del control de deriva 24 → 16**: 24 corregía antes pero modulaba
  el tono (±2.3 %) durante las correcciones; 16 es el equilibrio (con 8 el ring
  se desbordaba).
- Nuevo contador `gracias` (rellenos por underrun parcial) en el log de métricas.

## [1.5.9] - 2026-10-07

Más robustez ante micro-cortes intermitentes bajo carga.

### Cambiado
- **Ring de salida 0.2 s → 0.35 s**: ~250 ms de margen por encima de la
  consigna (antes ~100 ms) para absorber ráfagas del reloj/dispositivo sin
  desbordar (descartes).
- **Autoridad del control de deriva 16 → 24 frames/bloque**: cubre desajustes
  de reloj mayores (CABLE vs. dispositivo físico llega a ~1.5 %); con 16 el
  control se saturaba y el ring se vaciaba o desbordaba.
- Tests del ring desacoplados de `RING_SECONDS` (antes fijaban 0.2 s a mano).

## [1.5.8] - 2026-10-04

Corregidas las "bajadas"/cortes de volumen al reproducir.

### Corregido
- **Silencios intermitentes ("bajadas brutales")**: el proceso hijo del DSP
  corría a prioridad *below-normal*; bajo carga de CPU se quedaba sin tiempo,
  el ring de salida se vaciaba y el callback insertaba silencio. Ahora el hijo
  corre en *above-normal*. Verificado en 4 núcleos saturados: de 639 huecos /
  ~643 000 frames de silencio por intervalo a 0-2 huecos y 0 descartes.

## [1.5.7] - 2026-10-04

Corregido el crash de arranque en Windows.

### Corregido
- **Crash `0xC0000005` en `pyside6.abi3.dll` al arrancar**: lo provocaba la
  rama Qt/PySide **6.11.x** (bug de Qt en Windows). Se fija **PySide6 6.8.3**
  (LTS) en `pyproject.toml` y `constraints.txt`. Verificado: 10/10 arranques
  sin crash.
- Warning `QThread::setPriority: Cannot set priority, thread is not running`:
  la prioridad baja del `SpectrumWorker` se fija ya en ejecución.

## [1.5.6] - 2026-10-04

Menos contencion del GIL: el audio tiene prioridad sobre el grafico.

### Cambios
- **Una sola bomba** entre el proceso principal y el DSP (antes dos hilos) y
  **prioridad baja** del proceso DSP y del `SpectrumWorker`: el trabajo grafico
  cede ante los callbacks de audio.
- Los medidores se actualizan solo en Inicio y el espectro a 20 Hz; con la
  ventana en bandeja no se actualizan graficos.

## [1.5.5] - 2026-10-03

Estabilidad de audio: se eliminan los descartes/microcortes y el lag, sobre
todo con el espectro a la vista.

### Corregido
- **Lagazo y descartes de audio**: el DSP competia por el GIL con el callback
  de salida de PortAudio y lo retrasaba. Ahora el DSP corre en un **proceso
  hijo** (su propio GIL); el FFT del espectro y los niveles tambien se calculan
  alli y se publican por memoria compartida.
- **Bucle de repintado** que saturaba el hilo de UI (`setStyleSheet` dentro de
  `paintEvent` del sidebar) y mataba de hambre al DSP.
- **Captura en rafagas**: el buffer de captura vuelve a 1024 (antes 4096, que
  hacia oscilar el ring 0..8192 y descartar).
- **Autoridad del resampler de deriva** 8 -> 16 frames/bloque para no desbordar
  el ring con el desajuste de reloj captura/salida.
- **Espectro/medidores congelados** al mover el DSP al hijo: se restauran
  publicando niveles/espectro desde el proceso hijo.
- **Onefile** crasheaba (`0xc0000005`) con el proceso hijo: se desactiva el hijo
  solo en el portable; el instalado/onedir lo mantiene.

### Rendimiento
- Espectro y curva de EQ vectorizados (numpy) y sin gradientes/antialias por
  frame; actualizaciones de medidores/espectro solo cuando su pagina esta a la
  vista; espectro a 20 Hz.

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
