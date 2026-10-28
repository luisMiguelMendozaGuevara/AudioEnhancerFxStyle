# Verificación de la iteración `feat/v1.7-diagnostics`

Fecha: 2026-10-26. Base: `main` @ `e9b155d` (v1.6.1).
Rama: `feat/v1.7-diagnostics`. Proceso: Organic Driven Development (semilla
medida → validar antes de aplicar → crecer sin romper → propiedad exclusiva de
archivos → poda de lo no verificable).

## 1. Línea base (antes de tocar nada)

| Gate | Resultado |
|---|---|
| `pytest -q` | **372 passed** |
| `ruff check .` | All checks passed |
| `mypy` | Success, 33 source files |
| `bench_dsp.py` | `full_hot` p95 2008 µs = 9,42 % del bloque; ningún escenario > 11 % |

Baseline de rendimiento guardado en JSON para comparación posterior.

## 2. Qué se ha implementado

| Commit | Contenido |
|---|---|
| `0588261` | `audio_enhancer/rt_priority.py` — MMCSS vía `avrt.dll` (`ProAudioThread`, `mmcss_pro_audio`, `elevate_thread_priority`, `describe`) + 11 tests |
| `b286906` | `audio_enhancer/diagnostics.py` — `RollingStats` (p50/p95/p99/max) y `LatencyBreakdown` + 17 tests |
| `2713aa0` | Cableado: `AudioEngine.latency_breakdown()`, `AudioEngine.timing_snapshot()`, publicación de tiempos desde el hijo por memoria compartida, MMCSS en ambos hilos, UI (barra de estado + diagnóstico exportado) + 9 tests de integración |
| `728290e` | `tests/test_audio_quality.py` — 9 tests de calidad objetiva del DSP |

## 3. Números medidos (no estimados)

### Calidad de audio (red de seguridad nueva)

| Medida | Valor real |
|---|---|
| THD+N del camino neutro (1 kHz, −6 dBFS) | **−151,3 dB** (suelo de float32) |
| EQ peaking +6 dB @ 1 kHz, Q=1 | **+5,92 dB** → error **0,08 dB** |
| True-peak de entrada (picos inter-muestra) | **+0,028 dBTP** |
| True-peak de salida con limitador | **−0,446 dBTP** (no supera el techo) |
| Pico con parámetros extremos (31 bandas +24 dB, Q=10, 5 efectos a 10) | **0,9301** ≤ 1,0, sin NaN/Inf |
| Retardo de grupo del camino neutro (200 Hz–2 kHz) | **0,09 muestras ≈ 0,002 ms** |

### Diagnóstico nuevo

| Medida | Valor real |
|---|---|
| MMCSS disponible en esta máquina | `{'platform':'nt','mmcss':True,'avrt_loaded':True}` |
| Registro real del hilo en MMCSS | `registered=True`, `task_index=9829` |
| Coste de `process()` por bloque (medido, bench) | p50 **385 µs**, p95 **620 µs**, peor caso **≈ 2,1 ms** sobre 21,3 ms |
| Desglose de latencia (ejemplo real, ring 60 ms + 3 bloques de cola) | `bloque 21.3 \| ring 60.0 \| cola hijo 64.0 \| salida 21.3 \| total 166.7 ms` |
| Barra de estado (texto real generado) | `unders 0 \| huecos 0 \| deriva 0 fr \| lat total 167 ms \| dsp p99 2.9 ms \| -16.4 LUFS` |
| Diagnóstico exportado (líneas nuevas) | `latencia=…`, `dsp=…`, `sonoridad M=… S=… I=… LUFS (LRA … LU)`, `rt={…}` |

## 4. Gates de verificación (después)

| Gate | Antes | Después | Veredicto |
|---|---|---|---|
| `pytest -q` | 372 | **463 passed** (+91) | ✅ |
| `ruff check .` | limpio | limpio | ✅ |
| `mypy` | 33 archivos | 35 archivos, limpio | ✅ |
| `py_compile` + imports de la app | OK | OK | ✅ |
| `bench_dsp.py --check` (presupuesto de bloque) | peor caso 10,0 % | **9,66 %** | ✅ |
| Arranque real de la UI sin pantalla (offscreen) | — | ventana + métricas + export de diagnóstico OK | ✅ |

