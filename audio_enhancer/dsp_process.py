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

import contextlib
import logging
import math
import multiprocessing as mp
import os
import queue
import threading
import time
from typing import Any

import numpy as np

from .constants import CHUNK
from .diagnostics import RollingStats
from .loudness import LoudnessMeter
from .rt_priority import mmcss_pro_audio

logger = logging.getLogger("audio_enhancer.dsp_process")


def _finito(valor: float, defecto: float = -1000.0) -> float:
    """Valor publicable en la telemetría compartida (float32, sin inf/NaN).

    El integrado de un silencio es -inf y un Array('f') no lo representa: se
    sustituye por un suelo inequívoco (-1000 LUFS) para que la UI lo muestre
    como "--" sin tener que interpretar NaN."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return float(defecto)
    if not math.isfinite(numero):
        return float(defecto)
    return numero


def _parent_alive(parent_pid: int) -> bool:
    """True si el proceso padre sigue vivo (Windows: handle + wait 0).

    Si el principal muere a lo bruto (Administrador de tareas o crash nativo),
    este hijo seguiria capturando/procesando en silencio para siempre. Ante
    cualquier duda devuelve True: nunca se mata al hijo por un fallo de la
    comprobacion."""""
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102
        ERROR_INVALID_PARAMETER = 87
        # use_last_error: captura el error DE LA PROPIA LLAMADA (un
        # GetLastError() suelto puede leer un valor pisado entre medias).
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.OpenProcess(SYNCHRONIZE, False, int(parent_pid))
        if not handle:
            # 87 = PID inexistente (muerto); otro error (p. ej. acceso) -> vivo.
            return ctypes.get_last_error() != ERROR_INVALID_PARAMETER
        try:
            return k32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return True


DEFAULT_BLOCK_BYTES = CHUNK * 2 * 4  # 1024 frames estéreo float32 = 8192 bytes


class SharedBlockQueue:
    """Cola circular SPSC en memoria compartida (ctx.Array + ctx.Event).

    Sustituye a ``multiprocessing.Queue`` para el transporte de bloques de audio
    entre el proceso principal y el proceso hijo del DSP:
    - Cero hilos *feeder* internos ni tuberías (pipes) del SO.
    - Cero serialización ``pickle`` (copia directa de bytes sobre ``c_ubyte``).
    - Compatible con ``spawn`` en Windows y con PyInstaller (herencia de handles
      de ``ctx.Array`` / ``ctx.Event`` sin ``resource_tracker``).
    """

    def __init__(
        self,
        maxsize: int = 128,
        max_block_bytes: int = DEFAULT_BLOCK_BYTES,
        ctx: Any = None,
        notify_event: Any = None,
    ) -> None:
        self._maxsize = max(1, int(maxsize))
        self.maxsize = self._maxsize
        self._slot_bytes = max(64, int(max_block_bytes))
        self._is_mp = isinstance(ctx, mp.context.BaseContext)
        if self._is_mp:
            self._buf: Any = ctx.Array("B", self._maxsize * self._slot_bytes, lock=False)
            self._lengths: Any = ctx.Array("i", self._maxsize, lock=False)
            # [write_idx, read_idx, count] protegidos por el lock del propio Array.
            self._meta: Any = ctx.Array("i", 3)
            self._lock: Any = None
            self._not_empty: Any = ctx.Event()
        else:
            self._buf = bytearray(self._maxsize * self._slot_bytes)
            self._lengths = [0] * self._maxsize
            self._meta = [0, 0, 0]
            self._lock = threading.Lock()
            self._not_empty = ctx.Event() if (ctx is not None and hasattr(ctx, "Event")) else threading.Event()
        self._notify_event = notify_event

    def _guard(self):
        return self._meta.get_lock() if self._lock is None else self._lock

    def qsize(self) -> int:
        with self._guard():
            return int(self._meta[2])

    def full(self) -> bool:
        with self._guard():
            return int(self._meta[2]) >= self._maxsize

    def put_nowait(self, item: bytes | None) -> None:
        with self._guard():
            count = int(self._meta[2])
            if count >= self._maxsize:
                raise queue.Full
            w = int(self._meta[0])
            if item is None:
                self._lengths[w] = -1
            else:
                b = bytes(item)
                nb = len(b)
                if nb > self._slot_bytes:
                    raise ValueError(f"Bloque excede slot compartido: {nb} > {self._slot_bytes}")
                start = w * self._slot_bytes
                np.frombuffer(self._buf, dtype=np.uint8)[start : start + nb] = np.frombuffer(b, dtype=np.uint8)
                self._lengths[w] = nb
            self._meta[0] = (w + 1) % self._maxsize
            self._meta[2] = count + 1
            self._not_empty.set()
        if self._notify_event is not None:
            with contextlib.suppress(Exception):
                self._notify_event.set()

    def put(self, item: bytes | None, timeout: float | None = None) -> None:
        # Nunca bloquea: si la cola está llena, descarta el bloque más viejo
        # (la salida reciente importa más que la vieja) y reintenta; si otro
        # productor la llenó en la carrera, se repite el descarte.
        while True:
            try:
                self.put_nowait(item)
                return
            except queue.Full:
                with self._guard():
                    count = int(self._meta[2])
                    if count >= self._maxsize:
                        self._meta[1] = (int(self._meta[1]) + 1) % self._maxsize
                        self._meta[2] = count - 1

    def get_nowait(self) -> bytes | None:
        with self._guard():
            count = int(self._meta[2])
            if count <= 0:
                self._not_empty.clear()
                raise queue.Empty
            r = int(self._meta[1])
            nb = int(self._lengths[r])
            if nb < 0:
                out = None
            else:
                start = r * self._slot_bytes
                out = np.frombuffer(self._buf, dtype=np.uint8)[start : start + nb].tobytes()
            self._meta[1] = (r + 1) % self._maxsize
            new_count = count - 1
            self._meta[2] = new_count
            if new_count <= 0:
                self._not_empty.clear()
            return out

    def get(self, timeout: float | None = None) -> bytes | None:
        deadline = None if timeout is None else (time.monotonic() + max(0.0, float(timeout)))
        while True:
            try:
                return self.get_nowait()
            except queue.Empty:
                if deadline is not None:
                    rem = deadline - time.monotonic()
                    if rem <= 0.0:
                        raise queue.Empty from None
                    self._not_empty.wait(min(rem, 0.05))
                else:
                    self._not_empty.wait(0.05)


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
    # Capa enhancer (Fase 0): balance/master (dB) y leveling (0-10). El dict
    # `effects` se replica aparte (copia explícita, como eq_gains).
    "balance_db",
    "master_gain_db",
    "leveling",
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
    # Fase 5: frecuencias centrales por banda (el Q global va en PARAM_ATTRS).
    snap["eq_bands"] = [float(f) for f in enhancer.eq_bands]
    # Copia del dict de efectos (nunca la referencia viva: la UI lo muta).
    snap["effects"] = {str(k): float(v) for k, v in dict(getattr(enhancer, "effects", {})).items()}
    return snap


def _apply(enhancer, snap: dict) -> None:
    rate_changed = snap.get("sample_rate") is not None and snap["sample_rate"] != enhancer.sample_rate
    for name, value in snap.items():
        if name in ("eq_gains", "eq_bands", "effects"):
            continue
        setattr(enhancer, name, value)
    bands = snap.get("eq_bands")
    if bands:
        # Solo tocar las bandas si CAMBIARON: set_eq_bands limpia estados y
        # cachés del EQ, y llamarlo en cada snapshot (al mover CUALQUIER
        # control) metía transitorios y recomputaba sin motivo.
        new_bands = [float(f) for f in bands]
        if new_bands != enhancer.eq_bands:
            enhancer.set_eq_bands(new_bands)
    gains = snap.get("eq_gains")
    if gains is not None:
        enhancer.eq_gains = [float(g) for g in gains]
    effects = snap.get("effects")
    if isinstance(effects, dict):
        enhancer.effects = {str(k): float(v) for k, v in effects.items()}
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
    child_stats=None,
    child_timing=None,
) -> None:
    """Bucle del proceso hijo: crudo -> DSP -> procesado.

    El bucle de audio NO calcula el FFT del espectro: lo hace un hilo aparte
    (``_viz_loop``) para que la gráfica nunca retrase el DSP. Publica en
    memoria compartida lo que consume la UI (para no hacerlo en el proceso
    principal, donde competiría con el callback de salida):
    - ``gr_value``: gain reduction del limitador.
    - ``telemetry``: [rms, peak, rms_l, rms_r, peak_l, peak_r, gr] y, si la
      malla compartida tiene 10 huecos, también [lufs_m, lufs_s, lufs_i].
      Sonoridad medida segun ITU-R BS.1770 / EBU R 128 sobre la salida.
    - ``spectrum_buf``: 64 barras de espectro (dB), escritas por el hilo visual.
    - ``spec_needed``: bandera (padre -> hijo) de si la UI mira el espectro.
    - ``child_stats``: [raw_empty, blocks, backlog_sum, clipped] (diagnóstico).
    - ``child_timing``: [p50, p95, p99, max] ms de ``process()`` por bloque.
    """
    from .dsp import Enhancer

    enhancer = Enhancer()
    _drain_params(enhancer, params_q)
    # Medidor de sonoridad ('EBU Mode'): mide la SALIDA (lo que oye el usuario),
    # no la entrada, para que sirva de referencia y de base al AGC futuro.
    # Coste medido: ~0.13 ms por bloque de 1024 (0.6 % del presupuesto).
    medidor = LoudnessMeter(sample_rate=enhancer.sample_rate or 48000, channels=2)
    # Pre-importar scipy AQUÍ, antes del primer bloque: si no, la primera
    # llamada a process() importa scipy.signal/ndimage DENTRO del bucle de
    # audio y retrasa el primer bloque varios segundos (huecos al iniciar).
    with contextlib.suppress(Exception):
        from .dsp import _scipy_ndimage, _scipy_signal

        _scipy_signal()
        _scipy_ndimage()
    logger.info("Proceso DSP (hijo) iniciado")
    viz = threading.Thread(
        target=_viz_loop,
        args=(enhancer, spectrum_buf, spec_needed, stop_event),
        name="viz",
        daemon=True,
    )
    viz.start()
    # Diagnóstico (Paso 0): contadores locales que se publican junto a la
    # telemetría (así no se toma el lock del array compartido dos veces).
    raw_empty = 0
    blocks = 0
    backlog_sum = 0
    clipped = 0
    proc_errors = 0
    last_proc_error_log = 0.0
    # Estadística del coste por bloque: se publica cada PUBLISH_EVERY bloques
    # (un snapshot ordena la ventana; publicarlo en cada bloque sería gastar el
    # presupuesto de 21 ms en medir en vez de procesar).
    timing = RollingStats(window=256)
    publish_every = 8
    # MMCSS "Pro Audio": registra ESTE hilo en el planificador multimedia de
    # Windows (prioridades 23-26). PortAudio hace lo mismo con sus callbacks,
    # pero el hilo que procesa el DSP no lo hacía: bajo carga perdía CPU frente
    # a tareas de fondo, el ring de salida se vaciaba y se oían huecos. No-op
    # fuera de Windows o si avrt.dll no está disponible (nunca lanza).
    mmcss = mmcss_pro_audio("Pro Audio")
    mmcss.__enter__()
    # Vigilancia del padre: si muere a lo bruto, este proceso (que ya no puede
    # alimentar audio util) termina solo en vez de quedar huerfano.
    parent_pid = os.getppid()
    last_parent_check = 0.0
    while not stop_event.is_set():
        now = time.monotonic()
        if now - last_parent_check >= 1.0:
            last_parent_check = now
            if not _parent_alive(parent_pid):
                logger.warning("El proceso padre desaparecio; el DSP hijo termina")
                break
        try:
            item = raw_q.get(timeout=0.1)
        except Exception:
            # Sin crudo: el hijo va al día. Si esto se repite, el hijo se queda
            # sin alimentar (bomba lenta o productor parado): diagnóstico.
            raw_empty += 1
            _drain_params(enhancer, params_q)
            continue
        if item is None:  # centinela de parada
            break
        _drain_params(enhancer, params_q)
        try:
            with contextlib.suppress(Exception):
                backlog_sum += raw_q.qsize()
            data = np.frombuffer(item, dtype=np.float32).reshape(-1, 2)
            t0 = time.perf_counter()
            y = enhancer.process(data)
            timing.add(time.perf_counter() - t0)
            # Sonoridad de la señal procesada (una vez por bloque y antes de
            # publicar telemetría, para que ambas cosas sean coherentes).
            medidor.process(y)
            blocks += 1
            # Recorte: muestras que superan ±1.0 (posible recorte del driver
            # cuando no hay limitador/techo). Barato y fuera del audio crítico.
            clipped += int(np.count_nonzero(np.abs(y) > 1.0))
        except Exception:
            # Rate-limit: si process() falla en CADA bloque (p. ej. un estado
            # corrupto), loguear por bloque inunda el log rotativo (256 KB x2)
            # y se pierde el historial justo cuando hace falta. Se avisa del
            # primero y luego uno cada 5 s, con el total de fallos.
            proc_errors += 1
            now_err = time.monotonic()
            if proc_errors == 1 or (now_err - last_proc_error_log) >= 5.0:
                last_proc_error_log = now_err
                logger.exception("Error en el proceso DSP (%d fallos)", proc_errors)
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
                if len(telemetry) >= 10:
                    # Medir la sonoridad de la SALIDA con los valores ya
                    # cacheados (el recálculo del integrado está caché por
                    # bloque, no se hace 46 veces por segundo).
                    medidas = medidor.snapshot()
                    telemetry[7] = _finito(medidas["momentary_lufs"])
                    telemetry[8] = _finito(medidas["short_term_lufs"])
                    telemetry[9] = _finito(medidas["integrated_lufs"])
        if child_stats is not None:
            with child_stats.get_lock():
                child_stats[0] = raw_empty
                child_stats[1] = blocks
                child_stats[2] = backlog_sum
                child_stats[3] = clipped
        if child_timing is not None and blocks % publish_every == 0:
            snap = timing.snapshot()
            with contextlib.suppress(Exception), child_timing.get_lock():
                child_timing[0] = snap["p50_ms"]
                child_timing[1] = snap["p95_ms"]
                child_timing[2] = snap["p99_ms"]
                child_timing[3] = snap["max_ms"]
        if gr_value is not None:
            with gr_value.get_lock():
                gr_value.value = float(enhancer.level_gr)
        out_q.put(np.asarray(y, dtype=np.float32).tobytes())
    mmcss.__exit__(None, None, None)
    if child_stats is not None:
        with contextlib.suppress(Exception), child_stats.get_lock():
            child_stats[0] = raw_empty
            child_stats[1] = blocks
            child_stats[2] = backlog_sum
            child_stats[3] = clipped
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
