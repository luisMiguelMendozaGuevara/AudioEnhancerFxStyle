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
import threading
import time

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
    "spectrum_enabled",
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


def child_main(
    raw_q,
    out_q,
    params_q,
    stop_event,
    gr_value=None,
    telemetry=None,
    spectrum_buf=None,
    spec_needed=None,
) -> None:
    """Bucle del proceso hijo: crudo -> DSP -> procesado.

    El bucle de audio NO calcula el FFT del espectro: lo hace un hilo aparte
    (``_viz_loop``) para que la gráfica nunca retrase el DSP. Publica en
    memoria compartida lo que consume la UI (para no hacerlo en el proceso
    principal, donde competiría con el callback de salida):
    - ``gr_value``: gain reduction del limitador.
    - ``telemetry``: [rms, peak, rms_l, rms_r, peak_l, peak_r, gr].
    - ``spectrum_buf``: 64 barras de espectro (dB), escritas por el hilo visual.
    - ``spec_needed``: bandera (padre -> hijo) de si la UI mira el espectro.
    """
    from .dsp import Enhancer

    enhancer = Enhancer()
    _drain_params(enhancer, params_q)
    logger.info("Proceso DSP (hijo) iniciado")
    viz = threading.Thread(
        target=_viz_loop,
        args=(enhancer, spectrum_buf, spec_needed, stop_event),
        name="viz",
        daemon=True,
    )
    viz.start()
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
        if telemetry is not None:
            with telemetry.get_lock():
                telemetry[0] = enhancer.level_rms
                telemetry[1] = enhancer.level_peak
                telemetry[2] = enhancer.level_rms_l
                telemetry[3] = enhancer.level_rms_r
                telemetry[4] = enhancer.level_peak_l
                telemetry[5] = enhancer.level_peak_r
                telemetry[6] = enhancer.level_gr
        if gr_value is not None:
            with gr_value.get_lock():
                gr_value.value = float(enhancer.level_gr)
        out_q.put(np.asarray(y, dtype=np.float32).tobytes())
    stop_event.set()
    viz.join(timeout=1.0)
    logger.info("Proceso DSP (hijo) detenido")


def _viz_loop(enhancer, spectrum_buf, spec_needed, stop_event) -> None:
    """Hilo de visualización (baja prioridad) del hijo.

    Calcula el FFT del espectro FUERA del bucle de audio: si va lento, se salta
    refrescos (la gráfica lag), pero NUNCA retrasa el DSP. Solo trabaja cuando
    la UI necesita el espectro (``spec_needed``) y hay audio. El snapshot lo
    publica ``Enhancer.process`` en el bucle de audio (mismo proceso)."""
    while not stop_event.is_set():
        if spectrum_buf is None or not enhancer.spectrum_enabled or (spec_needed is not None and not spec_needed.value):
            time.sleep(0.1)
            continue
        try:
            enhancer.compute_spectrum()
            spec = enhancer.spectrum
            if spec is not None:
                with spectrum_buf.get_lock():
                    for i in range(min(len(spec), len(spectrum_buf))):
                        spectrum_buf[i] = float(spec[i])
        except Exception:
            logger.debug("compute_spectrum falló en el hilo visual", exc_info=True)
        time.sleep(0.03)  # ~33 Hz máx: cadencia visual, no audio
