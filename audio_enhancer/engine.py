"""Motor de audio: ring buffer + callbacks de PortAudio con compensación de deriva.

Separa la captura (loopback WASAPI, paquetes irregulares) de la salida física
para evitar los OutputUnderflow del modo full-duplex. Los callbacks se ejecutan
en hilos de audio: solo tocan estado interno bajo ``self.lock`` y nunca llaman a
Tk. ``start_capture``/``open_output``/``stop`` permiten un arranque no bloqueante
orquestado desde el hilo de la UI.
"""

import contextlib
import logging
import multiprocessing as mp
import os
import threading
import time
from functools import lru_cache
from typing import Any

import numpy as np

from .constants import CHUNK, DRIFT_TARGET_MS, RING_SECONDS, STARTUP_MUTE_S

logger = logging.getLogger("audio_enhancer.engine")

# Degradación elegante en underrun: si al pedir n frames al ring faltan pocos
# (ratio >= este umbral), se devuelven los disponibles y el remuestreador los
# ESTIRA al bloque pedido, en vez de insertar silencio (hueco audible de unos
# ms). Medido en el caso real del CABLE: los huecos eran de ~63 frames sobre
# 1024 (~6%), dentro de la zona. El estirado por bloque queda acotado por este
# ratio (0.9 = hasta ~11% de pitch en un bloque); por debajo se trata como
# hueco real y se aplica el silencio con fundidos.
UNDERFLOW_GRACE_RATIO = 0.9

_pa_mod = None


def _pa():
    """Import perezoso de pyaudiowpatch (arranque más rápido)."""
    global _pa_mod
    if _pa_mod is None:
        import pyaudiowpatch

        _pa_mod = pyaudiowpatch
    return _pa_mod


@lru_cache(maxsize=8)
def _downmix_matrix(ch: int) -> np.ndarray:
    """Matriz de mezcla n->estéreo (filas: canal de entrada; columnas L/R).

    Orden WASAPI/WAVEFORMATEXTENSIBLE por convención: FL FR C LFE SL SR...
    Pesadas estándar de downmix (ITU-ish): central y surround a -3 dB, LFE
    atenuado (suele venir ya mezclado en FL/FR del loopback)."""
    w = np.zeros((ch, 2), dtype=np.float32)
    if ch == 1:
        w[0, :] = 1.0  # mono a ambos
        return w
    w[0, 0] = 1.0  # FL -> L
    w[1, 1] = 1.0  # FR -> R
    if ch > 2:
        w[2, :] = 0.707  # C
    if ch > 3:
        w[3, :] = 0.3  # LFE
    if ch > 4:
        w[4, 0] = 0.707  # SL
    if ch > 5:
        w[5, 1] = 0.707  # SR
    if ch > 6:
        w[6:, :] = 0.5  # canales extra (7.1, ambientes)
    return w


def _downmix_to_stereo(x: np.ndarray) -> np.ndarray:
    """Mezcla (n, ch) con ch>2 a (n, 2) con la matriz cacheada."""
    return (x @ _downmix_matrix(x.shape[1])).astype(np.float32, copy=False)