### Sobre el `[FAIL]` del comparador de baseline

`bench_dsp.py --baseline` marcó `bypass: max 167,3 → 296,3 µs (+77 %)`. Es
**ruido de medición**, no regresión, con tres pruebas:

1. `p50` de `bypass` es **estable** en 73,9–74,0 µs frente a 74,5 µs del
   baseline (−0,8 %), y las allocaciones son idénticas (35,9 B/bloque).
2. Repitiendo el bench 3 veces el `max` oscila 167 → 214 → 294 → 296 µs en la
   misma máquina y el mismo código: es el *peor caso de una sola muestra* sobre
   un camino de 74 µs, dominado por el planificador del SO.
3. **`dsp.py` no se ha modificado en esta iteración** (el diff no lo toca).

Conclusión: el gate correcto para este repositorio es el presupuesto de bloque
(`%bloque(p95)`, que pasó con margen) más `p50`/`alloc`, no el `max` absoluto.

## 5. Hallazgos de los workers que NO se han arreglado (backlog con evidencia)

1. **`reset_state()` es asimétrico**: `_c_vol`, `_c_bass`, `_c_treble`, `_c_eq`
   y `blend` arrancan de su objetivo, pero `_c_master = 1.0` es constante y
   `_c_effects = default_effects()`/`_c_leveling = 0.0` también. Con master a
   −20 dB, un cambio de dispositivo o de tasa hace que el primer bloque salga
   **a 0 dB y baje en ~100 ms**: un pico de +20 dB, justo el escenario que el
   fix "F1" resolvió para el volumen.
2. **Margen del atajo de true-peak** en `_limit`: `sample_peak <= thr / 1.414`
   usa el máximo teórico de sobrepico justo, sin margen (`/1.5` costaría lo
   mismo y cubriría contenido real que se salte la medición).

## 6. Lo que queda fuera de esta iteración (y por qué)

- **AGC por sonoridad** (v1.8): el medidor LUFS ya está implementado y
  validado (ver §8); lo que falta es la decisión de producto de cambiar el
  comportamiento del Leveling, que altera cómo suena el material.
- **Captura por aplicación (Process Loopback)**: necesita PoC aparte con
  `comtypes` y validación de DRM/procesos elevados.
- **`IAudioClient3`**: PoC de 100 líneas antes de comprometerse; PortAudio no
  lo usa por diseño.
- **Firma de código / onedir**: bloqueado por credenciales, no por código.

## 7. Cómo revertir

```bat
git checkout main                 :: la iteración no toca main
git branch -D feat/v1.7-diagnostics
```


## 8. Segunda iteración: medidor LUFS (BS.1770 / EBU R 128)

Se añadió `audio_enhancer/loudness.py` y su validación contra **material de
conformidad oficial**, no contra sí mismo.

| Medida (EBU Tech 3341-2023, tabla 1) | Esperado | Medido |
|---|---|---|
| Caso 1 — seno estéreo 1 kHz, −23,0 dBFS pico/canal | −23,0 ±0,1 LUFS | **−22,993** |
| Caso 2 — igual a −33,0 dBFS | −33,0 ±0,1 | **−32,993** |
| Caso 3 — 10 s −36 / 60 s −23 / 10 s −36 (gate relativo) | −23,0 ±0,1 | **−23,014** |
| Caso 4 — añade 10 s a −72 dBFS (gate absoluto) | −23,0 ±0,1 | **−23,014** |
| Caso 5 — 20 s −26 / 20,1 s −20 / 20 s −26 | −23,0 ±0,1 | **−22,979** |
| Calibración §2.9 — −18 dBFS pico | −18,0 ±0,1 | **−17,99** |
| Coeficientes vs tabla del anexo 1 de BS.1770-4 | identidad | **diferencia 8,9e-16** |
| Ganancia del filtro K a 997 Hz | +0,691 dB | **+0,6910 dB** |

