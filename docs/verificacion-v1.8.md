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

**Decisión**: el medidor entra con 3,2 % documentado.

**La palanca de medir cada dos bloques se probó y se descartó**: ahorra solo el
11-14 % (2,11 % → 1,87 % del bloque), no la mitad, porque el coste es por muestra
procesada y no por llamada. La medida sale idéntica (< 0,001 dB), así que no era
un problema de exactitud sino de que no compensa. Hay un test que vigila que el
ahorro siga siendo marginal; si algún día crece, el test avisa.

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

## B1 — ¿Hace falta un de-esser? (cerrado: NO)

Herramienta: `benchmarks/medir_sibilancia.py` (reproducible). Señal: tren de
sibilantes (ruido 6-9 kHz, 120 ms cada 400 ms, −12 dBFS) sobre un cuerpo tonal
de 250 Hz, pasado por la cadena real.

Se mide la energía de la banda 6-9 kHz **respecto al cuerpo** (no en absoluto:
la nivelación cambia el volumen y con él todos los niveles).

| configuración | sibilante/cuerpo | diferencia vs neutro |
|---|---|---|
neutro | +1,68 dB | — |
**CONTROL: agudos +6 dB** | +6,02 dB | **+4,34 dB** |
**CONTROL: EQ +6 dB en 8k/12k** | +8,48 dB | **+6,80 dB** |
solo Clarity (`fidelity=4`) | +1,64 dB | **−0,04 dB** |
solo Bass exciter (`bass=3`) | +1,60 dB | **−0,09 dB** |
preset de fábrica "Recomendado (altavoces)" | −1,18 dB | **−2,87 dB** |

Los dos controles son la parte importante: **la herramienta sí detecta** un
aumento de agudos (+4,3 y +6,8 dB). Sin ellos, un resultado de −0,04 dB no
significaría nada.

### Qué hace Clarity de verdad (y por qué no es el problema)

| configuración | armónicos 12-18 kHz vs neutro |
|---|---|
solo Clarity | **+18,73 dB** |
preset de fábrica | **+24,78 dB** |
Bass exciter | 0,00 dB |

Clarity funciona: rectifica los agudos y genera armónicos **por encima** de la
banda original. Lo que **no** hace es endurecer la banda de la sibilante
(−0,04 dB), que es justo donde actuaría un de-esser. Confundir "no toca la banda"
con "no hace nada" habría llevado a implementar un de-esser que no arregla nada.

### Veredicto

El criterio estaba fijado de antemano: **< 1 dB → no entra**. Medido −0,04 dB
(Clarity) y −2,87 dB (preset completo, donde el realce del cuerpo gana más que el
de los agudos). **El de-esser no entra en la 1.8.** Punto cerrado con número, no
con opinión, y con la herramienta lista para repetir la medida si algún día
cambia Clarity, el EQ de fábrica o se sube el realce de agudos.

Nota de lectura: la sibilante **absoluta** sube +6,24 dB con el preset, pero eso
es el `dynamic_boost` y la nivelación subiendo todo el material, no un
endurecimiento del timbre. Por eso la medida se hace contra el cuerpo.

## A2 — Techo del limitador en dBTP (cerrado)

Antes el techo era el número heredado `limiter_threshold = 0,95`, que en términos
de pico real son **−0,4455 dBTP** por casualidad aritmética. Ahora el techo se
expresa como tal:

- `LIMITER_CEILING_DBTP = 20·log10(0,95) = −0,4455 dBTP`, y el umbral lineal se
  **deriva** de él (`dbtp_a_lineal`). La ida y la vuelta dan 0,95 exacto
  (diferencia < 1e-12), así que **el sonido no cambia** al pasar de un número al
  otro: es un cambio de unidades, no de comportamiento.
- Rango admitido: −6 a 0 dBTP. Se acota en la configuración (un −8 guardado por
  error pasa a −6, no a 0).
- Se guarda en `config.json` como `limiter_ceiling_dbtp` y aparece en el informe
  de diagnóstico (`- techo: -0.45 dBTP (limitador on, true-peak on)`), que es
  donde hace falta saberlo cuando alguien reporta recortes.
- Verificado con techo −3 dBTP: la salida no pasa de −2,9 dBTP.

## C — Importador de AutoEQ (cerrado)

Módulo `audio_enhancer/autoeq.py` (18 tests) + botón **Importar AutoEQ…** en la
página del Ecualizador.

**Decisión que resuelve el problema legal**: no se empaqueta ningún perfil. Se
leen archivos que aporte el usuario (`ParametricEQ.txt` y `GraphicEQ.txt`), y se
guarda la procedencia (nombre del archivo). Así el MIT del repositorio de AutoEQ y
la licencia de las mediciones de terceros dejan de ser un bloqueador: **no se
redistribuye nada**.

| criterio | resultado |
|---|---|
`ParametricEQ.txt` | lee los filtros `ON`; descarta `OFF`, `LP/HP/BP` y frecuencias fuera de 20 Hz-20 kHz |
`GraphicEQ.txt` | pares frecuencia/ganancia, hasta 127 puntos |
Formato desconocido | mensaje claro, sin excepción, y **el EQ no se toca** |
Interpolación | log-frecuencia; fuera del rango del archivo se usa el valor del extremo (no se extrapola) |
Límites | ganancias recortadas a ±12 dB, con aviso |
Avisos | pérdida al pasar de N puntos a 9 bandas, y si el perfil no cubre toda la banda |
Previsualización | el diálogo dice formato, puntos y ganancia máxima **antes** de aplicar |
Cancelar | no cambia ni una banda del EQ (test) |

**626 tests** en total · ruff/format/mypy limpios.
