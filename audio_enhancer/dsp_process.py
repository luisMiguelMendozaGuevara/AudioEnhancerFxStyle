"""Proceso hijo del DSP: aísla el procesamiento del proceso principal (GIL).

Motivo (medido): la cadena DSP real en Python (muchas operaciones numpy por
bloque) compite por el GIL con el callback de SALIDA de PortAudio y lo retrasa;
la salida consume menos, el ring rebosa y se descarta audio (microcortes/lag).
Con el DSP en un proceso aparte, ese trabajo no compite con el callback.

Interfaz:
    child_main(raw_q, out_q, params_q, stop_event)
- raw_q:   cola de bloques crudos (bytes float32 intercalados) main -> hijo.
- out_q:   cola de bloques procesados (bytes) hijo -> main.
- params_q: cola de snapshots de parámetros (dict) main -> hijo (el último gana).
- stop_event: threading/multiprocessing Event para terminar.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("audio_enhancer.dsp_process")

# Atributos públicos del Enhancer que la UI puede cambiar y que hay que
# replicar en el proceso hijo.
PARAM_ATTRS = (
    "sample_rate",
    "volume",
    "bass",
    "treble",
    "bass_freq",
    "treble_freq",
    "eq_q",
    "limiter",
    "limiter_threshold",
    "limiter_strength",
    "compressor",
    "comp_threshold",
    "comp_ratio",
    "comp_attack",
    "comp_release",
    "comp_makeup",
    "blend",
    "true_peak",
    "safety_ceiling_enabled",
    "safety_ceiling",
    "final_clip_enabled",
    "crossfeed",
    "crossfeed_preset",
    "crossfeed_cut_hz",
    "crossfeed_feed_db",
)


def snapshot_params(enhancer) -> dict:
    """Snapshot de los parámetros replicables del Enhancer."""
    snap = {}
    for name in PARAM_ATTRS:
        value = getattr(enhancer, name, None)
        if value is not None:
            snap[name] = value
    snap["eq_gains"] = [float(g) for g in enhancer.eq_gains]
    return snap


def _apply(enhancer, snap: dict) -> None:
    rate_changed = snap.get("sample_rate") is not None and snap["sample_rate"] != enhancer.sample_rate
    for name, value in snap.items():
        if name == "eq_gains":
            continue
        setattr(enhancer, name, value)
    gains = snap.get("eq_gains")
    if gains is not None:
        enhancer.eq_gains = [float(g) for g in gains]
    if rate_changed and hasattr(enhancer, "reset_state"):
        # Cambió la tasa: los estados zi son de otra tasa -> reiniciar.
        enhancer.reset_state()


def _drain_params(enhancer, params_q) -> None:
    latest = None
    while True:
        try:
            latest = params_q.get_nowait()
        except Exception:
            break
    if latest is not None:
        _apply(enhancer, latest)


def child_main(raw_q, out_q, params_q, stop_event) -> None:
    """Bucle del proceso hijo: crudo -> DSP -> procesado."""
    from .dsp import Enhancer

    enhancer = Enhancer()
    _drain_params(enhancer, params_q)
    logger.info("Proceso DSP (hijo) iniciado")
    while not stop_event.is_set():
        try:
            item = raw_q.get(timeout=0.1)
        except Exception:
            _drain_params(enhancer, params_q)
            continue
        if item is None:  # centinela de parada
            break
        _drain_params(enhancer, params_q)
        try:
            data = np.frombuffer(item, dtype=np.float32).reshape(-1, 2)
            y = enhancer.process(data)
        except Exception:
            logger.exception("Error en el proceso DSP")
            continue
        out_q.put(np.asarray(y, dtype=np.float32).tobytes())
    logger.info("Proceso DSP (hijo) detenido")