Coste medido: **0,246 ms por bloque de 1024** (1,15 % del presupuesto de
21,3 ms) y `snapshot()` ~0,7 ms con caché.

### 8.1 Ronda de auditoría (4 scopes independientes) y correcciones

Se enviaron cuatro revisiones en paralelo (normativa/DSP, tiempo real y
concurrencia, calidad de los tests, integración y UI). Encontraron **seis fallos
reales**, todos corregidos:

| # | Fallo | Origen | Corrección |
|---|---|---|---|
| 1 | El gate relativo del integrado se aplicaba sobre **todos** los bloques y podía reabrir el gate absoluto | mío | `pasa = pasa_abs & (niveles > umbral_rel)` en integrado y LRA |
| 2 | Un fallo del medidor de sonoridad **silenciaba el audio para siempre** (iba dentro del `try` que precede a la publicación) | mío | el bloque se publica primero; la medición va en su propio `try` con log limitado |
| 3 | `_sync_telemetry` leía la sonoridad **dos veces**, la segunda **fuera del lock** (código duplicado) | mío | eliminado el duplicado |
| 4 | `LoudnessMeter` sin lock: `list(deque)` podía lanzar en el hilo de UI (modo sin proceso hijo) | mío | `RLock` en `process`/`snapshot`/`reset` |
| 5 | `zi` con forma `(secciones, canales, orden)`: funcionaba con 2 canales **por coincidencia** y reventaba con 1 | mío | forma correcta `(secciones, 2, canales)` |
| 6 | El recálculo del integrado crecía con la duración de la sesión dentro del bucle de audio | mío | exacto con historial ≤10 min, 1 Hz a partir de ahí (EBU pide ≥1 Hz) |

Además: `try/finally` en los tres registros MMCSS, `try` global en la bomba,
prioridad `HIGH` para la bomba (no `CRITICAL`), LRA publicado (antes era código
inalcanzable en producción), contrato único `-1000.0 = sin medida` en ambos
caminos, limpieza de estado en `stop()`, y protección con `try/except` del tick
de métricas y del informe de diagnóstico.

**Un `NameError` real lo cazó un test nuevo** (`math` sin importar en
`main_window._fmt_lufs`): el informe de diagnóstico fallaba justo en el caso en
que se usa.

### 8.2 Tests reforzados (el scope 3 los llamó "teatro" en el cableado)

- Tolerancia del hijo bajada de ±0,5 a ±0,15 LUFS (la norma permite ±0,1).
- Nuevo test que ejecuta **`_dsp_loop_body` de verdad** (antes se podía borrar
  la medición o la publicación de audio y las 445 pruebas seguían verdes).
- Nuevo test de `_fmt_lufs` y del **informe de diagnóstico completo**, incluido
  el caso "motor roto" (el informe se escribe igual).
- `mmcss_available() is True`, orden de llamadas `set → priority → revert`,
  fallo de prioridad, `task_index` real y reversión ante excepción.
