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
| Barra de estado (texto real generado) | `unders 0 \| huecos 0 \| deriva 0 fr \| lat 167 ms \| dsp p99 0.0 ms` |
| Diagnóstico exportado (líneas nuevas) | `latencia=bloque 21.3 \| ring 60.0 \| ...`, `dsp=p50 ... p99 ... max ...` |

## 4. Gates de verificación (después)

| Gate | Antes | Después | Veredicto |
|---|---|---|---|
| `pytest -q` | 372 | **419 passed** (+47) | ✅ |
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

- **Medidor LUFS / AGC por sonoridad** (v1.8): requiere decisión de producto
  sobre el comportamiento del Leveling, no es solo instrumentación.
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
