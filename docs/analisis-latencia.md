# Análisis: latencia de 81,3 ms ¿por dónde se ataca?

Fecha: 2026-10 · Estado: **análisis, sin código** (puerta L0 sin abrir)
Relacionado: `docs/diseno-v1.8.md` §E, `docs/investigacion-2026-10.md` §5.1 y §7.1,
`benchmarks/probe_wasapi.py`.

---

## 1. De qué está hecha la cifra que muestra la app

El desglose no es una estimación: lo calcula `LatencyBreakdown` con el tamaño de
bloque real, el relleno real del ring y el backlog real de la cola del hijo.

| etapa | ms | qué lo fija |
|---|---|---|
bloque de captura | **21,33** | `CHUNK = 1024` frames a 48 kHz |
ring | **38,6** | consigna `DRIFT_TARGET_MS = 60` (lo medido es 38,6) |
cola del proceso hijo | **0,0** | el hijo va sobrado; no acumula |
bloque de salida | **21,33** | el mismo `CHUNK`, contado otra vez |
**total** | **81,3** | suma de las cuatro |

Dos hechos se caen de ahí:

1. **El tamaño de bloque cuenta doble** (captura + salida) y se lleva el **52 %**
   de la latencia. No es un detalle de configuración: es la palanca más grande.
2. **El ring se lleva el otro 48 %**, y su tamaño no es arbitrario: absorbe los
   paquetes irregulares que entrega el cable virtual. Reducirlo es cambiar
   colchón por retardo.

## 2. La parte incómoda: 81,3 ms NO es la latencia que oye el usuario

`LatencyBreakdown` cuenta **solo nuestro búfer**. Fuera de esa cuenta quedan
tres tramos que probablemente sean mayores que el nuestro:

| tramo | ¿lo controlamos? | ¿lo hemos medido? |
|---|---|---|
búfer del reproductor (Spotify, juego, navegador) | no | **no** |
búfer interno de VB-CABLE | solo desde su panel | **no** |
motor WASAPI en modo compartido | parcialmente | **no** (solo el periodo: 10 ms por defecto, mínimo 2,67) |
nuestro búfer | sí | **sí: 81,3 ms** |

Consecuencia antes de escribir una línea: **si el cable y el reproductor suman
150 ms, bajar nuestros 81 a 10 mueve el total de 230 a 160 (un 30 %)**, y el
usuario seguirá notando desfase. Si suman 20 ms, bajar a 10 casi lo resuelve.
**La decisión depende de un número que no tenemos.** Ese es el trabajo L0.

## 3. Palancas, con su ahorro y su precio

| palanca | ahorro en nuestro búfer | precio |
|---|---|---|
`CHUNK` 1024 → 512 | −21,3 ms (81,3 → 60,0) | 94 callbacks/s en vez de 47 |
`CHUNK` 1024 → 256 | −32,0 ms (81,3 → 49,3) | 187 callbacks/s; el coste fijo por llamada empieza a pesar |
`CHUNK` 1024 → 128 | −37,3 ms (81,3 → 44,0) | 375 callbacks/s; **el salto al proceso hijo deja de ser gratis** |
`DRIFT_TARGET_MS` 60 → 30 | −30 ms aprox. | menos colchón para los paquetes irregulares del cable |
hints de latencia en PortAudio (`input_latency`/`output_latency`) | **desconocido** | gratis de probar; el driver puede ignorarlo |
render nativo WASAPI con `IAudioClient3` (evento) | quita los búferes que PortAudio pone por defecto | reescribir la salida del motor |
captura por proceso (sin cable) | **elimina el tramo mayor desconocido** | selector de app + DRM |

Nota sobre el número estrella: los **2,67 ms** medidos son el **periodo mínimo**
del motor (`IAudioClient3`, 128 frames), **no** la latencia total. La total es
periodo × búferes + motor + DSP. Un objetivo realista es **10-15 ms de nuestra
parte**, no 2,67.

## 4. El triángulo que hay que decidir (no se puede tener todo)

En cuanto el bloque baja de ~5 ms, el **proceso hijo** pasa de invisible a
protagonista: cada bloque necesita un viaje de ida y vuelta (cola + despertar de
sincronización) y ese coste es del orden de milisegundos, no de microsegundos.
Con bloques de 2,67 ms eso es el bloque entero. Hay tres salidas y solo tres:

| opción | latencia | aislamiento de fallos | coste |
|---|---|---|---|
**A. Seguir con hijo**, bloques ≥ ~5 ms (240 frames) | 10-15 ms | **sí** (un fallo de numpy no mata el audio) | ninguno: es lo de ahora |
**B. DSP en el proceso principal**, bloques de 2,67 ms | **2,7-8 ms** | **no** | vigilante que reinicie el DSP si algo falla |
**C. Hijo, pero prerenderizando por delante** | igual que A | sí | no se gana nada: es lo de ahora |

Es decir: **A o B; C es volver a empezar.** Mi recomendación es **A** hasta que
haya una razón medida para B, porque el aislamiento del hijo es lo que hace que
la app no se calle cuando un filtro peta, y la diferencia entre 15 y 8 ms casi
nadie la oye; la diferencia entre sonar y no sonar, sí.

