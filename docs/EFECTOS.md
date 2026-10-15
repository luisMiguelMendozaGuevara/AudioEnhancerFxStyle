# Los efectos de Audio Enhancer FxStyle, explicados

Guía detallada de la capa "enhancer" (v1.6.0) y de los controles clásicos.
Dentro de la app, cada control tiene además su propia explicación.

## La cadena de procesado (en orden)

El audio entra por la captura y atraviesa estas etapas; las que están
apagadas se saltan sin coste:

1. **DC blocker** (siempre activo): quita la componente continua antes de
   filtros y dinámica (más headroom).
2. **Crossfeed** (opcional, para auriculares).
3. **EQ**: shelves de Graves/Agudos + ecualizador de 5/9/10/15/31 bandas.
4. **Exciters**: Claridad (3 kHz+) y Exciter de graves (<100 Hz).
5. **Refuerzo dinámico** (compresor ascendente).
6. **Compresor** (RMS clásico, opcional).
7. **Nivelación** (AGC lento).
8. **Ambiente** y **Surround** (espacio y anchura).
9. **Volumen** del usuario.
10. **Balance** y **Master** (posición y nivel final).
11. **Limitador / Techo de seguridad** y **Recorte final** (protección).

## Efectos de la capa enhancer (0-10, con interruptor)

### Claridad (Fidelity) — 0-10
- **Qué hace**: exciter de presencia. Filtra el contenido por encima de
  3 kHz y genera su **2º armónico** (rectificado + filtro que quita la
  continua), mezclándolo con la señal original.
- **Cómo suena**: recupera el detalle que se pierde al comprimir/streamear
  (voces, platos, "aire") **sin subir el volumen de esa banda**: es
  contenido nuevo, no un EQ.
- **Recomendación**: 3-5. Sube poco a poco; un exceso produce sibilancias.

### Ambiente (Ambience) — 0-10
- **Qué hace**: añade 3 reflexiones tempranas por canal con retardos primos
  distintos (izquierda 11/23/37 ms; derecha 13/27/41 ms) y ganancias
  decrecientes, como una sala pequeña.
- **Cómo suena**: más sensación de espacio; con auriculares la música deja
  de sentirse "pegada al centro". Un exceso emborrona la imagen.
- **Recomendación**: 1-3 para música/vídeo.

### Surround (anchura) — 0-10
- **Qué hace**: ensancha la imagen estéreo por Mid/Side. Los graves quedan
  al centro (**mono-bass**: solo actúa por encima de 150 Hz) y si la señal
  ya es muy ancha se reduce automáticamente (**guard de correlación**).
- **Cómo suena**: escena estéreo más ancha; la suma mono (L+R) se preserva.
  Se nota más con auriculares. Si sumas la salida a mono, el ensanchado se
  cancela (es normal).
- **Recomendación**: 2-4.

### Refuerzo dinámico (Dynamic Boost) — 0-10
- **Qué hace**: compresor **ASCENDENTE**: sube los pasajes bajos (hasta
  +12 dB) con un objetivo de -18 dBFS RMS, con envolvente por muestra
  (ataque 5 ms / release 350 ms, link estéreo). **Los picos no se tocan.**
- **Cómo suena**: todo más lleno y "cerca" a volumen bajo, sin subir el
  volumen global. Un exceso reduce la dinámica percibida.
- **Recomendación**: 3-6 si escuchas a volumen bajo; 0 si ya escuchas fuerte.

### Exciter de graves (Bass) — 0-10
- **Qué hace**: filtra lo que hay por debajo de 100 Hz y genera su
  **2º armónico**: el oído lo interpreta como grave real.
- **Cómo suena**: "punch" de graves en altavoces pequeños, sin retumbar.
- **Ojo**: es distinto del "Refuerzo de graves" (shelf) — aquel sube el
  volumen de esa banda; este **crea contenido nuevo**.
- **Recomendación**: 2-4 en altavoces pequeños; 0-2 con subwoofer.

### Nivelación (Leveling) — 0-10
- **Qué hace**: AGC muy lento hacia -18 dBFS (corrección ±9 dB), colocado
  antes del volumen del usuario: normaliza el material, no tu ajuste manual.
- **Cómo suena**: iguala el volumen ENTRE pistas — una canción bajita sube y
  una muy fuerte baja un poco; dejas de tocar el volumen entre canciones.
  En música clásica puede aplanar los contrastes.
- **Recomendación**: 2. (Con 4+ la música fuerte baja y, si subes el
  volumen, el techo aplasta los picos.)

## Controles clásicos

- **Volumen**: ganancia final del audio procesado. 1.0x no cambia el nivel;
  valores mayores pueden activar el limitador.
- **Graves / Agudos**: shelves de graves (<150 Hz, peso y cuerpo) y agudos
  (>6 kHz, claridad y detalle). Demasiado agudos suena áspero.
- **EQ (5/9/10/15/31 bandas)**: cada banda realza (+dB) o atenúa (-dB) su
  zona. El set de 31 es ISO 1/3 de octava (20 Hz-20 kHz). La **frecuencia
  central** de cada banda es editable (acotada entre las vecinas) y el **Q**
  global (0.2-10) ajusta la anchura de la curva.
- **Compresor (RMS)**: reduce gradualmente las partes demasiado fuertes;
  suaviza la dinámica. Un exceso suena "aplastado".
- **Limitador**: reduce suavemente los picos cerca de 0 dBFS en vez de
  cortarlos de golpe (anti-clipping).
- **True-peak (x4)**: además de los picos visibles, limita los que caen
  ENTRE dos muestras. Es la etapa más costosa; apágalo en equipos lentos.
- **Techo de seguridad**: con el limitador apagado, evita el recorte duro
  ("estática") reduciendo los picos por encima de -0.1 dBFS.
- **Recorte final (±1.0)**: última guardia digital. Apagado, cualquier
  recorte lo haría el driver de audio.
- **Crossfeed (BS2B)**: solo auriculares; mezcla cada canal en el opuesto
  para que el estéreo suene "fuera de la cabeza". Compacta la imagen.
- **Balance L/R** (±20 dB, equal-power) y **Master** (±20 dB): posición y
  nivel final, con rampas por muestra (sin clics).

## Aviso de recorte

La página **Efectos** avisa cuando hay efectos activos y **ninguna
protección** (ni limitador ni techo): en ese caso los picos que superen
±1.0 los recorta el driver (distorsión). Si lo ves, activa el techo de
seguridad, baja el volumen/master o reduce los efectos.

## Presets recomendados

- **Recomendado (altavoces)**: V suave medida con ruido rosa
  (+2.7 dB graves, +2.3 dB agudos), nivelación 2, volumen 1.0.
- **Recomendado (auriculares)**: pensado para escucha cercana.
- **Música / Cine / Juegos / Voz / Graves (enhancer)**: combinaciones de
  los 5 efectos para cada uso (p. ej. Graves: exciter de graves 6 +
  refuerzo dinámico 3; Voz: claridad 4 + refuerzo 3 + nivelación 4).

## Cómo empezar

1. Elige **fuente** (`CABLE Input [Loopback]`) y **salida** real en Audio.
2. Latencia: 60 ms por defecto; **"Estabilidad (250 ms)"** si oyes cortes
   con carga. Tras un corte real, el colchón sube solo a 250 ms 20 s.
3. Activa efectos de a uno y escucha: Claridad 3-5, Refuerzo 3-5,
   Ambiente 1-2, Surround 2-3, Graves 2-4.
4. Deja el **techo de seguridad** activado (es transparente) o vigila el
   aviso de recorte.