class AudioEngine:
    """Ring buffer (N, 2) + streams PortAudio con callbacks de captura/salida."""

    def __init__(self, enhancer) -> None:
        self.enhancer = enhancer
        self.lock = threading.Lock()
        self.pa: Any = None
        self._pa_mod: Any = None  # módulo pyaudiowpatch (para paFloat32/paContinue)
        self.stream: Any = None  # captura (input)
        self.out_stream: Any = None  # salida (output)
        # Estado del ring
        self.ring: np.ndarray | None = None
        # write_pos/read_pos: el productor (callback de captura) escribe en
        # write_pos y el consumidor (callback de salida) lee en read_pos. Antes
        # se llamaban rhead/whead, que sugerían lo contrario.
        self.write_pos: int = 0
        self.read_pos: int = 0
        self.fadein_frames: int = 0
        self.in_gap: bool = False
        self.nframes: int = 0
        self.rate: int = 0  # tasa configurada (para recalcular la consigna)
        self._fade: int = 1
        # Control de deriva alrededor del punto medio del ring
        # (los valores se ajustan en configure_ring; aqui quedan los por
        # defecto para construccion directa en tests)
        self._drift_target: int = 0
        self._drift_deadband: int = 0
        self._drift_gain: float = 0.02
        self._drift_accum: float = 0.0
        # Autoridad del resampler de deriva: 16 frames/bloque. Más autoridad
        # (24) corregía antes pero modulaba el tono (±2.3 %) y se oía "cambiar"
        # el audio; 16 es el equilibrio (con 8 el ring se desbordaba).
        self._max_drift_frames: int = 16
        # Canales negociados en la captura (el callback mezcla a estéreo si
        # el loopback entrega más de 2).
        self._capture_channels: int = 2
        # (ARQ) DSP FUERA del callback de captura. El callback de PortAudio solo
        # COPIA crudo al ring de entrada; un hilo dedicado lee, procesa y
        # escribe al ring de salida. Motivo: con carga alta (vol 2x + EQ +
        # limitador true-peak, ~1.8 ms/bloque) el callback no devolvía a tiempo
        # y PortAudio DESCARTABA input (capt/s < 48000) -> microcortes/estática.
        self._raw_ring: np.ndarray | None = None
        self._raw_write: int = 0
        self._raw_read: int = 0
        self._dsp_thread: threading.Thread | None = None
        self._dsp_running: bool = False
        # Evento para despertar/parar el hilo DSP sin polling con sleep
        # (menos wakeups y parada inmediata en stop()).
        self._dsp_wakeup = threading.Event()
        # DSP EXTERNO (proceso hijo): aísla el DSP del hilo del callback de
        # salida (la contención de GIL retrasaba ese callback y descartaba
        # audio). En tests se deja False (se usa el hilo DSP in-process/drain).
        self.use_dsp_process: bool = False
        self._dsp_proc: Any = None
        self._raw_q: Any = None
        self._out_q: Any = None
        self._params_q: Any = None
        self._mp_stop: Any = None
        self._gr: Any = None  # value compartido: gain reduction del limitador
        # Telemetría visual (hijo -> padre): niveles (7 floats) y espectro (64).
        self._telemetry: Any = None
        self._spectrum_buf: Any = None
        # Fin del silencio de arranque (monotonic). Mientras now < esto, la
        # salida emite silencio y el ring se recorta a la consigna para no
        # descartar audio a golpes mientras el dispositivo físico alcanza su
        # reloj real (>0 => no activo; se fija en open_output).
        self._warmup_until: float = 0.0
        # Contexto del remuestreador fraccional: 2 últimas muestras del bloque
        # de entrada anterior (interpolación continua entre callbacks).
        self._interp_tail: np.ndarray | None = None
        # Métricas en vivo (contadores desde los callbacks; leer vía
        # stats_snapshot() desde la UI).
        self._stats: dict[str, int] = {
            "captured_frames": 0,
            "dropped_frames": 0,
            "gap_blocks": 0,
            "drift_adjust_frames": 0,
            "output_underruns": 0,
            "input_overflows": 0,
            # Underruns pequeños absorbidos estirando (sin silencio audible).
            "grace_stretches": 0,
            # Frames de SILENCIO insertados por el hueco (subconjunto de los
            # frame_count de gap_blocks). Separa la causa: si en un intervalo
            # no hubo descartes ni overflows ni bajada del ring, el hueco no se
            # debe a un pico del DSP ni a sobrecarga de captura.
            "gap_frames": 0,
        }

    @property
    def drift_target(self) -> int:
        """Consigna de llenado del ring en frames (latencia objetivo)."""
        return self._drift_target

    def stats_snapshot(self) -> dict[str, int]:
        """Copia de las métricas en vivo (segura para el hilo de UI)."""
        with self.lock:
            return dict(self._stats)

    def configure_ring(self, rate: int, drift_target_ms: float | None = None) -> None:
        """Configura el ring para la tasa dada (tamaño, fundidos, deriva).

        ``drift_target_ms`` fija la LATENCIA objetivo (consigna del control de
        deriva) desacoplada del tamaño del ring: antes era nframes//2 = la
        mitad de RING_SECONDS (100 ms) siempre. Con 60 ms el ring sigue
        absorbiendo paquetes irregulares pero la latencia percibida cae a la
        mitad. ``None`` usa DRIFT_TARGET_MS."""
        nframes = int(rate * RING_SECONDS)
        self.nframes = nframes
        self.rate = int(rate)
        self.ring = np.zeros((nframes, 2), dtype=np.float32)
        # Ring de ENTRADA crudo (captura -> hilo DSP): ~1 s de margen. Absorbe
        # los tirones del callback y los picos del DSP sin descartar audio.
        self._raw_ring = np.zeros((rate, 2), dtype=np.float32)
        self._raw_write = 0
        self._raw_read = 0
        self.write_pos = 0
        self.read_pos = 0
        self.fadein_frames = 0
        self.in_gap = False
        self._interp_tail = None
        self._fade = max(1, int(rate * 0.005))  # fundido ~5 ms
        if drift_target_ms is None:
            drift_target_ms = DRIFT_TARGET_MS
        # Banda muerta estrecha (~0.3 ms): deja que el control ignore el ruido
        # del ring y NO deje acumular latencia. Antes era CHUNK/8 (~2.7 ms) y
        # eso dejaba que el ring derivara decenas de ms.
        self._drift_deadband = max(CHUNK // 64, int(rate * 0.00015))
        # Consigna acotada: ni tan baja que un chunk de hueco la vacíe, ni tan
        # alta que se acerque al borde del ring.
        upper = max(CHUNK, nframes - 4 * CHUNK)
        target = int(rate * drift_target_ms / 1000.0)
        self._drift_target = max(CHUNK, min(target, upper))
        self._drift_accum = 0.0
        self._warmup_until = 0.0

    def set_drift_target_ms(self, drift_target_ms: float) -> None:
        """Cambia la latencia objetivo EN CALIENTE (sin vaciar el ring).

        El control de deriva mueve el llenado hacia la nueva consigna a como
        mucho ``_max_drift_frames`` por bloque, así que el cambio es gradual
        (~1 s) y sin glitch: no hace falta reconstruir el ring."""
        if self.nframes <= 0 or self.rate <= 0:
            return
        upper = max(CHUNK, self.nframes - 4 * CHUNK)
        target = int(self.rate * drift_target_ms / 1000.0)
        self._drift_target = max(CHUNK, min(target, upper))

    def _open_capture_stream(self, pa, in_idx: int, rate: int, channels: int):
        # Mismo tamaño que la salida (CHUNK): el callback SOLO copia al ring
        # crudo (el DSP va en hilo dedicado), así que no necesita margen extra
        # y la captura fluye suave (un bloque de 4096 cada ~86 ms hacía oscilar
        # el ring 0..8192 -> lag y huecos; ver test_deriva_sostenida).
        return pa.open(
            format=self._pa_mod.paFloat32,
            channels=channels,
            rate=rate,
            frames_per_buffer=CHUNK,
            input=True,
            output=False,
            input_device_index=in_idx,
            stream_callback=self._cap_callback,
        )

    def start_capture(self, pa, in_idx: int, rate: int, device_info=None) -> None:
        """Abre y arranca la captura (loopback WASAPI).

        Negociación de canales: se pide estéreo; si el driver lo rechaza y el
        dispositivo declara más canales de entrada (loopbacks de salidas
        5.1/7.1, típico paInvalidChannelCount), se abre con los canales que
        tiene y el callback mezcla a estéreo (downmix)."""
        self.pa = pa
        self._pa_mod = _pa()
        self._capture_channels = 2
        try:
            self.stream = self._open_capture_stream(pa, in_idx, rate, 2)
        except OSError:
            max_in = 0
            if device_info:
                max_in = int(device_info.get("maxInputChannels", 0) or 0)
            if max_in <= 2:
                raise
            self._capture_channels = max_in
            logger.warning(
                "Captura estéreo rechazada; abriendo con %d canales y downmix a estéreo",
                max_in,
            )
            self.stream = self._open_capture_stream(pa, in_idx, rate, max_in)
        self.stream.start_stream()
        # Arrancar el DSP: en un proceso hijo (producción) o en un hilo local.
        if self.use_dsp_process:
            self._start_dsp_process()
        else:
            self._dsp_running = True
            self._dsp_wakeup.clear()
            self._dsp_thread = threading.Thread(target=self._dsp_loop, name="dsp", daemon=True)
            self._dsp_thread.start()

    def fill(self) -> int:
        """Frames disponibles en el ring (para pre-cargar la salida)."""
        with self.lock:
            return self.write_pos - self.read_pos

    def open_output(self, out_idx: int, rate: int) -> None:
        """Abre y arranca la salida física. Relanza la excepción si falla."""
        self.out_stream = self.pa.open(
            format=self._pa_mod.paFloat32,
            channels=2,
            rate=rate,
            frames_per_buffer=CHUNK,
            input=False,
            output=True,
            output_device_index=out_idx,
            stream_callback=self._out_callback,
        )
        self.out_stream.start_stream()
        # Silencio/descarte explícito de arranque: la salida física tarda en
        # alcanzar su reloj; mientras tanto la captura va más rápido y el motor
        # descartaría audio a golpes. Ver STARTUP_MUTE_S.
        self._warmup_until = time.monotonic() + STARTUP_MUTE_S

    def stop(self) -> None:
        """Detiene y cierra ambos streams (idempotente). Cierra también la
        captura si la salida nunca llegó a abrirse (evita fuga de stream)."""
        # Parar el DSP (hilo local o proceso hijo) ANTES de cerrar los streams.
        self._dsp_running = False
        self._dsp_wakeup.set()  # despierta el wait para que salga ya
        if self._dsp_thread is not None:
            self._dsp_thread.join(timeout=1.0)
            if self._dsp_thread.is_alive():
                logger.warning("El hilo de DSP no paró en 1 s; se abandona (daemon)")
            self._dsp_thread = None
        self._stop_dsp_process()
        for s in (self.stream, self.out_stream):
            if s is not None:
                try:
                    s.stop_stream()
                    s.close()
                except Exception:
                    # Cerrar dos veces o tras un error de PortAudio es esperable
                    # durante la limpieza: se deja rastro y se continúa.
                    logger.debug("Error al cerrar stream en stop()", exc_info=True)
        self.stream = None
        self.out_stream = None
        self.ring = None

    # ---------- pack / ring ----------

    def _put(self, data) -> None:
        n = len(data)
        nframes = self.nframes
        ring = self.ring
        if ring is None:  # sin configure_ring(): descartar, no reventar el hilo de audio
            return
        with self.lock:
            avail = self.write_pos - self.read_pos
            if avail + n > nframes:
                # descartar lo más viejo si la salida va más lenta
                drop = avail + n - nframes
                self.read_pos += drop
                self._stats["dropped_frames"] += drop  # métrica en vivo
                idx = self.read_pos % nframes
                if idx + drop <= nframes:
                    ring[idx : idx + drop] = 0.0
                else:
                    a = nframes - idx
                    ring[idx:] = 0.0
                    ring[: drop - a] = 0.0
            idx = self.write_pos % nframes
            if idx + n <= nframes:
                ring[idx : idx + n] = data
            else:
                a = nframes - idx
                ring[idx:] = data[:a]
                ring[: n - a] = data[a:]
            self.write_pos += n

    def _read(self, n):
        """Devuelve n frames para la salida. Llamar con self.lock tomado.

        Si el ring no tiene suficiente audio (la salida corrió más rápido que
        la captura) NO inserta un bloque seco de silencio: suaviza el hueco con
        fundidos de entrada/salida para que no haya chasquidos.
        """
        nframes = self.nframes
        ring = self.ring
        if ring is None:  # sin configure_ring(): silencio, no reventar el hilo de audio
            return np.zeros((n, 2), dtype=np.float32)
        avail = self.write_pos - self.read_pos
        if avail >= n:
            idx = self.read_pos % nframes
            if idx + n <= nframes:
                data = ring[idx : idx + n].copy()
            else:
                a = nframes - idx
                data = np.concatenate([ring[idx:], ring[: n - a]])
            self.read_pos += n
            # Solo se aplica fade-in al salir de un hueco. Mantener el estado
            # explícito evita perderlo en huecos consecutivos.
            if self.in_gap:
                f = min(self.fadein_frames or self._fade, n)
                if f > 0:
                    fade_in = np.linspace(0.0, 1.0, f, dtype=np.float32)
                    data[:f] *= fade_in[:, None]
                self.fadein_frames = 0
                self.in_gap = False
            return data
        # Underrun pequeño: rellenar el hueco REPITIENDO (con un crossfade) el
        # material disponible en vez de estirarlo. Estirarlo cambiaba el tono
        # (hasta ~10 % -> "el audio se cambia muchísimo"); repetir conserva el
        # tono y evita el silencio. No es un hueco: no se cuentan
        # gap_blocks/gap_frames ni se marca in_gap.
        if avail > 0 and avail / n >= UNDERFLOW_GRACE_RATIO:
            idx = self.read_pos % nframes
            if idx + avail <= nframes:
                data = ring[idx : idx + avail].copy()
            else:
                a = nframes - idx
                data = np.concatenate([ring[idx:], ring[: avail - a]])
            self.read_pos += avail
            self._stats["grace_stretches"] += 1
            short = n - avail
            out = np.empty((n, 2), dtype=np.float32)
            out[:avail] = data
            reps = int(np.ceil(short / avail))
            fill = np.tile(data, (reps, 1))[:short]
            f = min(64, short, avail)
            if f > 0:
                w = np.linspace(0.0, 1.0, f, dtype=np.float32)[:, None]
                fill[:f] = data[avail - f : avail] * (1.0 - w) + fill[:f] * w
            out[avail:] = fill
            return out
        # hueco: silencio con fundido de salida (sin corte seco)
        m = avail
        out = np.zeros((n, 2), dtype=np.float32)
        if m > 0:
            idx = self.read_pos % nframes
            if idx + m <= nframes:
                real = ring[idx : idx + m].copy()
            else:
                a = nframes - idx
                real = np.concatenate([ring[idx:], ring[: m - a]])
            self.read_pos += m
            out[:m] = real
            f = min(self._fade, m)
            if f > 0:
                fade_out = np.linspace(1.0, 0.0, f, dtype=np.float32)
                out[m - f : m] *= fade_out[:, None]
        self.in_gap = True
        self.fadein_frames = max(self.fadein_frames, self._fade)
        self._stats["gap_blocks"] += 1  # métrica en vivo (lock tomado)
        self._stats["gap_frames"] += n - m  # frames de silencio insertados
        return out

    # ---------- callbacks ----------

    def _cap_callback(self, in_data, frame_count, time_info, status):
        """Callback de captura: solo COPIA crudo al ring de entrada.

        No corre DSP aquí (eso va en el hilo _dsp_loop): así el callback
        devuelve en microsegundos y PortAudio nunca descarta input por tardanza
        (que era la causa de los microcortes/estática con carga alta)."""
        if self._raw_ring is None or self._pa_mod is None:
            return (None, self._pa_mod.paContinue if self._pa_mod else 0)
        ch = self._capture_channels
        x = np.frombuffer(in_data, dtype=np.float32)[: frame_count * ch]
        try:
            x = x.reshape(frame_count, ch)
        except ValueError:
            return (None, self._pa_mod.paContinue)
        if ch > 2:
            # Loopback multicanal (5.1/7.1): downmix a estéreo ANTES del DSP.
            x = _downmix_to_stereo(x)
        self._put_raw(x)
        with self.lock:
            self._stats["captured_frames"] += frame_count
            if status & getattr(self._pa_mod, "paInputOverflow", 0x2):
                self._stats["input_overflows"] += 1
        return (None, self._pa_mod.paContinue)

    # ---------- ring de entrada crudo (captura -> hilo DSP) ----------

    def _put_raw(self, data: np.ndarray) -> None:
        raw = self._raw_ring
        if raw is None:
            return
        n = len(data)
        nframes = len(raw)
        with self.lock:
            avail = self._raw_write - self._raw_read
            if avail + n > nframes:
                # El hilo DSP no da abasto: descartar lo más viejo del crudo.
                drop = avail + n - nframes
                self._raw_read += drop
            idx = self._raw_write % nframes
            if idx + n <= nframes:
                raw[idx : idx + n] = data
            else:
                a = nframes - idx
                raw[idx:] = data[:a]
                raw[: n - a] = data[a:]
            self._raw_write += n

    def _read_raw(self) -> np.ndarray | None:
        """Devuelve el bloque crudo disponible (o None si no hay)."""
        raw = self._raw_ring
        if raw is None:
            return None
        with self.lock:
            avail = self._raw_write - self._raw_read
            if avail <= 0:
                return None
            n = min(avail, CHUNK)  # procesar en bloques de CHUNK
            nframes = len(raw)
            idx = self._raw_read % nframes
            if idx + n <= nframes:
                data = raw[idx : idx + n].copy()
            else:
                a = nframes - idx
                data = np.concatenate([raw[idx:], raw[: n - a]])
            self._raw_read += n
            return data

    def drain(self) -> int:
        """Procesa TODO el crudo pendiente de forma SÍNCRONA y devuelve los
        frames procesados. Lo usa el hilo de DSP un bloque a la vez y los tests
        (que no arrancan el hilo) para no depender de threading."""
        total = 0
        while True:
            block = self._read_raw()
            if block is None:
                return total
            self._put(self.enhancer.process(block))
            total += len(block)

    def _start_dsp_process(self) -> None:
        """Arranca el proceso hijo del DSP y las bombas de cola.

        El proceso hijo tiene su propio GIL: su trabajo numpy no compite con el
        callback de salida de PortAudio (que era la causa de los descartes)."""
        from .dsp_process import child_main, snapshot_params

        ctx = mp.get_context("spawn")
        self._raw_q = ctx.Queue(maxsize=256)
        self._out_q = ctx.Queue(maxsize=256)
        self._params_q = ctx.Queue(maxsize=8)
        self._mp_stop = ctx.Event()
        self._gr = ctx.Value("f", 0.0)  # GR del limitador (hijo -> padre)
        # El FFT del espectro y la medición de niveles se hacen en el HIJO (su
        # GIL): si se hacen en el padre compiten con el callback de salida y
        # dañan el audio justo cuando el espectro está visible.
        self._telemetry = ctx.Array("f", 7)
        self._spectrum_buf = ctx.Array("f", 64)
        self._params_q.put(snapshot_params(self.enhancer))
        self._dsp_proc = ctx.Process(
            target=child_main,
            args=(
                self._raw_q,
                self._out_q,
                self._params_q,
                self._mp_stop,
                self._gr,
                self._telemetry,
                self._spectrum_buf,
            ),
            name="dsp-proc",
            daemon=True,
        )
        self._dsp_proc.start()
        self._set_child_priority()
        self._dsp_running = True
        # UN hilo para ambas direcciones (menos hilos Python compitiendo por el
        # GIL con los callbacks de audio) + el de parámetros.
        for target in (self._pump, self._pump_params):
            threading.Thread(target=target, daemon=True).start()

    def _set_child_priority(self) -> None:
        """Sube la prioridad del proceso hijo por ENCIMA de lo normal.

        El hijo procesa en tiempo real: si se queda sin CPU (antes estaba en
        below-normal) el ring de salida se vacía y el callback inserta silencio
        -> cortes/bajadas audibles bajo carga. La salida física corre en el hilo
        de alta prioridad de PortAudio, así que subir el hijo no la perjudica.
        """
        if os.name != "nt" or self._dsp_proc is None:
            return
        try:
            import ctypes

            above_normal = 0x00008000
            process_set_information = 0x0200
            handle = ctypes.windll.kernel32.OpenProcess(process_set_information, False, self._dsp_proc.pid)
            if handle:
                ctypes.windll.kernel32.SetPriorityClass(handle, above_normal)
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            logger.debug("No se pudo ajustar la prioridad del proceso DSP", exc_info=True)

    def _stop_dsp_process(self) -> None:
        if self._dsp_proc is None:
            return
        if self._mp_stop is not None:
            self._mp_stop.set()
        with contextlib.suppress(Exception):
            self._raw_q.put_nowait(None)  # centinela de parada
        self._dsp_proc.join(timeout=2.0)
        if self._dsp_proc.is_alive():
            logger.warning("El proceso DSP no paró en 2 s; se termina a la fuerza")
            self._dsp_proc.terminate()
            self._dsp_proc.join(timeout=1.0)
        self._dsp_proc = None

    def _pump(self) -> None:
        """Un solo hilo mueve crudo->hijo y procesado->salida.

        Antes eran dos hilos + los hilos "feeder" de las multiprocessing.Queue:
        demasiados hilos Python compitiendo por el GIL con los callbacks de
        audio (retrasaban la salida -> descartes)."""
        while self._dsp_running:
            block = self._read_raw()
            if block is not None:
                with contextlib.suppress(Exception):
                    self._raw_q.put(block.tobytes(), timeout=1.0)
            got = False
            try:
                item = self._out_q.get_nowait()
            except Exception:
                item = None
            if item is not None:
                got = True
                data = np.frombuffer(item, dtype=np.float32).reshape(-1, 2)
                # Niveles y espectro los publica el HIJO por memoria compartida:
                # el padre solo los copia (sin numpy/FFT que compitan).
                self._sync_telemetry()
                self._put(data)
            if block is None and not got:
                time.sleep(0.001)

    def _sync_telemetry(self) -> None:
        t = self._telemetry
        if t is None:
            return
        with t.get_lock():
            e = self.enhancer
            e.level_rms = t[0]
            e.level_peak = t[1]
            e.level_rms_l = t[2]
            e.level_rms_r = t[3]
            e.level_peak_l = t[4]
            e.level_peak_r = t[5]
            e.level_gr = t[6]

    def has_dsp_process(self) -> bool:
        """True si el DSP corre en un proceso hijo (el padre no hace FFT)."""
        return self._dsp_proc is not None

    def read_spectrum(self) -> list[float] | None:
        """Último espectro publicado por el hijo (o None si no hay datos)."""
        buf = self._spectrum_buf
        if buf is None:
            return None
        with buf.get_lock():
            values = [float(buf[i]) for i in range(len(buf))]
        return values if any(v != 0.0 for v in values) else None

    def _pump_params(self) -> None:
        """Replica los parámetros del enhancer al proceso hijo cuando cambian."""
        from .dsp_process import snapshot_params

        last = snapshot_params(self.enhancer)
        while self._dsp_running:
            time.sleep(0.05)
            snap = snapshot_params(self.enhancer)
            if snap != last:
                last = snap
                with contextlib.suppress(Exception):
                    self._params_q.put_nowait(snap)

    def _dsp_loop(self) -> None:
        """Hilo dedicado: lee crudo, procesa y alimenta el ring de salida."""
        logger.info("Hilo de DSP iniciado")
        while self._dsp_running:
            block = self._read_raw()
            if block is None:
                self._dsp_wakeup.wait(0.001)  # espera sin quemar CPU; stop() despierta
                continue
            try:
                y = self.enhancer.process(block)
                self._put(y)
            except Exception:
                logger.exception("Error en el hilo de DSP")
        logger.info("Hilo de DSP detenido")

    def _out_callback(self, in_data, frame_count, time_info, status):
        if self.ring is None or self._pa_mod is None:
            return (None, self._pa_mod.paContinue if self._pa_mod else 0)
        # Silencio de arranque: la salida física aún no corre a su reloj real, así
        # que se emite silencio y se recorta el ring a la consigna (conservando lo
        # más nuevo) para no desbordar/descartar. No cuenta como hueco.
        if self._warmup_until and time.monotonic() < self._warmup_until:
            with self.lock:
                excess = self.write_pos - self.read_pos - self._drift_target
                if excess > 0:
                    self.read_pos += excess
            return (np.zeros((frame_count, 2), dtype=np.float32).tobytes(), self._pa_mod.paContinue)
        with self.lock:
            fill = self.write_pos - self.read_pos
            error = fill - self._drift_target
            if abs(error) <= self._drift_deadband:
                # El ruido normal del ring no debe provocar resampling.
                self._drift_accum *= 0.95
                n_adj = 0
            else:
                # Acumulador fraccionario: una diferencia de reloj de 100 ppm
                # se reparte como un frame ocasional, no como un salto fijo.
                self._drift_accum += error * self._drift_gain
                n_adj = int(np.trunc(self._drift_accum))
                n_adj = max(-self._max_drift_frames, min(self._max_drift_frames, n_adj))
                self._drift_accum -= n_adj
            if n_adj:
                self._stats["drift_adjust_frames"] += abs(n_adj)
            if status & getattr(self._pa_mod, "paOutputUnderflow", 0x4):
                self._stats["output_underruns"] += 1
            raw = self._read(max(1, frame_count + n_adj))
            tail = self._interp_tail
        # Remuestreo fraccional con memoria de frontera (fuera del lock: solo
        # toca arrays locales). La memoria es de la ENTRADA del remuestreador
        # (el flujo del ring), que es el stream continuo a interpolar.
        data = self._match_frame_count(raw, frame_count, tail=tail)
        if raw.shape[0] >= 2:
            self._interp_tail = raw[-2:].copy()
        return (data.tobytes(), self._pa_mod.paContinue)

    @staticmethod
    def _cubic_hermite(x: np.ndarray, t: np.ndarray) -> np.ndarray:
        """Interpolación cúbica de Hermite (Catmull-Rom) sobre posiciones
        fraccionarias ``t`` (índices en unidades de muestra de ``x``).

        C1-continua: a diferencia del lineal no presenta quiebres de pendiente
        entre muestras, que se oyen como armónicos de distorsión en el ajuste
        de deriva (el resampler fraccional "continuo" de la Fase 3). Los
        bordes se tratan replicando la muestra extrema (pendiente nula).

        x: (n, ch); devuelve (len(t), ch) en float32.
        """
        n = x.shape[0]
        i0 = np.clip(np.floor(t).astype(np.int64), 0, n - 1)
        frac = (t - i0).astype(np.float64)
        i_m1 = np.clip(i0 - 1, 0, n - 1)
        i1 = np.clip(i0 + 1, 0, n - 1)
        i2 = np.clip(i0 + 2, 0, n - 1)
        a = frac[:, None]
        b = (frac * frac)[:, None]
        c = (frac * frac * frac)[:, None]
        # Vectorizado sobre canales (antes un bucle Python por canal: más tiempo
        # con el GIL dentro del callback de salida).
        p0 = x[i_m1]
        p1 = x[i0]
        p2 = x[i1]
        p3 = x[i2]
        out = 0.5 * (
            2.0 * p1 + (-p0 + p2) * a + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * b + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * c
        )
        return out.astype(np.float32, copy=False)

    @staticmethod
    def _match_frame_count(data, frame_count, tail=None):
        """Ajusta suavemente la cantidad leída al bloque solicitado.

        Leer unos frames de más o de menos corrige los relojes de captura y
        salida, pero interpolar el bloque evita el salto que producía quitar el
        primer frame o duplicar el último. La interpolación es cúbica de
        Hermite (C1-continua); ``tail`` (2 últimas muestras del bloque
        anterior) extiende el contexto hacia atrás para que la interpolación
        sea continua TAMBIÉN a través de la frontera entre callbacks: sin esa
        memoria, el remuestreador introduce una discontinuidad periódica
        (cada bloque de salida) aunque la interpolación interna sea suave.
        """
        count = data.shape[0]
        if count == frame_count:
            return data
        if count <= 1 or frame_count <= 1:
            return np.resize(data, (frame_count, 2)).astype(np.float32, copy=False)
        positions = np.linspace(0.0, count - 1.0, frame_count, dtype=np.float64)
        if tail is not None and len(tail):
            ext = np.concatenate([np.asarray(tail, dtype=data.dtype), data], axis=0)
            out = AudioEngine._cubic_hermite(ext, positions + len(tail))
        else:
            out = AudioEngine._cubic_hermite(data, positions)
        return out.astype(np.float32, copy=False)
