"""Motor de audio: ring buffer + callbacks de PortAudio con compensación de deriva.

Separa la captura (loopback WASAPI, paquetes irregulares) de la salida física
para evitar los OutputUnderflow del modo full-duplex. Los callbacks se ejecutan
en hilos de audio: solo tocan estado interno bajo ``self.lock`` y nunca llaman a
Tk. ``start_capture``/``open_output``/``stop`` permiten un arranque no bloqueante
orquestado desde el hilo de la UI.
"""

import logging
import threading
from functools import lru_cache

import numpy as np

from .constants import CHUNK, DRIFT_TARGET_MS, RING_SECONDS

logger = logging.getLogger("audio_enhancer.engine")

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
        self.pa = None
        self._pa_mod = None  # módulo pyaudiowpatch (para paFloat32/paContinue)
        self.stream = None  # captura (input)
        self.out_stream = None  # salida (output)
        # Estado del ring
        self.ring: np.ndarray | None = None
        self.rhead: int = 0  # posición de escritura (frames)
        self.whead: int = 0  # posición de lectura (frames)
        self.fadein_frames: int = 0
        self.in_gap: bool = False
        self.nframes: int = 0
        self._fade: int = 1
        # Control de deriva alrededor del punto medio del ring
        # (los valores se ajustan en configure_ring; aqui quedan los por
        # defecto para construccion directa en tests)
        self._drift_target: int = 0
        self._drift_deadband: int = 0
        self._drift_gain: float = 0.02
        self._drift_accum: float = 0.0
        self._max_drift_frames: int = 8
        # Canales negociados en la captura (el callback mezcla a estério si
        # el loopback entrega más de 2).
        self._capture_channels: int = 2
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
        self.ring = np.zeros((nframes, 2), dtype=np.float32)
        self.rhead = 0
        self.whead = 0
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

    def _open_capture_stream(self, pa, in_idx: int, rate: int, channels: int):
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
        except Exception:
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

    def fill(self) -> int:
        """Frames disponibles en el ring (para pre-cargar la salida)."""
        with self.lock:
            return self.rhead - self.whead

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

    def stop(self) -> None:
        """Detiene y cierra ambos streams (idempotente). Cierra también la
        captura si la salida nunca llegó a abrirse (evita fuga de stream)."""
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
        with self.lock:
            avail = self.rhead - self.whead
            if avail + n > nframes:
                # descartar lo más viejo si la salida va más lenta
                drop = avail + n - nframes
                self.whead += drop
                self._stats["dropped_frames"] += drop  # métrica en vivo
                idx = self.whead % nframes
                if idx + drop <= nframes:
                    self.ring[idx : idx + drop] = 0.0
                else:
                    a = nframes - idx
                    self.ring[idx:] = 0.0
                    self.ring[: drop - a] = 0.0
            idx = self.rhead % nframes
            if idx + n <= nframes:
                self.ring[idx : idx + n] = data
            else:
                a = nframes - idx
                self.ring[idx:] = data[:a]
                self.ring[: n - a] = data[a:]
            self.rhead += n

    def _read(self, n):
        """Devuelve n frames para la salida. Llamar con self.lock tomado.

        Si el ring no tiene suficiente audio (la salida corrió más rápido que
        la captura) NO inserta un bloque seco de silencio: suaviza el hueco con
        fundidos de entrada/salida para que no haya chasquidos.
        """
        nframes = self.nframes
        avail = self.rhead - self.whead
        if avail >= n:
            idx = self.whead % nframes
            if idx + n <= nframes:
                data = self.ring[idx : idx + n].copy()
            else:
                a = nframes - idx
                data = np.concatenate([self.ring[idx:], self.ring[: n - a]])
            self.whead += n
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
        # hueco: silencio con fundido de salida (sin corte seco)
        m = avail
        out = np.zeros((n, 2), dtype=np.float32)
        if m > 0:
            idx = self.whead % nframes
            if idx + m <= nframes:
                real = self.ring[idx : idx + m].copy()
            else:
                a = nframes - idx
                real = np.concatenate([self.ring[idx:], self.ring[: m - a]])
            self.whead += m
            out[:m] = real
            f = min(self._fade, m)
            if f > 0:
                fade_out = np.linspace(1.0, 0.0, f, dtype=np.float32)
                out[m - f : m] *= fade_out[:, None]
        self.in_gap = True
        self.fadein_frames = max(self.fadein_frames, self._fade)
        self._stats["gap_blocks"] += 1  # métrica en vivo (lock tomado)
        return out

    # ---------- callbacks ----------

    def _cap_callback(self, in_data, frame_count, time_info, status):
        if self.ring is None or self._pa_mod is None:
            return (None, self._pa_mod.paContinue if self._pa_mod else 0)
        ch = self._capture_channels
        x = np.frombuffer(in_data, dtype=np.float32)
        x = np.asarray(x[: frame_count * ch], dtype=np.float32)
        try:
            x = x.reshape(frame_count, ch)
        except ValueError:
            return (None, self._pa_mod.paContinue)
        if ch > 2:
            # Loopback multicanal (5.1/7.1): downmix a estéreo ANTES del DSP.
            x = _downmix_to_stereo(x)
        y = self.enhancer.process(x)
        self._put(y)
        with self.lock:
            self._stats["captured_frames"] += frame_count
            if status & getattr(self._pa_mod, "paInputOverflow", 0x2):
                self._stats["input_overflows"] += 1
        return (None, self._pa_mod.paContinue)

    def _out_callback(self, in_data, frame_count, time_info, status):
        if self.ring is None or self._pa_mod is None:
            return (None, self._pa_mod.paContinue if self._pa_mod else 0)
        with self.lock:
            fill = self.rhead - self.whead
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
        a = frac
        b = frac * frac
        c = b * frac
        out = np.empty((len(t), x.shape[1]), dtype=np.float32)
        for ch in range(x.shape[1]):
            p0, p1, p2, p3 = x[i_m1, ch], x[i0, ch], x[i1, ch], x[i2, ch]
            out[:, ch] = 0.5 * (
                2.0 * p1
                + (-p0 + p2) * a
                + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * b
                + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * c
            )
        return out

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
