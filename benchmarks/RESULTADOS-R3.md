# Resultados de la ronda R3 — higiene + rendimiento + arquitectura

Comparación **baseline (antes de R3, commit 183f9b3)** vs **final (HEAD de R3)**.
Método: `benchmarks/bench_dsp.py` (400 bloques medidos tras 30 de calentamiento,
seeds fijas, mismo hardware y sesión). Las cifras en µs por bloque de
1024 muestras @48 kHz (presupuesto por bloque: 21 333 µs).

## Camino de audio (callback de captura)

| escenario  | p50 antes | p50 después | Δ p50   | p95 antes | p95 después | alloc B/blk antes → después |
|------------|----------:|------------:|---------|----------:|------------:|-----------------------------|
| bypass     | 13.2      | 13.3        | ~0      | 19.3      | 17.6        | 35.9 → 35.9                 |
| eq_only    | 151.1     | 151.7       | ~0      | 166.5     | 164.8       | 54.9 → 53.5                 |
| full       | 206.0     | 158.3       | **−23.2%** | 228.5  | 172.5       | 56.7 → 53.5                 |
| full_hot   | 1585.8    | 1518.9      | −4.2% (ruido) | 1710.1 | 1656.4   | 163.0 → 165.1 (ruido)       |
| spectrum*  | 27.6      | 27.8        | ~0      | 38.8      | 35.8        | —                           |

\* `compute_spectrum` corre en el hilo de UI (no en el callback); su coste por
tick no cambió — lo que cambió es CUÁNDO se ejecuta (ver abajo).

Notas:
- `full` (música bajo umbrales, el caso común) mejora **−23%** por la salida
  temprana del compresor (R3-B4): cota segura `max(pico actual, estados zi)`
  bajo el umbral al cuadrado, con decaimiento exacto de estados.
- `full_hot` (peor caso: compresión + limitación true-peak activas) no cambia:
  es el trabajo legítimo del DSP (resample_poly ×4 ×2 pasadas). Fuera del
  alcance por decisión de la ronda (riesgo/coste no justificado).
- La rampa EQ in-place (R3-B5) elimina las allocations por bloque en el
  callback (verificado por identidad de buffers en el test).

## Hilo de UI (trabajo evitado, no medible con el bench de DSP)

| mejora | antes | después |
|--------|-------|---------|
| Dedupe de señales de nivel (R3-B1) | 2 emits + 2 repaints por tick (30 Hz) aunque el valor no cambiara | solo emite si delta ≥ umbral (RMS 0.001 / pico 0.002, sub-píxel) |
| SpectrumWorker adaptativo (R3-B2) | 30 FFT/s + 64 floats/tick SIEMPRE (bandeja, otra página, audio activo) | FFT solo con ventana visible + Home en pantalla; si no, sondeo 4 Hz sin FFT |
| Métricas del motor | sin cambios (ya era ~1 Hz) | sin cambios |

## Limpieza (R3-A)

- Eliminado: `device_state.py` (fósil CustomTkinter), señal `peak_level_changed`
  sin consumidores, `AudioState.update_levels_from_enhancer` duplicado,
  `_FREQ_SHORT`, docstring duplicado.
- Logging semántico: informativos de arranque/parada de warning → info.

## Arquitectura (R3-C)

| archivo antes | responsabilidad | después |
|---------------|-----------------|---------|
| main_window.py (1013 líneas, 7 responsabilidades) | motor + ruteo + config + presets + i18n + tema + tray | ventana solo pinta: reacciona a señales de `AudioController` y usa APIs públicas de páginas |
| — (nuevo) `audio_controller.py` | ciclo de vida del audio: tasa, prefill, recuperación de errores, ruteo puro `evaluate_route()` testeable sin Qt | |
| — (nuevo) `config_manager.py` | esquema + coerción + defaults + migración del config.json | |
| pages/*.py | recibían toques a privados desde la ventana | cada página expone su contrato público (señales + setters) |

`main_window.py` queda en 935 líneas (−8%) y **sin un solo acceso a atributos
privados de páginas**; toda la lógica de ruteo/config tiene tests puros.

## Estado de validación

- pytest: **140 passed, 9 skipped** (125 → 140: +14 nuevos, −3 del módulo borrado, +1 dedupe, +1 compresor, +1 EQ, +6 ruteo/controlador, +7 config manager, +1 spectrum)
- ruff check / ruff format --check: limpio
- py_compile del entrypoint: OK

## Cómo reproducir

```bash
python benchmarks/bench_dsp.py --json resultados.json
```
