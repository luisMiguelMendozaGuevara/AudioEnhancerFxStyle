"""Benchmark del camino de audio (DSP puro, sin Qt ni dispositivos).

Mide duración por bloque y allocaciones por bloque de ``Enhancer.process``
en escenarios representativos, más el coste del análisis de espectro.
Sirve como red de regresión de rendimiento: cualquier cambio en el
callback de audio (rutas DSP, caches, salidas tempranas) debe venir
acompañado de una comparación antes/después con este script.

Uso:
    python benchmarks/bench_dsp.py                 # tabla legible
    python benchmarks/bench_dsp.py --json salida   # resultados machine-readable

Escenarios:
    bypass      blend=0 (camino directo B, sin DSP)
    eq_only     solo filtros (compresor y limitador apagados)
    full        cadena completa (EQ + compresor + limitador true-peak),
                señal moderada por debajo de umbrales (caso común)
    full_hot    cadena completa con señal sobre umbrales (compresión y
                limitación activas, peor caso de CPU)
    full_hot_sin_limiter  igual pero con el limitador APAGADO y señal que
                cruza el techo de seguridad en todos los bloques
    spectrum    compute_spectrum() por tick (hilo de UI, no callback)

Métricas por escenario (µs/bloque sobre CHUNK=1024 @48 kHz ≈ 21,3 ms):
    p50 / p95 / max  — duración del proceso
    alloc            — bytes nuevos por bloque en régimen estable
                       (tracemalloc, caches ya calientes)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from audio_enhancer.constants import CHUNK, SAMPLE_RATE  # noqa: E402
from audio_enhancer.dsp import Enhancer  # noqa: E402

WARMUP_BLOCKS = 30
MEASURE_BLOCKS = 400
RNG_SEED = 1234


def _make_enhancer(**overrides) -> Enhancer:
    enh = Enhancer()
    enh.sample_rate = SAMPLE_RATE
    # Cadena por defecto: EQ con varias bandas activas + compresor + limitador.
    enh.eq_gains = [4.0, 2.0, 0.0, -3.0, 0.0, 2.5, 4.0, 0.0, -2.0]
    enh.bass = 3.0
    enh.treble = 2.0
    for key, value in overrides.items():
        setattr(enh, key, value)
    return enh


def _signal_hot(n: int, rng: np.random.Generator) -> np.ndarray:
    """Señal con transitorios sobre el techo tras volumen>1 (limitador activo)."""
    t = np.arange(n) / SAMPLE_RATE
    base = 0.45 * np.sin(2 * np.pi * 220.0 * t)
    # "Golpes" cada ~0,4 s: picos de hasta ~1,2 en la suma.
    beats = (np.sin(2 * np.pi * 2.5 * t) > 0.93).astype(np.float32)
    perc = beats * 0.9 * rng.standard_normal(n).astype(np.float32)
    x = np.stack([base + perc, base * 0.9 + perc], axis=1)
    return (x * 1.15).astype(np.float32)


def _signal_moderate(n: int, rng: np.random.Generator) -> np.ndarray:
    """Música moderada: pico ~0,4, bajo el umbral de compresor/limitador."""
    t = np.arange(n) / SAMPLE_RATE
    x = 0.3 * np.sin(2 * np.pi * 330.0 * t) + 0.1 * rng.standard_normal(n)
    st = np.stack([x, 0.95 * x], axis=1)
    return st.astype(np.float32)


def _signal_over_ceiling(n: int, rng: np.random.Generator) -> np.ndarray:
    """Señal calibrada para que el techo de seguridad entre en TODOS los
    bloques (limitador apagado): normalizada a pico ~1,2 antes del volumen
    (1,4), de modo que tras el EQ y el volumen cruce el techo 0,99 con
    holgura. Sin esto, la realización del rng puede dejar la señal "hot"
    por debajo del techo y el escenario deja de medir el peor caso."""
    x = _signal_hot(n, rng)
    peak = float(np.abs(x).max())
    return (x * (1.2 / peak)).astype(np.float32)


def _time_blocks(enh: Enhancer, feed, n_blocks: int) -> list[float]:
    """Devuelve duraciones en µs de process() (feed entrega el bloque i)."""
    samples: list[float] = []
    for i in range(n_blocks):
        block = feed(i)
        t0 = time.perf_counter_ns()
        enh.process(block)
        samples.append((time.perf_counter_ns() - t0) / 1000.0)
    return samples


def _alloc_per_block(enh: Enhancer, feed, n_blocks: int) -> float:
    """Bytes asignados por bloque en régimen estable (tracemalloc)."""
    tracemalloc.start()
    base_current, _ = tracemalloc.get_traced_memory()
    for i in range(n_blocks):
        enh.process(feed(i))
    end_current, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return max(0.0, (end_current - base_current) / n_blocks)


def _stats(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    return {
        "p50_us": round(statistics.median(ordered), 1),
        "p95_us": round(p95, 1),
        "max_us": round(ordered[-1], 1),
    }


def run_benchmarks(n_blocks: int = MEASURE_BLOCKS) -> dict:
    rng = np.random.default_rng(RNG_SEED)
    hot = _signal_hot(CHUNK, rng)
    moderate = _signal_moderate(CHUNK, rng)
    over_ceiling = _signal_over_ceiling(CHUNK, rng)

    scenarios: dict[str, tuple[Enhancer, object]] = {
        "bypass": (
            _make_enhancer(blend=0.0),
            lambda _i: moderate,
        ),
        "eq_only": (
            _make_enhancer(compressor=False, limiter=False),
            lambda _i: moderate,
        ),
        "full": (
            _make_enhancer(),
            lambda _i: moderate,
        ),
        "full_hot": (
            _make_enhancer(volume=1.4),
            lambda _i: hot,
        ),
        # Peor caso del techo de seguridad: limitador musical APAGADO con
        # señal que cruza el techo en todos los bloques (el look-ahead entra
        # como techo 0.99; mismo coste que el limitador true-peak activo).
        "full_hot_sin_limiter": (
            _make_enhancer(volume=1.4, limiter=False),
            lambda _i: over_ceiling,
        ),
    }

    results: dict[str, dict] = {}
    for name, (enh, feed) in scenarios.items():
        _time_blocks(enh, feed, WARMUP_BLOCKS)  # calentar caches LRU/zi
        samples = _time_blocks(enh, feed, n_blocks)
        entry = _stats(samples)
        entry["alloc_b_per_block"] = round(_alloc_per_block(enh, feed, 120), 1)
        results[name] = entry

    # Espectro: coste del análisis por tick (hilo de UI, cada ~33 ms).
    enh = _make_enhancer()
    enh.spectrum_enabled = True
    enh.process(moderate)
    samples = []
    for _ in range(200):
        t0 = time.perf_counter_ns()
        enh.compute_spectrum()
        samples.append((time.perf_counter_ns() - t0) / 1000.0)
    results["spectrum"] = _stats(samples)

    results["_meta"] = {
        "chunk": CHUNK,
        "sample_rate": SAMPLE_RATE,
        "block_ms": round(CHUNK / SAMPLE_RATE * 1000.0, 2),
        "warmup_blocks": WARMUP_BLOCKS,
        "measure_blocks": n_blocks,
        "python": sys.version.split()[0],
        "numpy": np.__version__,
    }
    return results


def _print_table(results: dict) -> None:
    meta = results["_meta"]
    print(f"_DSP benchmark — bloque de {meta['block_ms']} ms ({meta['chunk']} muestras @ {meta['sample_rate']} Hz)_\n")
    header = f"{'escenario':<10} {'p50 µs':>9} {'p95 µs':>9} {'max µs':>9} {'alloc B/blk':>12} {'%bloque(p95)':>13}"
    print(header)
    print("-" * len(header))
    budget_us = meta["block_ms"] * 1000.0
    for name in ("bypass", "eq_only", "full", "full_hot", "full_hot_sin_limiter", "spectrum"):
        entry = results.get(name)
        if not entry:
            continue
        pct = entry["p95_us"] / budget_us * 100.0
        alloc = entry.get("alloc_b_per_block", "-")
        print(
            f"{name:<10} {entry['p50_us']:>9.1f} {entry['p95_us']:>9.1f} "
            f"{entry['max_us']:>9.1f} {alloc:>12} {pct:>12.2f}%"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=str, default="", help="ruta para volcar resultados JSON")
    args = parser.parse_args()
    results = run_benchmarks()
    _print_table(results)
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nresultados volcados en {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
