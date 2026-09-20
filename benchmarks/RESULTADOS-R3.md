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

## Decisiones explicitas (no revertir sin re-medir)

Optimizaciones evaluadas y DESCARTADAS con medicion (no volver a proponerlas):

- **SpectrumWorker a 25 Hz (en vez de 30)**: el espectro cuesta 0.82% del
  bloque (medido: spectrum p50 ~70-100 us vs bloque de 21.33 ms). Bajarlo a
  25 Hz ahorraria ~0.2% sin beneficio perceptual. No tocar.
- **Vectorizar `_cubic_hermite`**: la version vectorizada es ~30% MAS LENTA
  (el fancy-indexing crea temporales (N,2)). Mantener el bucle de 2 canales.
  Medido: bucle p50=163 us vs vectorizado p50=211 us.
- **Mascara en el `np.log10` del compresor**: el compresor completo cuesta
  ~200 us; la mascara anade overhead neto sin ahorro. No aplicar.
- **Ventanas deslizantes del limitador true-peak**: YA optimizadas a filtros
  O(n) de scipy.ndimage (maximum/minimum_filter1d). Medido: -44% en el
  limitador true-peak (4110 -> 2288 us p50) y -25% en full_hot. Equivalencia
  exacta con test de regresion.

## Gate de rendimiento (bench_dsp.py --check)

`--check` mide el PEOR caso (`max_us`), no el p95: un pico por encima del
bloque es lo que produce un microcorte real (underrun del ring). Referencia
con el umbral 100% del bloque (medido en este equipo):

    full_hot  max=23.1 ms (108% del bloque)  <-- UNICO escenario que excede

Es un hallazgo real (no un fallo del gate): el peor caso del DSP con EQ +
compresor + limitador supera el presupuesto de 21.33 ms y puede provocar
microcortes. Mitigacion actual: subir la latencia objetivo (40 -> 60/100 ms)
da mas margen al ring y/o apagar true-peak (la etapa mas cara).

## Microcortes: causa raiz encontrada y corregida (no era el DSP)

El gate por peor caso (max) detecto full_hot = 23.1 ms (108% del bloque).
Perfilado (cProfile) del escenario full_hot revelo la causa:

    resample_poly  ->  firwin (DISENO del filtro Kaiser) en CADA llamada
    400 llamadas de firwin en 200 bloques = 2 por bloque (entrada y salida)

No era el filtrado: era que scipy.signal.resample_poly RE-DISENA el FIR en
cada invocacion. Con la senal caliente hay 2 sobremuestreos/bloque y ese
diseno (ventana Kaiser sobre ~800 taps) es lo que disparaba picos de 20-30 ms
-> underrun del ring -> microcorte audible.

Correccion: `_true_peak_oversample()` construye el MISMO filtro (n=10*max_rate,
kaiser 5.0) UNA vez por tasa y aplica con `upfirdn` (bit a bit identico a
resample_poly, verificado con test). Ademas, el 2o sobremuestreo de la
garantia de techo solo se hace si el sample-peak de salida > thr/1.414.

Resultado medido (mismo equipo):

    escenario             antes (max)   despues (max)
    full_hot              23081 us      3787 us    (-84%)
    full_hot_sin_limiter   9799 us      4367 us
    eq_only               12052 us       987 us
    p50 full_hot           3359 us      1546 us

Gate: [OK] todos los escenarios <= 100% del bloque (peor caso). Sin microcortes
por presupuesto en el DSP.
