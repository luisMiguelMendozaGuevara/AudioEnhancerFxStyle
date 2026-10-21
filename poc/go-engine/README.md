# PoC: motor de audio en Go (experimento)

Rama `exp-go-engine` — **experimento**, no forma parte de la app ni se mergea.

## Que hace
Captura WASAPI loopback (por defecto "CABLE Input") -> ring buffer -> render
al dispositivo fisico. Salida **silenciosa** por defecto (mide sin duplicar
audio; `-audible` para oir). Mide: underruns del ring (cortes), huecos,
desbordes, jitter de los callbacks y coste por callback.

## Requisitos
- Go 1.27+ (probado con el zip oficial, sin admin).
- Windows 10/11 (WASAPI).

## Uso
```
go mod tidy
go build -o poc.exe .
.\poc.exe -sec 180 -target-ms 250 -render "Synaptics"
.\poc.exe -sec 120 -target-ms 60  -render "Synaptics"   # comparar con la app
.\poc.exe -sec 20  -audible -render "Synaptics"         # oir el passthrough
```

## Resultados (2026-10-17, esta maquina, app corriendo)

| Prueba | Underruns | Fill (ms) | Jitter p50 / p99 / max |
|---|---|---|---|
| 15 s @250 ms | 0 | 224-288 | 0.03 / 8.8-12.7 / 25-55 ms |
| 120 s @60 ms | 0 | 4-92 (deriva a la baja) | 0.02 / 5.1-6.0 / 208 ms |
| 180 s @250 ms | 0 | 226-370 | 0.03 / 9.6 / 136-236 ms |

## Conclusiones
- El motor en Go es ligero (jitter p50 ~0.03 ms, coste de callback bajo) y no
  produjo ningun corte del ring en 5 minutos de medida.
- Los **atascos del sistema (100-240 ms)** aparecen igual que en Python/SO:
  son del scheduling de Windows, no del lenguaje. Un callback de render tarde
  provoca underrun del dispositivo aunque el ring tenga 1 s.
- El **colchon** sigue siendo la palanca real: a 60 ms el fill se hunde (el
  PoC no tiene control de deriva); a 250 ms es estable.
- Veredicto: viable tecnicamente, pero el port **no** elimina la causa medida
  de los fallos (stalls del SO + politica de buffer). Se queda como
  experimento; solo tendria sentido retomarlo (con paridad DSP y control de
  deriva portados) si se busca un binario nativo, no para arreglar los cortes.

## A/B limpio (2026-10-20, app parada, 250 ms, 10 min cada uno)

Mismo tono (440 Hz, amplitud 0.03) hacia CABLE Input, salida a altavoces,
ventanas de 600 s, sin otras cargas durante la medida.

| Metrica | App @250 ms | PoC Go @250 ms |
|---|---|---|
| Cortes (huecos / underruns) | 0 | 0 |
| Huecos (segundos) | 0.000 | 0.000 |
| Repeticiones por gracia | 0 | n/a |
| Hambrunas del hijo DSP | 13 (absorbidas por el colchon) | n/a |
| Fill del ring | 215-265 ms | 156-296 ms |
| Jitter p50 / p99 / max | (la app no lo registra) | 0.04 / 5.8 / 711 ms |
| CPU del motor | ~7% (1552 us/bloque de DSP, medido antes) | 3.1% |

**Conclusion**: al mismo colchon (250 ms) **ambos motores son igual de limpios
(0 cortes)**. Go gana en CPU (~4 puntos de un nucleo) y en simplicidad de
proceso (sin hijo ni colas), pero **no en cortes**: lo que los elimina es el
colchon. Los atascos del sistema (hasta 711 ms en esta ventana) aparecen en
ambos mundos y el buffer es lo que los absorbe.

