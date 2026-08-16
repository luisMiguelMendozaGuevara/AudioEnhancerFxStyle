# -*- coding: utf-8 -*-
"""Motor de audio: ring buffer + callbacks de PortAudio con compensación de deriva.

Separa la captura (loopback WASAPI, paquetes irregulares) de la salida física
para evitar los OutputUnderflow del modo full-duplex. Los callbacks se ejecutan
en hilos de audio: solo tocan estado interno bajo ``self.lock`` y nunca llaman a
Tk. ``start_capture``/``open_output``/``stop`` permiten un arranque no bloqueante
orquestado desde el hilo de la UI.
"""

import threading

import numpy as np

from .constants import CHUNK, RING_SECONDS

_pa_mod = None


def _pa():
    """Import perezoso de pyaudiowpatch (arranque más rápido)."""
    global _pa_mod
    if _pa_mod is None:
        import pyaudiowpatch
        _pa_mod = pyaudiowpatch
    return _pa_mod


class AudioEngine:
    """Ring buffer (N, 2) + streams PortAudio con callbacks de captura/salida."""

    def __init__(self, enhancer) -> None:
        self.enhancer = enhancer
        self.lock = threading.Lock()
        self.pa = None
        self._pa_mod = None       # módulo pyaudiowpatch (para paFloat32/paContinue)
        self.stream = None       # captura (input)
        self.out_stream = None   # salida (output)
        # Estado del ring
        self.ring: np.ndarray | None = None
        self.rhead: int = 0      # posición de escritura (frames)
        self.whead: int = 0      # posición de lectura (frames)
        self.fadein_frames: int = 0
        self.in_gap: bool = False
        self.nframes: int = 0
        self._fade: int = 1
        # Control de deriva alrededor del punto medio del ring
        self._drift_target: int = 0
        self._drift_deadband: int = 0
        self._drift_gain: float = 0.0001
        self._drift_accum: float = 0.0
        self._max_drift_frames: int = 4

    def configure_ring(self, rate: int) -> None:
        """Configura el ring para la tasa dada (tamaño, fundidos, deriva)."""
        nframes = int(rate * RING_SECONDS)
        self.nframes = nframes
        self.ring = np.zeros((nframes, 2), dtype=np.float32)
        self.rhead = 0
        self.whead = 0
        self.fadein_frames = 0
        self.in_gap = False
        self._fade = max(1, int(rate * 0.005))   # fundido ~5 ms
        self._drift_target = nframes // 2
        self._drift_deadband = max(CHUNK // 8, int(rate * 0.0025))
        self._drift_accum = 0.0

    def start_capture(self, pa, in_idx: int, rate: int) -> None:
        """Abre y arranca la captura (loopback WASAPI)."""
        self.pa = pa
        self._pa_mod = _pa()
        self.stream = pa.open(
            format=self._pa_mod.paFloat32,
            channels=2,
            rate=rate,
            frames_per_buffer=CHUNK,
            input=True,
            output=False,
            input_device_index=in_idx,
            stream_callback=self._cap_callback,
        )
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
                    pass
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
                idx = self.whead % nframes
                if idx + drop <= nframes:
                    self.ring[idx:idx + drop] = 0.0
                else:
                    a = nframes - idx
                    self.ring[idx:] = 0.0
                    self.ring[:drop - a] = 0.0
            idx = self.rhead % nframes
            if idx + n <= nframes:
                self.ring[idx:idx + n] = data
            else:
                a = nframes - idx
                self.ring[idx:] = data[:a]
                self.ring[:n - a] = data[a:]
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
                data = self.ring[idx:idx + n].copy()
            else:
                a = nframes - idx
                data = np.concatenate([self.ring[idx:], self.ring[:n - a]])
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
                real = self.ring[idx:idx + m].copy()
            else:
                a = nframes - idx
                real = np.concatenate([self.ring[idx:], self.ring[:m - a]])
            self.whead += m
            out[:m] = real
            f = min(self._fade, m)
            if f > 0:
                fade_out = np.linspace(1.0, 0.0, f, dtype=np.float32)
                out[m - f:m] *= fade_out[:, None]
        self.in_gap = True
        self.fadein_frames = max(self.fadein_frames, self._fade)
        return out

    # ---------- callbacks ----------

    def _cap_callback(self, in_data, frame_count, time_info, status):
        x = np.frombuffer(in_data, dtype=np.float32)
        x = np.asarray(x[: frame_count * 2], dtype=np.float32)
        try:
            x = x.reshape(frame_count, 2)
        except ValueError:
            return (None, self._pa_mod.paContinue)
        y = self.enhancer.process(x)
        self._put(y)
        return (None, self._pa_mod.paContinue)

    def _out_callback(self, in_data, frame_count, time_info, status):
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
                n_adj = max(-self._max_drift_frames,
                            min(self._max_drift_frames, n_adj))
                self._drift_accum -= n_adj
            data = self._read(max(1, frame_count + n_adj))
        data = self._match_frame_count(data, frame_count)
        return (data.tobytes(), self._pa_mod.paContinue)

    @staticmethod
    def _match_frame_count(data, frame_count):
        """Ajusta suavemente la cantidad leída al bloque solicitado.

        Leer unos frames de más o de menos corrige los relojes de captura y
        salida, pero interpolar el bloque evita el salto que producía quitar el
        primer frame o duplicar el último.
        """
        count = data.shape[0]
        if count == frame_count:
            return data
        if count <= 1 or frame_count <= 1:
            return np.resize(data, (frame_count, 2)).astype(np.float32, copy=False)
        positions = np.linspace(0.0, count - 1.0, frame_count, dtype=np.float32)
        base = np.arange(count, dtype=np.float32)
        return np.column_stack((
            np.interp(positions, base, data[:, 0]),
            np.interp(positions, base, data[:, 1]),
        )).astype(np.float32, copy=False)