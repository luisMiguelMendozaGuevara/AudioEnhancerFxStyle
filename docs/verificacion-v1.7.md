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