- Rutas de memoria acotada de `LoudnessMeter` (`max_blocks=8`), `hop_s=50 ms` y
  parámetros degenerados (que destaparon el fallo #5).
- Tolerancias de audio estrechadas a los valores teóricos (peaking 0,3 dB;
  shelf 4-6 dB; retardo de grupo 0-0,6 / 0,2-1,5 muestras).
- `try/finally` en los ayudantes que arrancan hilos (antes un fallo dejaba dos
  hilos zombis girando durante el resto de la sesión de tests).


## 9. Segunda ronda de auditoría (scopes 5 y 6) y cierre

Se enviaron dos revisiones más: **verificación de los 6 arreglos** y
**mutation thinking** (31 mutaciones plausibles, ¿las caza algún test?).

### 9.1 Fallos nuevos encontrados y corregidos

| # | Fallo | Corrección |
|---|---|---|
| 7 | El mismo patrón del fallo #2 seguía en el **padre**: `_sync_telemetry()` iba **antes** de `_put(data)` en la bomba y sin `try` → un fallo de instrumentación mataba el hilo y dejaba silencio | `_put(data)` primero; `_sync_telemetry()` en su propio `try` con `logger.debug` |
| 8 | `_local_dsp_ms.add()` dentro del `try` crítico del audio (diagnóstico antes que audio) | movido después de `_put`, con su propio `try` |
| 9 | `_stop_dsp_process()` salía antes si no había hijo → la limpieza de estado **nunca** corría en el modo local; y las bombas no se joinaban (carrera al limpiar) | limpieza movida a `stop()` (siempre) + join de las bombas |
| 10 | `integrated_lufs`/`loudness_range_lu` leían `self._cache[...]` **fuera** del lock → `KeyError` posible en un slot de Qt tras `reset()` | `.get(...)` con centinela |
| 11 | El medidor del **hijo** no se recreaba al cambiar de tasa en caliente (filtro K con coeficientes viejos) | se recrea al detectar el cambio |
| 12 | `telemetry`/`child_stats`/`gr_value` se leían/escribían sin protección de excepción (solo `child_timing` la tenía) | `contextlib.suppress` en los tres |

### 9.2 Mutaciones que sobrevivían y ahora mueren

De las 31 mutaciones, 13 sobrevivían. Se añadieron tests para las de mayor
riesgo:

| Mutación que sobrevivía | Test nuevo |
|---|---|
| Gate relativo invertido / gate absoluto eliminado | `test_el_gate_relativo_no_reabre_el_gate_absoluto` (programa a −65 con pasajes a −72) y `test_un_programa_entero_bajo_el_gate_absoluto_no_cuenta` |
| Pesos de canal surround mal (1,41 → 1,0) o aplicados dos veces | `test_pesos_de_canal_surround_valen_1_41` (compara Ls/Rs contra L/R con la misma señal: aísla el peso) |
| Percentil del LRA cambiado a máximo | `test_el_lra_usa_percentiles_y_no_el_maximo` (pico aislado de 1 s) |
| Quitar el `RLock` del medidor | `test_procesar_y_leer_desde_dos_hilos_no_lanza` (escritor + lector concurrentes) |
| Medidor de sonoridad dentro del `try` del audio | `test_un_medidor_roto_no_deja_el_audio_en_silencio` (medidor que lanza: el bloque debe llegar al ring) |
| Lectura de telemetría fuera del lock | `test_la_telemetria_se_lee_con_el_lock_tomado` (doble que falla si se lee sin lock) |
| Malla de 10 huecos sin LRA | `test_telemetria_de_diez_huecos_no_revienta` |
| Techo del limitador 0,95 → 0,99 | `test_el_techo_del_limitador_por_defecto_esta_fijado` (valor de producto + true-peak real) |

### 9.3 Estado final

| Gate | Resultado |
|---|---|
| `pytest -q` | **472 passed** en ~27 s |
| `ruff check .` | limpio |
| `mypy` | limpio (36 archivos) |
| `bench_dsp.py --check` | peor caso **8,6 %** del bloque (sin tocar `dsp.py`) |
| Coste del medidor LUFS | 0,246 ms/bloque (1,15 %) |
| Casos EBU 3341 | 1-5 pasan con margen ≤0,03 LU (tolerancia ±0,1) |

Pendiente asumido (documentado, no bloqueante): el recálculo del integrado
(1 Hz) sigue ocurriendo en el hilo de audio (≈0,3-1 ms una vez por segundo con
historial largo) — moverlo al hilo `viz` es la mejora natural. El callback de
PortAudio sigue sin instrumentar (se mide `Enhancer.process()`, no el callback).


## 10. Leveling por sonoridad (v1.8) y su auditoría

El AGC de nivelación dejó de medir RMS y mide **sonoridad LUFS** (filtro K de
ITU-R BS.1770, objetivo −18 LUFS, tope ±9 dB, control 0-10).

| Medida | Antes (RMS) | Ahora (LUFS) |
|---|---|---|
| Material a −26 LUFS con nivelación 10 | quedaba a ~11 LU del objetivo | **−18,3 LUFS** |
| Material a −20 / −12 LUFS | — | **−18,1 / −17,4 LUFS** |
| Mismo RMS, 60 Hz vs 2 kHz | misma corrección | **+8,9 dB vs +5,9 dB** |
| Primera nota tras 2 s de pausa | — | **0,0 dB** (sin ráfaga) |
| Coste | — | **+0,3-0,5 ms/bloque** solo con el efecto activo |

### 10.1 Lo que encontraron los dos scopes

**Defecto real (el más importante):** no había puerta ni cebado. La envolvente
lenta (release 3 s) tarda ~13 s en cruzar −70 LUFS tras un silencio, así que el
AGC se quedaba en +9 dB durante una pausa y **lo aplicaba al material que
volviera**: la primera nota entraba hasta 9 dB de más. Era, además, justo el
defecto contra el que el propio módulo avisa (`loudness_gain_db`).

Corregido en tres pasos (cada uno medido, no supuesto):

1. **Cebado** de 1 s de medida válida antes de actuar.
2. **Detector rápido de puerta** (25 ms + hold de 150 ms) para distinguir una
   pausa de material bajito, sin reiniciar el AGC en los huecos entre notas.
3. **Slew asimétrico**: la corrección sube despacio (0,5 s) y baja rápido
   (0,08 s) cuando entra material fuerte.

Resultado medido: con material callado el AGC está en **+8,98 dB**; tras 2 s de
silencio, **+0,00 dB**; la primera nota fuerte que vuelve recibe **0,0 dB**.

**Otros hallazgos aplicados:** el `clip` estaba duplicado (ahora se usa
`loudness_gain_db`, una sola fuente de verdad), el tooltip de la UI decía
"~−18 dBFS" (corregido a LUFS en ES/EN), el CHANGELOG se contradecía ("no
cambia el sonido" cuando sí), y el docstring del bench prometía una vigilancia
en CI que no existe (el bench es manual: los runners dan falsos positivos).

### 10.2 Tests: de 13/17 mutaciones supervivientes a contrato fijado

La primera batería solo demostraba la *dirección* del efecto. Se añadieron 11
tests que fijan el contrato numérico con **literales** (no leyendo las propias
constantes): objetivo −18 LUFS ±1, tope ≤9,5 dB, escalado de 0-10 (5 ≈ mitad de
10), envolvente lenta medida directamente, convergencia con compresor activo
(orden en la cadena) y con EQ activo (punto de medida), decaimiento en pausa,
ráfaga al reactivar, puerta rápida y hueco corto entre notas.

### 10.3 Estado

**494 tests** (empezó en 372) · ruff y mypy limpios · bench dentro del
presupuesto (`full_hot_leveling` 8,2 % del bloque).


## 11. Exciters (v1.8): 3er armónico en graves y anti-aliasing ADAA

### 11.1 Qué cambió y por qué

| Cambio | Motivo | Medido |
|---|---|---|
| Bass exciter genera **2º y 3º** armónico (banda hasta 120 Hz) | El 2º de una fundamental de 40 Hz cae en **80 Hz**, donde un altavoz pequeño no reproduce nada: el efecto era inaudible justo donde ayuda | 2F **−14,7 dB**, 3F **−22 a −27 dB** según la fundamental; fundamental intacta (±0,4 dB) |
| 3er armónico con **cúbico normalizado** `x³/mean(x²)` | `x·\|x\|` dejaba el 3F a −37 dB (inaudible) | 3F pasa de −37 a −23/−27 dB |
| **Potencia suavizada y por canal** para normalizar | Con la media del bloque: +6 dB de 3F en bloques medio llenos (modulación a 47 Hz), ±1,3 dB de rizo con un seno, +6 dB con bajo paneado | rizo entre bloques **0,19 dB** (antes 1,30) |
| Paso-alto del 3F a **4º orden** | Con 2º orden la fuga de fundamental de 60 Hz era −14,8 dB, a 2 dB del propio 3F | fuga **−27,1 dB** (dominancia 13,5 dB) |
| Clarity con **ADAA** en la no linealidad | El 4º armónico de un tono de 11 kHz se plegaba a 4 kHz; el paso-bajo posterior no lo quitaba (el aliasing ya había ocurrido) | alias/2F de **−13,8 a −31,6 dB** (17,8 dB mejor) |

### 11.2 Dos defectos de calibración que encontraron los scopes

1. **`ADAA_GAIN = 2.0` estaba calibrado en el peor punto.** El factor 0,516 se midió
   a 22 kHz, donde el ADAA atenúa casi al máximo. Medida la atenuación real por
   frecuencia del armónico (3 kHz 0,981 · 6 kHz 0,924 · 8 kHz 0,866 · 11 kHz
   0,872 · **22 kHz 0,516**), compensar ×2 dejaba Clarity **hasta +6 dB más
   caliente justo en la banda que se oye**. Recalibrado a **1,15** (+1,2 dB): el
   nivel cuadra dentro de ±1 dB en 6-16 kHz (+0,53 dB a 6 kHz).
2. **Normalización del cúbico por bloque** (ver tabla): corregida con potencia
   por canal y suavizada a ~150 ms.

### 11.3 Tests añadidos tras la auditoría (8)

Invariancia de nivel del 3F (amplitud 0,1/0,3/0,6 → dispersión < 1 dB),
estabilidad de la potencia entre bloques, HP3 como propiedad medida (fuga y
dominancia), **fase** del ADAA por correlación (los tests de magnitud no ven un
signo invertido), ADAA a −40 dBFS (nivel real de platos), limpieza completa de
estado al apagar cada exciter, y atenuación real del LP de Clarity (el test
preexistente era casi tautológico: 2 dB de margen).

### 11.4 Estado

**517 tests** (empezó en 372) · ruff y mypy limpios · bench con escenario nuevo
`full_enhancer` (antes los efectos iban a 0 y los exciters no se medían):
**10,2 % del bloque**. Coste de los dos exciters activos: **+0,9 ms/bloque**
(~4 % del presupuesto) en la máquina de desarrollo.

---

## 12. Interfaz de diagnóstico y PoC de captura por aplicación (2026-10-26)

### 12.1 El panel de métricas del Ecualizador

Las métricas del motor vivían en una sola línea de la barra inferior, donde se
cortaban con cualquier nombre de dispositivo y no cabía la unidad de cada cifra.
Ahora están en la página **Ecualizador**, en una rejilla 3×3 ordenada por tema:

| columna | campos |
|---|---|
| cortes | underruns, huecos, deriva |
| tiempos | latencia total, consigna del ring, DSP p99 |
| nivel | LUFS, LRA, frecuencia de muestreo |

Verificado con tests de widget (texto de cada campo, `--` cuando no hay medida o
el valor es el centinela `-1000` de LUFS, vuelta a `--` al parar el motor) y, para
el aspecto, con un render de la página a PNG.

La barra inferior se queda solo con el estado. Los avisos son temporales (6 s) y
después vuelven al estado: verificado con test (aviso → vuelve a "Activo") y con
el caso contrario (un cambio de estado cancela el aviso pendiente y un timeout
tardío no lo resucita).

### 12.2 Bug de Inicio reportado por el usuario

**Síntoma**: al cambiar el estado en Inicio aparecía "Detenido" aunque el audio
estuviera activo.

**Causa medida**: cambiar el tema (o el idioma) reconstruye las páginas; la nueva
`HomePage` nacía con el literal `"Detenido"` y el setter de `AudioState.processing`
**solo emite cuando el valor cambia**, así que no llegaba ninguna señal. Quedaba
el par contradictorio: botón `Detener audio` + texto `Detenido`. Reproducido por
script antes de tocar código (navegar no lo rompía; cambiar tema sí).

**Arreglo**: `HomePage.set_processing_state(active)` público e idempotente,
llamado al final del `build()` con el estado real; en `_rebuild_pages` una sola
fuente de verdad (`running or state.processing`) para Inicio, la barra y la
página Audio (esta última se quedaba en `--`).

### 12.3 PoC: Process Loopback (captura por aplicación)

Objetivo: dejar de depender de VB-CABLE capturando el audio de un proceso
concreto. Implementado en `audio_enhancer/process_loopback.py`, con `ctypes`
puro (sin `comtypes`).

**Estructuras comprobadas contra la cabecera de Windows** (no contra el propio
código): `AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS` 8 B (PID 0, modo 4),
`AUDIOCLIENT_ACTIVATION_PARAMS` 12 B (tipo 0, PID 4, modo 8), `PROPVARIANT` con
`VT_BLOB` 24 B (unión en 8) y `WAVEFORMATEX` 18 B empaquetado. Dos fallos cazados
así: `guid_de_texto` interpretaba `Data1` en little-endian (GUID con los bytes
invertidos) y la unión anidada aceptaba `params.TargetProcessId = 4242` sin
escribir nada (ctypes permite crear atributos sueltos en silencio).

**Hallazgo que bloqueaba la activación**: `ActivateAudioInterfaceAsync` devolvía
`E_ILLEGAL_METHOD_CALL` (0x8000000E). No era el apartamento (`CoInitializeEx` MTA
devolvía `S_OK`): instrumentando los callbacks se vio que Windows preguntaba por
`IID_IAgileObject` ({94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}) al handler de
finalización. Al no implementarlo, COM intentaba marshalling entre apartamentos y
la activación nunca llegaba a `ActivateCompleted`. Aceptarlo como marca
free-threaded en `QueryInterface` lo resuelve.

**Verificación real** (test marcado `slow`, se salta si no hay dispositivo): un
proceso aparte reproduce un tono de 1 kHz a -20 dBFS de pico y se captura con
Process Loopback.

| magnitud | esperado | medido |
|---|---|---|
| frames (2,5 s @ 48 kHz) | 120 000 | 120 000 |
| paquetes | — | 250 (0 silenciosos, 0 descartados) |
| pico | 0,1000 (-20,00 dBFS) | **0,1000 (-20,00 dBFS)** |
| frecuencia dominante | 1000 Hz | **1000,0 Hz** |
| RMS | -23,01 dBFS (seno de pico 0,1) | **-23,07 dBFS** |

**Integración en el motor (hecha)**: `AudioEngine.start_capture_process(pid,
rate)` llena el mismo ring crudo que el callback de PortAudio (`_put_raw` +
contadores), así que DSP, deriva y salida no cambian. Se rechaza si el endpoint
del proceso no está a la tasa del motor (meter 44,1 kHz en un ring que la salida
lee a 48 kHz desafinaría un 8 %). Verificado con un test que captura el tono del
proceso y comprueba que `fill()` crece y `captured_frames` avanza.

**Enumeración para el selector (hecha)**: `procesos_con_audio()` lee las
sesiones del endpoint por defecto (`IAudioSessionManager2` →
`IAudioSessionControl2::GetProcessId`) y resuelve el nombre con
`QueryFullProcessImageNameW`. Medido en la máquina de desarrollo:
`AudioEnhancerFxStyle.exe (PID 2900)` y `zen.exe (PID 15448)`; los procesos del
mismo PID se colapsan.

**Fallo cazado aquí**: al reescribir el módulo se perdió el bloque
`if __name__ == "__main__"`, así que `python -m audio_enhancer.process_loopback
--probe` salía con código 0 **sin imprimir nada**. Ahora hay dos tests que
ejecutan la CLI de verdad.

**Lo que falta para que sustituya a VB-CABLE**: el selector de aplicación en la
página Audio (elegir proceso en vez de cable) y capturar el árbol de procesos
para navegadores; y probarlo con DRM y procesos elevados (Netflix/Spotify/Steam),
que la investigación dejó como incógnita.

### 12.4 Estado

**564 tests** · ruff, format y mypy limpios · build 1.7.0 recompilado
(onedir 12,7 MB + instalador 61,6 MB) y verificado en arranque real
(`ring 48000 Hz, latencia 81.3 ms`).
