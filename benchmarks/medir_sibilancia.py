"""B1 — ¿Hace falta un de-esser? Medido antes de escribirlo.

El diseño de la 1.8 dice: antes de implementar un de-esser hay que **demostrar
que el problema existe**. La sospecha es que Clarity (que rectifica agudos para
generar armónicos), el Bass exciter y el realce de agudos del EQ de fábrica
endurecen las sibilantes (la "s" de una voz).

Cómo se mide:

1. Se fabrica una señal de sibilante: ruido rosa filtrado a 6-9 kHz con un
   envolvente rápido de 120 ms, repetido cada 400 ms, a −12 dBFS de pico. Es la
   banda donde vive la "s" y donde el oído nota la dureza.
2. Se pasa por la cadena real (`Enhancer`) en cuatro configuraciones: neutra,
   preset de fábrica "Recomendado (altavoces)", solo Clarity y solo Bass exciter.
3. Se compara la energía de la banda 6-9 kHz **respecto al total** de cada salida
   y respecto al cuerpo de la señal (100 Hz-4 kHz). Comparar el total sería
   engañoso: la nivelación (AGC) cambia el volumen y con él todos los niveles.

Criterio de decisión (fijado en `docs/diseno-v1.8.md` antes de medir):

- Si la subida de la banda es **< 1 dB** → el de-esser no entra en la 1.8.
- Si es **>= 3 dB** → hay problema y entra.
- Entre 1 y 3 dB → se documenta y se decide con el criterio del usuario.

Uso:
    python benchmarks/medir_sibilancia.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audio_enhancer.dsp import Enhancer  # noqa: E402

FS = 48000
#: Banda de la sibilante ("s", "sh", "ch").
BANDA_SIBILANTE = (6000.0, 9000.0)
#: Banda del cuerpo de la voz, para normalizar.
BANDA_CUERPO = (100.0, 4000.0)

PRESET_FABRICA = {
    "volume": 1.0,
    "bass": 2.0,
    "treble": 1.0,
    "eq_gains": [0.5, 0.5, 0.0, -0.5, 0.0, 0.0, 0.5, 0.5, 0.5],
    "effects": {"dynamic_boost": 5.0, "fidelity": 4.0, "bass": 3.0, "ambience": 1.0, "surround": 2.0},
    "leveling": 2.0,
}


def señal_sibilante(segundos: float = 4.0, semilla: int = 5) -> np.ndarray:
    """Tren de sibilantes: ráfagas de ruido 6-9 kHz cada 400 ms, 120 ms cada una.

    Se añade un cuerpo tonal suave (voz sintética) para que el EQ y los exciters
    tengan con qué interactuar, como en material real.
    """
    n = int(FS * segundos)
    t = np.arange(n) / FS
    rng = np.random.default_rng(semilla)
    ruido = rng.normal(0.0, 1.0, n)
    sos = butter(4, [BANDA_SIBILANTE[0] / (FS / 2), BANDA_SIBILANTE[1] / (FS / 2)], btype="band", output="sos")
    sibilante = sosfilt(sos, ruido)
    # Envolvente: 120 ms de ráfaga cada 400 ms, con ataque y caída rápidos.
    envolvente = np.zeros(n)
    periodo, duracion = int(0.4 * FS), int(0.12 * FS)
    forma = np.hanning(duracion)
    for inicio in range(0, n - duracion, periodo):
        envolvente[inicio : inicio + duracion] = forma
    sibilante *= envolvente
    sibilante *= 0.25 / max(np.max(np.abs(sibilante)), 1e-9)  # pico −12 dBFS
    # Cuerpo: 250 Hz con armónicos (un "aa" tosco), 12 dB por debajo.
    cuerpo = sum(np.sin(2 * np.pi * 250.0 * k * t) / k for k in range(1, 6)) * 0.02
    mono = sibilante + cuerpo
    return np.repeat(mono[:, None], 2, axis=1).astype(np.float32)


def energia_banda(x: np.ndarray, banda: tuple[float, float]) -> float:
    """Energía (RMS) de la señal en una banda, por FFT."""
    mono = np.asarray(x, dtype=np.float64).mean(axis=1)
    espectro = np.fft.rfft(mono * np.hanning(len(mono)))
    frecuencias = np.fft.rfftfreq(len(mono), 1.0 / FS)
    mascara = (frecuencias >= banda[0]) & (frecuencias <= banda[1])
    return float(np.sqrt(np.sum(np.abs(espectro[mascara]) ** 2)))


def procesar(señal: np.ndarray, **ajustes) -> np.ndarray:
    """Pasa la señal por la cadena real con los ajustes indicados."""
    enh = Enhancer()
    enh.effects = {k: 0.0 for k in enh.effects}
    enh.eq_gains = [0.0] * len(enh.eq_gains)
    enh.bass, enh.treble, enh.volume = 0.0, 0.0, 1.0
    enh.leveling = 0.0
    for clave, valor in ajustes.items():
        setattr(enh, clave, valor)
    salida = [enh.process(señal[i : i + 1024]) for i in range(0, len(señal), 1024)]
    return np.concatenate(salida)


def main() -> int:
    señal = señal_sibilante()
    neutra = procesar(señal)
    solo_clarity = procesar(señal, effects={**{k: 0.0 for k in Enhancer().effects}, "fidelity": 4.0})
    solo_exciter = procesar(señal, effects={**{k: 0.0 for k in Enhancer().effects}, "bass": 3.0})
    # CONTROL de sensibilidad: si la herramienta no ve un +6 dB de agudos, un
    # resultado de 0,04 dB no significa nada. No es una configuración de la app:
    # es la prueba de que la medida sirve.
    control_agudos = procesar(señal, treble=6.0)
    control_eq = procesar(señal, eq_gains=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 6.0, 6.0])
    fabrica = procesar(
        señal,
        bass=PRESET_FABRICA["bass"],
        treble=PRESET_FABRICA["treble"],
        eq_gains=list(PRESET_FABRICA["eq_gains"]),
        effects=dict(PRESET_FABRICA["effects"]),
        leveling=PRESET_FABRICA["leveling"],
    )

    # Referencia: la relación sibilante/cuerpo de la señal de ENTRADA.
    ref_sib = energia_banda(señal, BANDA_SIBILANTE)
    ref_cuerpo = energia_banda(señal, BANDA_CUERPO)
    print(
        f"entrada: sibilante {ref_sib:.4f}  cuerpo {ref_cuerpo:.4f}  "
        f"relacion {20 * np.log10(ref_sib / ref_cuerpo):+.2f} dB\n"
    )

    filas = (
        ("neutro (todo apagado)", neutra),
        ("CONTROL: agudos +6 dB", control_agudos),
        ("CONTROL: EQ +6 dB en 8k/12k", control_eq),
        ("solo Clarity (fidelity=4)", solo_clarity),
        ("solo Bass exciter (bass=3)", solo_exciter),
        ("preset 'Recomendado (altavoces)'", fabrica),
    )
    print(f"{'configuracion':36s} {'sib/cuerpo':>12s} {'vs neutro':>10s} {'sib/total':>10s} {'sib abs':>9s}")
    base_sib = None
    for nombre, salida in filas:
        sib = energia_banda(salida, BANDA_SIBILANTE)
        cuerpo = energia_banda(salida, BANDA_CUERPO)
        total = energia_banda(salida, (20.0, FS / 2))
        relacion = 20 * np.log10(max(sib, 1e-12) / max(cuerpo, 1e-12))
        if base_sib is None:
            base_sib = relacion
            delta = 0.0
        else:
            delta = relacion - base_sib
        absoluta = 20 * np.log10(max(sib, 1e-12))
        print(
            f"{nombre:36s} {relacion:+11.2f} dB {delta:+9.2f} dB "
            f"{20 * np.log10(max(sib, 1e-12) / max(total, 1e-12)):+9.2f} dB {absoluta:+8.2f} dB"
        )

    final = 20 * np.log10(max(energia_banda(fabrica, BANDA_SIBILANTE), 1e-12)) - 20 * np.log10(
        max(energia_banda(neutra, BANDA_SIBILANTE), 1e-12)
    )
    rel_cuerpo = 20 * np.log10(
        max(energia_banda(fabrica, BANDA_SIBILANTE), 1e-12) / max(energia_banda(fabrica, BANDA_CUERPO), 1e-12)
    ) - 20 * np.log10(
        max(energia_banda(neutra, BANDA_SIBILANTE), 1e-12) / max(energia_banda(neutra, BANDA_CUERPO), 1e-12)
    )
    print(f"\nsibilante absoluta (fabrica vs neutro): {final:+.2f} dB")
    print(f"sibilante respecto al cuerpo (fabrica vs neutro): {rel_cuerpo:+.2f} dB")
    # Donde actua Clarity de verdad: rectifica agudos y genera armonicos POR
    # ENCIMA de la banda original (12-18 kHz), no dentro de ella. Conviene verlo
    # para no confundir "no toca la sibilante" con "no hace nada".
    armonicos = (12000.0, 18000.0)
    ref_arm = energia_banda(neutra, armonicos)
    print("\narmonicos 12-18 kHz (donde Clarity si actua):")
    for nombre, salida in filas:
        energia = energia_banda(salida, armonicos)
        print(f"  {nombre:36s} {20 * np.log10(max(energia, 1e-12) / max(ref_arm, 1e-12)):+6.2f} dB vs neutro")

    if rel_cuerpo < 1.0:
        print("\nVEREDICTO: < 1 dB -> el de-esser NO entra en la 1.8 (se documenta y se cierra).")
    elif rel_cuerpo < 3.0:
        print("\nVEREDICTO: entre 1 y 3 dB -> gris. Se documenta y decide el usuario.")
    else:
        print("\nVEREDICTO: >= 3 dB -> hay problema real y el de-esser entra en la 1.8.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
