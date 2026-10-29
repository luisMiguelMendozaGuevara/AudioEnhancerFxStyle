# Verificación de la v1.8

Registro de lo que se mide al cerrar cada paso de `docs/diseno-v1.8.md`. Cada
cifra sale de un test o de un sondeo reproducible, no de una estimación.

## A1 — Medidor de true-peak (cerrado)

Módulo: `audio_enhancer/true_peak.py`. Tests: `tests/test_true_peak.py` (32).

### Exactitud contra la verdad analítica

Un seno de amplitud 1 tiene pico real **0,000 dBTP** por definición, sin importar
cómo se muestree. Todo lo que se aparte de ahí es error del medidor.

| señal | pico de muestra | pico real medido | error |
|---|---|---|---|
| `fs/4` (12 kHz) fase 45° | −3,0103 dBFS | **+0,0000 dBTP** | 0,000 dB |
| `fs/8` (6 kHz) fase 0° | 0,0000 dBFS | +0,0000 dBTP | 0,000 dB |
| 997 Hz | 0,0000 dBFS | +0,0000 dBTP | 0,000 dB |
| 15 kHz fase 20° | −0,0083 dBFS | −0,0000 dBTP | 0,000 dB |
| 20 kHz fase 45° | — | +0,0010 dBTP | 0,001 dB |
| 1 kHz a −40 dBFS | −40,000 dBFS | −40,000 dBTP | 0,000 dB |

El caso de referencia (el que distingue un medidor de muestras de uno de pico
real) da la diferencia esperada: **3,0106 dB** entre pico de muestra y pico real,
frente a los 3,01 dB que se deducen de la aritmética del muestreo a `fs/4` con
fase de 45°.

### Filtro de interpolación

| propiedad | valor medido |
|---|---|
| factor de sobremuestreo | 4× (mínimo de BS.1770-4 anexo 2) |
| taps por fase | 32 (total 129) |
| ganancia en continua | exactamente el factor (error < 1e-6) |
| continua interpolada | plana, sin rizado (< 0,001 dB) |
| atenuación de imágenes desde 0,6·fs | **−82 dB** |
| respuesta en 20 kHz | +0,0008 dB |
| atenuación en 0,55·fs | −30 dB (banda de transición: inevitable con un filtro finito) |

### Equivalencia en bloques

El medidor con estado tiene que dar lo mismo que medir el archivo entero, con
cualquier tamaño de bloque: verificado a 480, 1024 y 2880 frames, diferencia
< 0,01 dB en todos los casos.

Para conseguirlo hicieron falta tres correcciones que se vieron midiendo, no
razonando:

1. **Contexto a ambos lados**: cada muestra se mide con 12 muestras de contexto
   por delante y por detrás. Sin el contexto posterior, el tramo final de cada
   bloque se interpolaba sin soporte completo del filtro y aparecía rizado de
   Gibbs: **+0,11 dB de más** en el caso `fs/4` a 45°, y el medidor se quedaba con
   ese falso pico para siempre.
2. **Tramo de longitud exacta**: la primera versión medía 185 muestras de más por
   bloque y la reconstrucción no era contigua (el máximo salía 1,135 en vez de
   1,000).
3. **Primado del primer bloque**: el contexto inicial es silencio artificial y su
   transitorio no es audio. Sin descartarlo, el arranque en seco de un seno
   añadía hasta **+1,10 dB** (997 Hz) al máximo histórico.

### Coste

| configuración | coste por bloque (1024 frames estéreo) | % del bloque de 21,33 ms |
|---|---|---|
| **4× / 32 taps (elegida)** | **0,687 ms** | **3,22 %** |
| 4× / 24 taps | 0,68 ms | 3,20 % (error 0,135 dB a 20 kHz) |
| 4× / 48 taps | 0,82 ms | 3,84 % |
| 8× / 24 taps | 1,05 ms | 4,90 % |

