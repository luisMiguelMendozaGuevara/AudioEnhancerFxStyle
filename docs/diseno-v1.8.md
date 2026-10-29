# Diseño de la v1.8 — "sonar mejor" (segunda mitad)

Estado: **propuesta**. Nada de este documento está implementado salvo lo que se
marca como hecho en la 1.7. El criterio para aceptar cada punto es una medida
contra una norma o una referencia externa, nunca contra la propia
implementación.

## 0. Qué queda de la v1.8

Del roadmap original (`docs/investigacion-2026-10.md` §6):

| # | Punto | Estado |
|---|---|---|
1 | Leveling por LUFS con K-weighting y controlador lento | **hecho en 1.7** |
2 | Rediseño del Bass Exciter (2.º+3.er armónico) | **hecho en 1.7** |
3 | Anti-aliasing (ADAA) en el camino no lineal | **hecho en 1.7**, solo en Clarity |
4 | **True-peak 8× y de-esser tras Clarity** | este documento, A y B |
5 | **Importación de perfiles AutoEQ por dispositivo** | este documento, C |

Y una deuda de la 1.7: el **true-peak no se muestra en la interfaz** (se mide,
pero solo en los tests y en el informe de diagnóstico).

Fuera de alcance en la 1.8, a propósito: latencia baja (`IAudioClient3`), captura
por aplicación y compresor multibanda. Mezclar cualquiera de los tres con
cambios de sonido haría imposible saber qué movió la aguja.

---

## A. True-peak: medirlo bien y (opcionalmente) limitarlo

### A.1 El problema, con los números que ya tenemos

El limitador actual trabaja sobre **muestras**. Un pico entre muestras no lo ve.
En la 1.7 medimos que la cadena no rompe el techo de muestras (pico 0,93 con
parámetros extremos), pero eso no dice nada del pico real: una señal con pico de
muestra a −3,01 dBFS puede tener pico real a 0 dBTP si su frecuencia es `fs/4` y
la fase cae mal. Es un hecho matemático, no una hipótesis, y es justo el caso que
satura el convertidor D/A de un usuario.

Además, los caminos no lineales (Clarity con ADAA, exciters) **inventan**
contenido entre muestras: es donde más probable es que aparezca el problema.

### A.2 Qué se mide y contra qué norma

- **Norma**: ITU-R BS.1770-4, anexo 2 (definición del true-peak por
  sobremuestreo) y **EBU Tech 3341** para los casos de prueba y su tolerancia.
- **Banco de verificación** (señales de referencia, generadas por nosotros):
  1. Seno a `fs/4` con fase de 45°: pico de muestra −3,01 dBFS y pico real
     0 dBTP. **La diferencia entre ambos es 3,01 dB**, y es la que debe leer el
     medidor.
  2. Seno a `fs/8` a 0 dBFS de muestra: diferencia ≈ 0,69 dB.
  3. Ruido blanco a −1 dBFS: la diferencia debe ser pequeña (< 0,3 dB).
  4. Los casos de true-peak de EBU Tech 3341, con la tolerancia que declare la
     norma (se copia el literal al test, no se ajusta a lo que midamos).
- **Criterio de aceptación**: error ≤ ±0,5 dB en los casos 1-2, y el medidor no
  puede inventar picos en el caso 3.

### A.3 Diseño

**Fase A1 — medidor (no toca el audio).**
- Sobremuestreo **8×** con FIR polifásico (Raised cosine, 48 taps por fase), solo
  en la ruta del medidor, no en la del audio.
- Ventana deslizante de pico con caída lenta (peak hold configurable, por
  defecto 1 s) más un "max since reset" que el usuario puede poner a cero.
- Coste objetivo: **< 1,5 % del bloque** de 21,3 ms. **Medido y no cumplido**:
  el suelo real es ~3,2 % (ver `docs/verificacion-v1.8.md` §A1); se aplicó el plan
  B (bajar a 4×, el mínimo de la norma) y aun así no baja de 3 %, porque el coste
  está en la llamada al filtro, no en la aritmética. Queda documentado y la
  palanca, si molesta, es medir cada dos bloques.
