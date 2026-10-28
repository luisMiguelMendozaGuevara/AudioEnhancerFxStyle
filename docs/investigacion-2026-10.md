# Análisis técnico + investigación de mejoras (octubre 2026)

Documento de trabajo. Base: análisis del grafo de código del proyecto (índice
`codebase-memory`, 1542 nodos / 5949 aristas, versión 1.6.1) y tres
investigaciones web independientes:

1. **Estado del arte en DSP de audio de consumo** (algoritmos).
2. **Competencia en Windows** (FxSound, Equalizer APO, Voicemeeter, Sonar, Boom 3D, Dolby/DTS, Sonarworks, EasyEffects…).
3. **Mejoras de ingeniería** (latencia, arquitectura, distribución, calidad).

> Nota de método: el buscador Google del entorno no tenía credenciales, así que
> la evidencia se obtuvo por **fetch directo a fuentes primarias** (Microsoft
> Learn, ffmpeg, FabFilter, SciPy, GitHub API de FxSound, VB-Audio, ITU/EBU).
> Lo no confirmado se marca explícitamente como *no verificado*.

---

## 1. Radiografía del proyecto (grafo de código)

| Dimensión | Dato |
|---|---|
| Código de app | ~10.200 líneas Python en 29 archivos |
| Tests | ~5.550 líneas pytest, 385 aristas `TESTS` |
| Núcleo DSP | `audio_enhancer/dsp.py` (1634 líneas) — **hotspot nº1**, `Enhancer.process` con fan-in 48 |
| Motor | `audio_enhancer/engine.py` (985) — ring buffer, deriva, proceso hijo |
| UI | `audio_enhancer/ui/new/main_window.py` (1439) — `NewMainWindow._t` con fan-in 37 |
| Ciclos de llamadas | **1** (`AudioController.start → AudioEngine.start_capture → _start_dsp_process`): es composición, no un ciclo problemático |
| Capas | `dsp` (fan-out 31) y `ui` (fan-out 28) son los dos ejes; `audio_controller → engine` (12) es el acoplamiento real entre capas |
| Dependencias externas | numpy, scipy, PySide6 (<6.9), PyAudioWPatch (solo Windows) |

**Lectura:** la arquitectura está sana (un solo ciclo, y es de arranque). Los
dos riesgos estructurales no son de acoplamiento sino de **concentración**:
`dsp.py` y `main_window.py` acumulan demasiada responsabilidad, y `_t` (i18n)
es el símbolo más llamado de la UI, señal de que la traducción está cableada en
el código de vista en lugar de resuelta en los datos.

**Deuda funcional detectada en el propio código/README**

- El *Leveling* es un **AGC por RMS**, no por sonoridad percibida (LUFS).
- El *Bass Exciter* genera **solo el 2.º armónico por debajo de 100 Hz**: cae en
  80 Hz para una fundamental de 40 Hz, es decir, exactamente donde los
  altavoces pequeños no reproducen nada. Es el candidato nº1 a rediseñar.
- Los *exciters* y el waveshaping **no tienen oversampling ni ADAA**: el
  aliasing es el defecto técnico más objetivo de la cadena actual.
- El limitador usa true-peak **4×**; según la tabla de sublectura de ITU-R
  BS.1770 el 4× puede quedarse corto frente a 8×.
- Latencia: `CHUNK=1024` (21,3 ms) + setpoint del ring (60 ms por defecto) +
  salto de proceso = **60–120 ms**. Ningún APO del mercado pierde tanto.
- Dependencia de **VB-CABLE** y `.exe` sin firmar.

---

## 2. Lo que cambia el mapa: FxSound ya es gratis y open source

El competidor de referencia **dejó de ser de pago en 2022 y es AGPLv3 desde
2023** (C++/JUCE, builds x86/x64/ARM64, ~281.000 descargas de un solo asset en
la última release). Consecuencia directa:

> "Gratis, open source y sin telemetría" **ya no es diferenciador**. La
> propuesta de valor debe moverse a **capacidades técnicas + ausencia de
> lock-in + transparencia**.

Y hay una asimetría de licencia a favor: **MIT es la licencia más permisiva del
mercado de esta categoría** (FxSound AGPLv3, Equalizer APO GPLv2, EasyEffects
GPLv3, JamesDSP GPLv3). Permite que el DSP se reutilice en productos cerrados:
es una ventaja de ecosistema, no solo legal.

**Los cinco "no" de FxSound** (verificados en su repo y en su guía oficial de
27 pasos de troubleshooting): no crossfeed, no VST, no convolución, no
procesado por aplicación, no A/B ni medidores de reducción de ganancia. Eso es
la portada del proyecto.