El objetivo del diseño era **< 1,5 %** y **no se cumple**: el suelo real está en
~3 %, porque el coste lo domina la llamada a `upfirdn` y la alocación de su
salida, no la aritmética (se probó con `float32` y con búfer preasignado: 0,66 y
0,84 ms, sin mejora). Se elige 4×/32 porque con el mismo coste que 4×/24 reduce
el error a 20 kHz de 0,135 dB a 0,001 dB.

**Decisión**: el medidor entra con 3,2 % documentado. Si en la integración
(A4) molesta, la palanca es medir cada dos bloques y sumar los dos, no bajar la
calidad del filtro.

### A1.5 — El limitador que ya existe, medido con el medidor nuevo

La app ya traía limitador por true-peak (casilla "True peak" en Efectos, activa
por defecto). El medidor permite por fin comprobar si cumple. Señal de prueba:
seno de 12 kHz con fase de 45° y amplitud 1,15, que tiene **pico de muestra
−1,80 dBFS pero pico real +1,21 dBTP**.

| configuración | pico de muestra | pico real | veredicto |
|---|---|---|---|
| entrada | −1,80 dBFS | **+1,21 dBTP** | 1,2 dB por encima del techo |
| limitador apagado | −1,77 dBFS | +1,22 dBTP | pasa tal cual |
| limitador encendido, "True peak" **desactivado** | −1,77 dBFS | **+1,22 dBTP** | **no hace nada**: el pico de muestra está por debajo del umbral y nunca lo ve |
| limitador encendido, "True peak" **activado** | −3,47 dBFS | **−0,46 dBTP** | ✅ lo deja en el techo (−0,45) |

Conclusión: la casilla funciona y no es cosmética, y el limitador de muestras
tiene un punto ciego real de más de 1 dB en contenido cerca de Nyquist. El error
del camino con true-peak es de 0,01 dB, así que el filtro 4× del limitador no se
queda corto frente al del medidor.

### Estado (A1)

Medidor terminado y probado. Conectado al motor en A4.

## A4 — Conectado al motor y en la interfaz (cerrado)

- **Motor**: el medidor mide la **salida** en los dos caminos, en su propio
  `try` y **después** de publicar el bloque (la misma regla que el medidor de
  sonoridad: un fallo del medidor no puede silenciar el audio). En el proceso
  hijo los valores viajan por la memoria compartida como huecos 11..13 de la
  telemetría (`pico`, `true_peak`, `diferencia`); en el camino local se miden en
  el hilo de DSP. Contrato único: `engine.true_peak_snapshot()` con `-1000.0`
  como "sin medida".
- **Interruptor**: casilla "Medir pico real (~3 % CPU)" en el panel del
  Ecualizador. Apagarla **no cambia el audio** (solo deja de medir y ahorra el
  coste); la bandera viaja al proceso hijo por memoria compartida.
- **Interfaz**: cuarta columna del panel — `Pico` (dBFS), `True peak` (dBTP) y
  `Reducción` (dB del limitador). El true peak se pinta en color de aviso cuando
  pasa de 0 dBTP, que es cuando el convertidor del altavoz recorta.

### Verificación

| criterio | resultado |
|---|---|
| **bit-exactitud**: salida con y sin medidor | **idéntica byte a byte** (test) |
| medición apagada | `true_peak_snapshot()` devuelve `-1000.0` → la UI muestra `--` |
| telemetría del hijo (14 huecos) | pico de muestra, pico real y diferencia llegan al padre |
| interruptor | apaga y enciende la medición del motor, y no toca el audio |
| aviso visual | `+0,4 dBTP` se pinta en aviso; `−0,4 dBTP` no |

**600 tests** en total, ruff/format/mypy limpios.

### Coste real en la app

El medidor añade 0,69 ms por bloque de 1024 frames, el 3,2 % del bloque. La
telemetría del motor en marcha lo confirma con `dsp p99` dentro de lo que ya
había: no aparece ningún pico nuevo atribuible al medidor.