- **Sin cambios en el sonido**: con el medidor activo, la salida debe ser
  **bit-exacta** respecto de la 1.7. Es un test, no una promesa.

**Fase A2 — techo por true-peak en el limitador (opt-in, apagado por defecto).**
- El limitador actual pasa a mirar el pico real con lookahead de 1 ms (48
  muestras) en vez del pico de muestra, y el techo se expresa en dBTP
  (por defecto −1,0 dBTP).
- Motivo de que sea opt-in: cambia el sonido (más reducción de ganancia en
  material ya caliente). Quien no lo active no debe notar nada.

### A.4 Dónde se ve

El panel del Ecualizador hoy es 3×3. El true-peak entra como **cuarta columna**
("picos"): `Pico` (dBFS), `True peak` (dBTP), `Reducción` (dB del limitador).
La rejilla pasa a 4×3 con la misma lógica: columna = qué está pasando.

### A.5 Riesgos

- Que el medidor cueste más de lo previsto con 8× → plan B 4×, medido.
- Creer que el limitador por true-peak "arregla" material mal masterizado: no,
  reduce el riesgo de saturación del DAC, no recupera masterings.

---

## B. De-esser tras Clarity

### B.1 El problema: hay que medirlo antes de escribirlo

Hoy **no está demostrado** que haga falta. La cadena realza agudos (Clarity,
realce de presencia del EQ) y los exciters añaden armónicos; la sospecha es que
las sibilantes ("s", "sh", "ch") se vuelven duras. Antes de implementar nada, el
primer entregable es una **medición del problema** (ODD: la semilla se mide):

1. Señal sintética de sibilante: ráfaga de ruido rosa filtrado 6-9 kHz, 120 ms,
   a −12 dBFS.
2. Medir la energía 6-9 kHz a la salida con Clarity y exciters **activados** vs
   **desactivados**, con los presets de fábrica.
3. Si la diferencia es < 1 dB, **el de-esser no entra en la 1.8**: se documenta y
   se cierra el punto. Si es ≥ 3 dB, entra con el diseño de abajo.

### B.2 Diseño (si el paso B.1 lo justifica)

- **Estructura**: banda dividida con Linkwitz-Riley de 4.º orden en 5 kHz y
  9 kHz; el detector trabaja solo en la banda media (5-9 kHz) y aplica reducción
  **solo a esa banda**, para no tocar el cuerpo de la voz.
- **Detector**: RMS de la banda con ataque 0,5-1 ms y liberación 30-80 ms
  (ajustables), umbral en dBFS relativo al nivel de la banda, reducción máxima
  −12 dB.
- **Posición**: después de Clarity y de los exciters, antes del limitador.
- **Controles**: solo tres (Cantidad, Frecuencia central, Liberación) más un
  interruptor. Los presets de fábrica lo dejan **apagado**; se activa en el
  preset "voz/podcast".

### B.3 Criterios de aceptación (medidos)

| criterio | valor exigido |
|---|---|
| Apagado = neutro | salida bit-exacta respecto de la 1.7 |
| Reducción en la banda | ≥ 4 dB en la ráfaga de B.1 con Cantidad al 50 % |
| Ataque | reducción a −3 dB del máximo en < 2 ms |
| Sin ceceo | energía 1-4 kHz cambia < 0,5 dB |
| Sin bombeo | ruido rosa estacionario: reducción estable < 1 dB |
| Transparencia | THD+N del camino neutro sin cambios (< −140 dB) |
| Coste | < 2 % del bloque de 21,3 ms |

### B.4 Riesgos

- El de-esser es el efecto más fácil de estropear: si recorta demasiado suena a
  "lisp" y si recorta tarde no sirve. De ahí que los criterios incluyan el
  daño (ceceo) y no solo la reducción.