**Quejas recurrentes del líder** (issues #29, #37, #40, #192, #221, #610):
cortes/pops, ~1 s de retardo en algunos equipos, crashes con Bluetooth al
conectar/desconectar, pérdida de dispositivo al despertar de suspensión, y
**opacidad** ("¿qué hacen exactamente Clarity, Dynamic Boost y Bass Boost?").

---

## 3. Huecos explotables, ordenados por (impacto × viabilidad)

| # | Hueco | Quién lo tiene hoy | Coste | Nota |
|---|---|---|---|---|
| 1 | **Captura por aplicación** (Process Loopback API) → sin VB-CABLE | Nadie en user-mode; FxSound lo declaró "out of scope" | M-L | Rompe el lazo de eco **por construcción** |
| 2 | **Perfiles de auricular AutoEQ importables** | Boom 3D (5000 modelos), Sonarworks (USD 99) | S | Solo parsing + selector de modelo |
| 3 | **Latencia declarada y medida < 30 ms** | Todos los APO ganan hoy | M | Nadie publica mediciones con metodología |
| 4 | **Leveling por LUFS** + medición de loudness en UI | Nadie en esta categoría de consumo | M | Cambia la percepción de "suena mejor" |
| 5 | **Transparencia**: curva de respuesta resultante, cadena documentada, desglose de latencia | Nadie | S | Es la petición literal del issue #610 |
| 6 | Convolución / corrección de sala ligera | Equalizer APO, EasyEffects, Sonarworks | M | Encaja en el presupuesto de latencia actual |
| 7 | Canal de micrófono (gate/EQ/compresor) | APO, Sonar, Blue VO!CE | M | Mismo usuario, DSP ya escrito |
| 8 | Instalación sin fricción: firma + WinGet/Scoop + asistente | FxSound (instalador firmado con driver) | S | Principal causa de abandono |
| 9 | Accesibilidad (teclado, lector de pantalla, alto contraste) | Prácticamente nadie | S | Barato, reputacional, encaja con el caso "prótesis auditiva" |
| 10 | Multicanal 5.1/7.1 | FxSound (2–8 ch), APO (8 ch) | L | Mejor **no** atacarlo: posicionarse en estéreo/auriculares |

---

## 4. Mejoras de DSP recomendadas (orden impacto/coste)

1. **Leveling → AGC por LUFS short-term (3 s)** con K-weighting (2 biquads),
   sub-bloques de 100 ms y controlador lento (τ 2–5 s) con hold. Referencia
   normativa: ITU-R BS.1770-5 (K-weighting, gating 400 ms/75%, true-peak);
   EBU R 128-2023 (−23 LUFS, −1 dBTP); EBU R 128 s2 (streaming: −20 a −16 LUFS).
   `libebur128` (MIT) expone funciones específicas de monitorización en tiempo
   real; `pyloudnorm` sirve como oráculo de test.
2. **Medidor LUFS-M/S + true-peak en UI** y A/B con *loudness match*.
3. **Rediseñar el Bass Exciter**: 2.º **y** 3.º armónico en 150–400 Hz, fase
   alineada, mezcla controlada y envolvente de la fundamental. El 2.º armónico
   sub-100 Hz actual es un error de octava audible en auriculares y nulo en
   altavoces pequeños.
4. **True-peak a 8×** en el limitador (el 4× puede subestimar el pico real).
5. **De-esser de banda dividida** (5–9 kHz) después de *Clarity*: el exciter de
   presencia es el que más sibilancias añade.
6. **EQ dinámico por banda** reutilizando los biquads de la cascada SOS
   (threshold/ratio/attack/release por banda, modelo `adynamicequalizer`).
7. **Anti-aliasing en el camino no lineal**: oversampling 2×/4× polifásico o
   ADAA. Es la práctica estándar en las implementaciones de referencia
   (`asoftclip` de ffmpeg expone `oversample` precisamente por esto).
8. **Transient shaper** (envolventes fast/slow → attack/sustain) antes del
   compresor.
9. **Medidor de correlación/fase + auto-narrow con histéresis**: el guard
   actual ya controla, falta **exponer la medida** (el usuario no entiende por
   qué el widening desaparece en mono).
10. **Compresor multibanda 2–3 vías** con crossovers Linkwitz-Riley: alto
    impacto, pero hacerlo **solo después** del EQ dinámico y medido.

**Descartado con evidencia**

- **EQ de fase lineal**: FabFilter documenta 3072–66560 muestras de latencia
  (70–1509 ms a 44,1 kHz) y el pre-ringing degrada los transitorios. Duplicaría
  o triplicaría la latencia actual. El estado del arte real no es fase lineal
  sino *aproximar la fase analógica* (que es lo que ya hace la cascada SOS).
- **HRTF/SOFA por convolución**: coste CPU comparable a toda la cadena actual,
  dependencia de dataset, y no mejora música estéreo genérica. BS2B ya cubre el
  90 % del beneficio con un biquad.
- **Declipper AR y denoise espectral en el lazo de playback**: coste alto,
  lookahead de decenas de ms, riesgo de artefactos; el limitador ya evita el
  clipping propio.
- **Modelos neuronales en el camino de audio** (RNNoise, DeepFilterNet) son de
  **voz**, no de música; **separación de fuentes** (Demucs) va a ~1,5× tiempo
  real en CPU. Solo tendrían sentido como *modo voz* separado o como acción
  offline ("exportar stems").

**Rendimiento:** el presupuesto es 21,3 ms/bloque. `scipy.signal.sosfilt` con
`zi` es la elección correcta (DF2T, una sola llamada C para 31 secciones). El
enemigo no es el cómputo, es el **overhead por llamada y los bucles Python por
muestra**. Antes de plantear numba/Rust: medir con `benchmarks/bench_dsp.py`.

---

## 5. Mejoras de ingeniería

### 5.1 Latencia (la palanca grande)

- **`IAudioClient3` + `InitializeSharedAudioStream`**: PortAudio pide el IID de
  `IAudioClient3` en Windows 10+ pero **nunca llama a `InitializeSharedAudioStream`**;
  en modo shared usa `IAudioClient::Initialize` y su propio comentario dice que
  la latencia shared "no puede ser inferior a 20 ms". Es decir: **con
  PyAudioWPatch no se puede alcanzar el low-latency shared mode**. Microsoft
  documenta periodo de motor 1,3 ms y buffers de 128–480 frames (2,67–10 ms).
  → PoC con `comtypes` que solo reporte el periodo concedido, y mantener
  PortAudio como fallback si llega `AUDCLNT_E_ENGINE_PERIODICITY_LOCKED`.
- **El proceso hijo no cuesta IPC, cuesta bloques**: el coste por bloque es de
  decenas de µs (despreciable), pero el diseño retiene 1–2 bloques. Bajar
  `CHUNK` a 256–512 + shared memory + colchón de 1 bloque recorta más que
  cualquier microoptimización.
- **Setpoint adaptativo del ring** (30 → 15–20 ms cuando el xrun rate es 0
  durante N segundos, con histéresis), en lugar de un colchón fijo.

### 5.2 Arquitectura: salir de VB-CABLE

- **APO propio y driver virtual propio: descartar.** Un APO se instala como
  parte del paquete de driver (INF), el test WHQL "Required Driver Attributes"
  de 24H2 espera un `.sys` que un APO no tiene, y la firma por attestation
  exige cuenta de Partner Center con certificado EV. Inviable para un proyecto
  Python MIT.
- **Process Loopback API (Windows 10 2004+): hacer.** `ActivateAudioInterfaceAsync`
  + `AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS` captura solo el árbol de procesos
  indicado y **no está atado a un endpoint**, lo que elimina el cable virtual
  por diseño. Existe envoltorio Python (`process-audio-capture`, MIT-verificar).
  Riesgos a probar: DRM/contenido protegido, apps elevadas, launchers que
  cambian de PID.
- **Guard anti-eco → detección de lazo activa** con mensaje accionable y
  auto-silenciado reactivo (un guard estático no protege si el usuario cambia
  el dispositivo por defecto con la app corriendo).

### 5.3 Robustez en tiempo real (barato y con evidencia)

- **MMCSS "Pro Audio"** (`AvSetMmThreadCharacteristicsW` + `AVRT_PRIORITY_CRITICAL`)
  en el hilo DSP del proceso hijo. PortAudio ya lo hace en sus callbacks, pero
  el proceso hijo no. Coste S.
- **Contadores de xrun/glitch + p99 del callback** expuestos en Ajustes y log.
- **Cero allocs en el camino caliente**: buffers preasignados, `gc.disable()` /
  `gc.freeze()` tras arrancar, nada de `logging` en el callback (solo
  contadores), y un test que verifique que `sys.getallocatedblocks()` no crece.

### 5.4 Distribución

- **Firma de código**: desde 2024 **EV ya no evita SmartScreen**; el plan
  barato es el correcto. **SignPath Foundation firma gratis proyectos OSS**
  (encaje perfecto: MIT, Windows). Alternativa: Azure Artifact Signing
  ~9,99 USD/mes (limitación geográfica: individuos solo EE. UU./Canadá). OV
  150–300 USD/año; EV 400+.
- **onedir como distribución principal**: `--onefile` extrae a `%TEMP%\_MEIxxxxxx`
  en cada arranque → arranque más lento y heurísticas de antivirus más agresivas.
- **Matriz de falsos positivos** (VirusTotal en CI, disputas), sin UPX.
- **Actualizaciones automáticas**: WinSparkle 0.9.4 sigue vivo y soporta firmas
  EdDSA; alternativa sin dependencias: consultar GitHub Releases y verificar
  SHA-256 + Authenticode.
- **VB-CABLE es empaquetable legalmente**: VB-Audio permite distribuir y
  embeber el paquete con instalación silenciosa **si** se respeta el modelo
  donationware (atribución visible, enlace de donación) y **no** se empaquetan
  las variantes A+B / C+D. Formalizar con `THIRD_PARTY_NOTICES.md`.

### 5.5 Calidad

- Tests de calidad objetiva (hoy no existe ninguno que diga "el audio suena
  igual"): THD+N y SNR del camino en bypass, respuesta en frecuencia por preset
  contra la curva teórica (±0,5 dB), **latencia loopback medida** por
  correlación cruzada, y true-peak real del limitador.
- **Golden files espectrales** (hash del espectro log-magnitud 1/3 octava con
  tolerancia ±0,2 dB) en lugar de comparación de bits: inmune al redondeo,
  sensible a cambios reales de DSP.
- **Fuzzing con `hypothesis`** sobre Q, Fc, ganancias, tasas 44,1/48/96/192 kHz
  y canales; incluyendo `config.json` corrupto (los presets son entrada no
  confiable).

---

## 6. Roadmap propuesto

> El detalle de la segunda mitad de la v1.8 (true-peak, de-esser e
> importación de AutoEQ), con criterios de aceptación medibles, está en
> `docs/diseno-v1.8.md`.


**v1.7 — "medir y no romper"** (riesgo bajo, todo verificable)
1. Desglose de latencia instrumentado (captura / ring / cola del hijo / render) visible en la **barra de estado** y en el informe de diagnóstico exportado.
2. Contadores de xrun + p99 del **coste de `Enhancer.process()` por bloque** (el callback de PortAudio sigue sin instrumentar: es backlog).
3. MMCSS "Pro Audio" en el hilo DSP.
4. Tests de calidad objetiva + golden espectrales.
5. Medidor LUFS-M/S/I + LRA en UI (solo medición, sin cambiar el AGC). El true-peak en UI queda pendiente.
6. Curva de respuesta resultante dibujada en la UI.
7. Firma con SignPath + onedir como distribución principal.

**v1.8 — "sonar mejor"**
1. Leveling por LUFS con K-weighting y controlador lento.
2. Rediseño del Bass Exciter (2.º+3.º armónico, 150–400 Hz).
3. True-peak 8× y de-esser tras Clarity.
4. Anti-aliasing (oversampling/ADAA) en el camino no lineal.
5. Importación de perfiles AutoEQ + selector por dispositivo.

**v1.9 — "sin cable y sin latencia"**
1. PoC y luego beta de captura por aplicación (Process Loopback) → VB-CABLE opcional.
2. `CHUNK` reducible + shared memory + setpoint adaptativo.
3. PoC `IAudioClient3` propio (solo para usuarios con driver compatible).
4. EQ dinámico por banda y transient shaper.

**Backlog / evaluar**
Compresor multibanda, convolución ligera, canal de micrófono, Auto-presets con
YAMNet ONNX (solo sugerencia, nunca automático), accesibilidad, i18n ampliada.

---

## 7. Incertidumbres: estado a 2026-10-28

Cada punto de la lista original lleva ahora su veredicto y la evidencia que lo
sostiene. Lo que sigue sin verificar se dice como tal, sin adornos.

### 7.1 Periodo de motor real concedido por chipset — **medido en 1 máquina**

`benchmarks/probe_wasapi.py` (nuevo, reproducible) mide `GetDevicePeriod` y
`IAudioClient3::GetSharedModeEnginePeriod` del endpoint por defecto. En la
máquina de desarrollo (Windows 10 22H2, build 19045, Realtek/Synaptics):

| medida | valor |
|---|---|
| `IAudioClient3` disponible | sí |
| formato de mezcla | float32, 2 canales, 48 kHz |
| `GetDevicePeriod` | default 10,00 ms · mínimo 2,00 ms |
| motor (compartido) default | 480 frames = **10,00 ms** |
| motor fundamental | 1 frame = **0,02 ms** |
| motor mínimo | 128 frames = **2,67 ms** |
| motor máximo | 480 frames = **10,00 ms** |

Conclusión: el suelo de latencia en modo compartido con `IAudioClient3` es
**2,67 ms** en esta máquina, la mitad del periodo actual por defecto (10 ms) y
muy por debajo del bloque de 21,3 ms que usa la app. **Sigue abierto para otros
chipsets**: la herramienta ya existe, falta ejecutarla en 2-3 equipos más.

### 7.2 Soporte de `IAudioClient3` en `soundcard`/`miniaudio`/`RtAudio` — **no aplica**

La app no usa ninguna de esas tres librerías: los streams van por PyAudioWPatch
(PortAudio) y el WASAPI nativo se hace con `ctypes` propio
(`audio_enhancer/process_loopback.py`). Medido: `IAudioClient3` se activa por
`QueryInterface` sin problema, así que si se aborda la latencia baja no hace
falta auditar librerías de terceros: el cliente ya es nuestro.

### 7.3 Mantenimiento de `tufup` y `pytest-regressions` — **verificado (PyPI)**

Ninguno de los dos es dependencia del proyecto; eran candidatos.

| paquete | versión | última publicación | licencia |
|---|---|---|---|
| `tufup` | 0.10.0 | 2025-10-04 | MIT |
| `pytest-regressions` | 2.11.0 | 2026-05-25 | MIT |

Ambos con publicación reciente y licencia permisiva: ninguno está abandonado.

### 7.4 Process Loopback con DRM y con procesos elevados — **verificado a medias**

- **Funciona con aplicaciones normales**: capturado un tono de 1 kHz a −20 dBFS
  de un proceso aparte, pico 0,1000 (−20,00 dBFS) y 1000,0 Hz, y el motor lo
  consume (`fill()` > 0). También se enumeran los procesos que suenan
  (`--listar`): en la máquina de desarrollo apareció la propia app y el navegador
  del usuario, con la sesión en el proceso principal.
- **Pendiente DRM**: requiere poner Netflix/Spotify a sonar; si devuelve silencio
  (pico 0,0000) hay que decirlo en la interfaz, no arreglarlo.
- **Pendiente procesos elevados**: el shell de desarrollo **no es administrador**,
  así que no se pudo probar la activación sobre un proceso elevado. El modo de
  fallo esperado es `acceso denegado`; hay que medirlo ejecutando la app como
  administrador.

### 7.5 Licencia de PySide6/Qt y suficiencia del onedir — **verificado, con un hueco**

- `PySide6` 6.8.3 se declara `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`
  (classifier LGPLv3). La opción LGPLv3 es la que aplica al distribuir.
- **El onedir sí es suficiente**: los 13 `Qt6*.dll` viajan como archivos
  separados en `_internal/PySide6/`, así que el usuario puede reemplazarlos
  (LGPL §4, requisito de reenlazado). El **onefile no** vale como distribución
  principal: los extrae a una carpeta temporal y el reemplazo deja de ser
  práctico.
- **Hueco real detectado**: el repo es MIT y **no incluye el texto LGPLv3 ni
  avisos de terceros**, y el instalador no tiene `LicenseFile`. El wheel de
  PySide6 tampoco trae el texto (no hay `LICENSES/`). Hay que añadir
  `LICENSES/LGPL-3.0.txt` + avisos de terceros y engancharlos al instalador.
  La v1.7.0 publicada sale sin ese aviso: corregir en la siguiente.

### 7.6 Coste real del salto de proceso en µs/bloque — **sin medir**

Sigue siendo estimación. El método correcto ya está claro: medir con el bench el
mismo bloque de DSP (a) en el hilo local y (b) a través de la memoria compartida
con el proceso hijo, y restar. La telemetría necesaria existe
(`child_timing`/`timing_snapshot`), falta el escenario en `benchmarks/`.

### 7.7 Precios de DTS Sound Unbound y Razer THX — **no verificable aquí**

La búsqueda web del entorno no tiene credenciales, y el precio cambia con
ofertas y región. Además ninguna decisión técnica depende de ese dato: era
contexto de competencia.

### 7.8 Licencia de los datos de AutoEQ — **verificado (MIT), con matiz**

El repositorio `jaakkopasanen/AutoEq` declara **MIT**, lo que permite
redistribuir código y resultados con atribución. **Matiz importante**: las
mediciones crudas que AutoEQ procesa vienen de terceros (oratory1990, crinacle,
Rtings…), y la licencia de cada conjunto de medidas puede no ser MIT. Antes de
empaquetar perfiles hay que revisar el origen de cada medición incluida, no solo
la licencia del repositorio.
