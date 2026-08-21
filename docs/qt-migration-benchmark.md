# Benchmark experimental: CustomTkinter vs PySide6

Fecha de medición: 2026-08-19 (Windows, Python 3.11.15, venv del proyecto).

## Alcance

Se ejecutaron tres procesos independientes por toolkit con:

- audio real **no iniciado**;
- snapshot fijo de 64 barras;
- scroll sintético de 100 operaciones;
- ventana mostrada y contenido construido mediante el event loop real;
- enumeración WASAPI ejecutada en segundo plano.

Las métricas de CPU/RAM no se registraron porque `psutil` no está disponible de forma importable desde el intérprete usado por el benchmark. Se conservan como `null`; no son estimaciones.

## Medianas de tres ejecuciones

| Métrica | CustomTkinter | PySide6 |
| --- | ---: | ---: |
| Shell desde raíz (ms) | 48.97 | 25.09 |
| Primer paint desde raíz (ms) | 49.73 | 27.10 |
| UI lista desde raíz (ms) | 486.54 | 48.36 |
| Dispositivos listos desde raíz (ms) | 1,293.73 | 258.77 |
| Spectrum FPS sintético | 5.79 | 23.09 |
| Latencia máxima de operación scroll sintético (ms) | 0.011 | 0.622 |
| CPU en reposo | no disponible | no disponible |
| CPU con audio | no ejecutado | no ejecutado |
| CPU con audio + scroll | no disponible | no disponible |
| RAM | no disponible | no disponible |

## Interpretación

- PySide6 mostró menor tiempo medido hasta la UI completa y hasta la enumeración de dispositivos en estas tres ejecuciones.
- El spectrum Qt se pinta con un solo `QWidget` y `QPainter`; la cadencia observada se acercó a 23 FPS con una actualización objetivo de 30 FPS.
- La latencia de la llamada sintética `QScrollArea` fue mayor que la llamada Canvas equivalente, pero esta cifra no representa por sí sola la percepción de scroll del usuario ni el frame pacing completo.
- No se puede concluir todavía que PySide6 elimine microcortes durante audio: faltan las pruebas críticas con WASAPI activo, spectrum activo, scroll rápido, sliders y redimensionado.

## Estado de la decisión

**RECOMENDADO MIGRAR GRADUALMENTE A PRUEBA CONTROLADA**, no reemplazar aún la UI original.

La evidencia inicial favorece PySide6 en arranque y representación del spectrum, pero la decisión definitiva requiere validar la ruta de audio real y CPU/RAM con herramientas de medición disponibles en el entorno del usuario.

## Actualización: migración total (prueba de capacidad)

El 2026-08-20 se ejecutó la migración total a PySide6 como prueba de capacidad:

- La UI CustomTkinter fue eliminada (`app.py`, `widgets.py`, `tray.py`) y `main.py` pasó a ser la entrada única Qt.
- La UI Tk anterior queda preservada en la rama `legacy/tk-ui`.
- El modo `tk` de `benchmarks/ui_benchmark.py` se eliminó; solo queda el modo `qt` (`python benchmarks/ui_benchmark.py qt`).
- Sigue pendiente la validación con audio activo (microcortes y CPU/RAM), que es el criterio go/no-go definitivo antes de considerar la migración como definitiva.