- Con auriculares brillantes puede parecer que "no hace nada": la reducción es
  estrecha a propósito.

---

## C. Importación de perfiles AutoEQ

### C.1 El problema

Quien tiene unos auriculares con corrección publicada (AutoEQ) quiere esa curva,
y meter 31 bandas a mano es inviable. Hoy hay que copiarla valor a valor.

### C.2 Decisión de diseño que resuelve el tema legal

AutoEQ es MIT, pero las **mediciones crudas** que procesa vienen de terceros
(oratory1990, crinacle, Rtings…) cuya licencia puede no ser MIT. Por eso:

- **No se empaqueta ningún perfil.** La app solo **lee archivos que el usuario
  aporte** (`ParametricEQ.txt` y `GraphicEQ.txt` de AutoEQ).
- Se guarda la procedencia (ruta y fecha del archivo) en el preset, para que el
  usuario sepa qué cargó, pero no se redistribuye nada.
- Con eso el bloqueador legal desaparece sin dejar de dar la función.

### C.3 Diseño

- **Parser** de los dos formatos documentados por AutoEQ:
  - `ParametricEQ.txt`: líneas `Filter N: ON PK Fc 2500 Hz Gain -3.0 dB Q 1.41`.
  - `GraphicEQ.txt`: `Fc1 gain1; Fc2 gain2; …` (hasta 127 puntos).
- **Mapeo a nuestras bandas** (9 o 31): interpolar la curva del archivo en las
  frecuencias de nuestras bandas y recortar a ±12 dB. Mostrar **antes/después**
  de la curva resultante en el Ecualizador.
- **Previsualización obligatoria**: no se aplica nada hasta que el usuario ve la
  curva y confirma. Un perfil de 31 bandas sobre un EQ de 9 pierde información y
  hay que decirlo en la interfaz.
- **Guardado** como preset personalizado con nombre "AutoEQ — <modelo>" y la
  procedencia en el campo de notas.

### C.4 Criterios de aceptación

| criterio | valor exigido |
|---|---|
| Parser `ParametricEQ` | 10 filtros PK/LS/HS → 10 bandas, error de ganancia < 0,1 dB |
| Parser `GraphicEQ` | 127 puntos interpolados sin desbordar |
| Archivo inválido | mensaje claro, sin excepción, sin tocar el EQ actual |
| Límites | ganancias recortadas a ±12 dB con aviso |
| Aviso de pérdida | si el archivo tiene más bandas que el EQ activo, se avisa |
| Coste | solo al importar: 0 % en el audio |

---

## D. Orden de trabajo y puertas

Cada paso termina en verde (tests + ruff + mypy) y con una medida anotada en
`docs/verificacion-v1.8.md`:

1. ~~**A1 — medidor de true-peak** con su banco de señales~~ **HECHO**
   (`audio_enhancer/true_peak.py`, 32 tests, error 0,000 dB; coste 3,2 %).
2. **A4 — true-peak en la interfaz** (cuarta columna del panel).
3. **B1 — medir si el de-esser hace falta**. Si no lo hace, se documenta y se
   salta el paso 4.
4. **B2 — de-esser**, solo si B1 lo justifica.
5. **A2 — limitador por true-peak** (opt-in, apagado por defecto).
6. **C — importador AutoEQ**, con previsualización.

Motivo del orden: los dos primeros **no cambian el sonido** y son los que dan la
información para decidir el resto. El de-esser va después de su propia medición y
el limitador al final porque es el único que altera el nivel.

## E. Qué NO entra en la 1.8

- Latencia baja con `IAudioClient3` (medida en 2,67 ms de suelo, pero es otra
  iteración: toca el motor, no el DSP).
- Integración de la captura por aplicación (aparcada, ver
  `docs/verificacion-v1.7.md` §12.3).
- Compresor multibanda y convolución: siguen en el backlog.
- Cualquier perfil AutoEQ empaquetado: prohibido por el punto C.2.