## 5. Plan por fases, cada una con su puerta

### L0 — Presupuesto completo del camino real (medir antes de tocar)

Método: una señal con impulsos de referencia generada por un programa de prueba
que anota su marca de tiempo (`perf_counter`) al escribirla en `CABLE Input`; se
captura el **altavoz físico por loopback** (captura nativa que ya existe en
`process_loopback.py`) y se busca el impulso. La diferencia entre la marca de
tiempo y la llegada es **la latencia total real**, cable incluido.

| medición | qué se aprende |
|---|---|
reproductor → `CABLE Input` (sin la app) | búfer del reproductor + entrada del cable |
con la app activa | el total real y, por diferencia con el nuestro, el motor + cable + salida |
por diferencia con 81,3 | cuánto del total es **nuestro** |

**Puerta**: tabla con los cuatro tramos. Si nuestro búfer es < 40 % del total,
el trabajo que rinde es L3/L4 (motor propio y quitar el cable), no L1/L2.

### L1 — Barrido de `CHUNK` con la pila actual (barato, sin motor nuevo)

1024 → 512 → 256 → 128 midiendo `underruns`, `huecos`, p99 del DSP y CPU, 10
minutos de audio real por escalón.

**Puerta**: el bloque más pequeño con **0 underruns y 0 huecos** en 10 min y
p99 < mitad del bloque. Si el suelo es 512 con PortAudio, entonces la
justificación de un motor propio deja de ser la latencia y pasa a ser **quitar
VB-CABLE**, que es otra razón (y buena).

### L2 — Consigna del ring

`DRIFT_TARGET_MS` 60 → 40 → 30 → 20 midiendo deriva y huecos.

**Puerta**: la consigna más baja que aguanta 10 min de audio real **sin huecos**
con la deriva compensada sin saltos. Ojo: el control de deriva existe justo para
absorber la irregularidad del cable; bajar el colchón sin cambiar de arquitectura
es pedirle al control que haga magia.

### L3 — Motor propio: captura nativa + render con `IAudioClient3`

Lo que ya está a medio camino: `process_loopback.py` tiene captura WASAPI con
ctypes y sondeo de `IAudioClient3` (periodo mínimo 128 frames con este chipset).

- Captura: cliente loopback en **modo evento** (ya hay cliente, falta el evento).
- Salida: `IAudioClient3::InitializeSharedAudioStream` pidiendo periodo mínimo,
  render por evento; anillo de salida con consigna de 2 periodos.
- El DSP sigue en su hilo/child detrás del anillo (opción A del §4).

**Puerta**: latencia nuestra **≤ 12 ms** medida con el método de L0, 0 huecos en
10 min, y el candado de seguridad: `IAudioClient3` ausente → vuelta a PortAudio
sin que el usuario note nada.

### L4 — Quitar VB-CABLE (el único que reduce el total)

La PoC de captura por proceso ya funciona (tono de 1 kHz verificado, 250
paquetes, 0 descartes). Falta el selector y decidir DRM/elevados.

**Puerta**: total real (L0) más bajo que con cable, en una app real, y el
instalador deja de pedir VB-CABLE.

### L5 — Dónde vive el DSP

Decidir A o B del §4 **con el coste del salto medido** (§7.6 de la investigación,
hoy sin medir). Sin ese número, cambiar a DSP en proceso es una apuesta.

## 6. Podas (cuándo abandonar, decididas de antemano)

- Si L1 da suelo 512 con PortAudio y L0 dice que el cable es < 30 ms del total:
  **no se escribe motor propio**; se queda en 512 y se documenta.
- Si L2 no aguanta 40 ms sin huecos: el ring se queda como está y se dice que la
  latencia la manda el cable, no nosotros.
- Si el total de L0 es mayormente del reproductor: **no hay nada que arreglar en
  la app**; se documenta el reparto y se cierra el punto. Un reproductor con 100 ms
  de búfer no se arregla desde el otro lado del cable.
- Modo exclusivo: **descartado de entrada**. Silenciaría al resto del sistema, y
  esta app es un "cascada" del sistema entero.

## 7. Riesgos medidos y no medidos

| riesgo | estado |
|---|---|
bloques pequeños provocan xruns en equipos cargados | por medir (L1) |
el periodo mínimo depende del chipset (medido en **1** máquina) | §7.1, sin cerrar |
coste del salto al hijo en µs | **sin medir** (§7.6) |
búfer interno de VB-CABLE | desconocido |
`IAudioClient3` no disponible en equipos viejos | mitigado: vuelta a PortAudio |
el techo de 0,687 ms del true-peak por bloque | a 2,67 ms de bloque sería el 26 % → el medidor es **opt-in**, correcto |

## 8. Resumen en una frase

De nuestros 81,3 ms, **52 % es un `CHUNK = 1024` contado dos veces** y **48 % es
el colchón del ring**; las dos palancas baratas (L1, L2) se prueban en una tarde
y pueden dejar el búfer en ~30-40 ms sin tocar el motor, pero **el tramo que más
duele (el cable y el reproductor) no está medido ni está bajo nuestro control**:
primero L0, que es medir; después decidir si hace falta motor propio.
