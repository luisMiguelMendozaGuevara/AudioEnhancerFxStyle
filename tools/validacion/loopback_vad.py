"""Validacion F0: loopback de un VAD render-only con nuestro stack.

Reproduce un tono al endpoint render WASAPI de "FxSound Speakers" (VAD de
FxSound, render-only: su INF no expone captura) SIN la app FxSound corriendo,
y captura su [Loopback] con pyaudiowpatch, midiendo continuidad y contenido.

Si esto funciona, confirma el mecanismo del diseno v1 del cable propio:
un endpoint render + loopback del motor de audio (audiodg) basta para que la
app capture, sin implementar puente render->capture en el driver.

Uso: python tools/validacion/loopback_vad.py
"""

import sys
import threading
import time

import numpy as np
import pyaudiowpatch as pa
import sounddevice as sd

FS_ESPERADA = 48000
CH = 1024
DUR_S = 20.0
FREQ = 440.0
UMBRAL_SILENCIO = 1e-4


def main() -> int:
    # 1) localizar el render WASAPI del VAD en sounddevice
    sd_render = None
    for i, d in enumerate(sd.query_devices()):
        if (
            "FxSound Speakers" in d["name"]
            and "WASAPI" in sd.query_hostapis(d["hostapi"])["name"]
            and d["max_output_channels"] >= 2
        ):
            sd_render = i
            break
    if sd_render is None:
        print("NO se encontro el render WASAPI de FxSound Speakers")
        return 1

    # 2) localizar su loopback en pyaudiowpatch
    p = pa.PyAudio()
    loop = None
    for d in p.get_loopback_device_info_generator():
        if "FxSound Speakers" in d["name"]:
            loop = d
            break
    if loop is None:
        print("NO se encontro el loopback de FxSound Speakers")
        p.terminate()
        return 1

    fs = int(loop["defaultSampleRate"])
    print(f"render(sd)={sd_render}  loopback(pa)={loop['index']} '{loop['name']}'  rate={fs}")

    # 3) tono continuo al VAD (callback; sin tocar la salida fisica)
    phase = [0.0]

    def out_cb(outdata, frames, _time_info, _status):
        t = (np.arange(frames, dtype=np.float32) + phase[0]) / fs
        outdata[:, 0] = 0.5 * np.sin(2 * np.pi * FREQ * t)
        outdata[:, 1] = outdata[:, 0]
        phase[0] = (phase[0] + frames) % fs

    out = sd.OutputStream(samplerate=fs, channels=2, device=sd_render, dtype="float32", callback=out_cb)
    out.start()

    # 4) capturar el loopback durante DUR_S
    stream = p.open(
        format=pa.paFloat32,
        channels=2,
        rate=fs,
        input=True,
        input_device_index=loop["index"],
        frames_per_buffer=CH,
    )
    stream.start_stream()

    n_chunks = int(DUR_S * fs / CH)
    silenciosos = 0
    rms_all = []
    last = None
    t0 = time.monotonic()
    for _ in range(n_chunks):
        data = np.frombuffer(stream.read(CH, exception_on_overflow=False), dtype=np.float32).reshape(-1, 2)
        last = data
        r = float(np.sqrt(np.mean(data.astype(np.float64) ** 2)))
        rms_all.append(r)
        if r < UMBRAL_SILENCIO:
            silenciosos += 1
    elapsed = time.monotonic() - t0

    stream.stop_stream()
    stream.close()
    p.terminate()
    out.stop()
    out.close()

    rms = np.array(rms_all)
    print(f"duracion={elapsed:.1f}s  chunks={n_chunks}  silenciosos={silenciosos} ({100 * silenciosos / n_chunks:.1f}%)")
    print(f"RMS medio={rms.mean():.4f}  min={rms.min():.4f}  max={rms.max():.4f}")

    # 5) contenido: pico espectral del ultimo chunk
    if last is not None:
        spec = np.abs(np.fft.rfft(last[:, 0] * np.hanning(len(last))))
        freqs = np.fft.rfftfreq(len(last), 1 / fs)
        pico = freqs[int(np.argmax(spec))]
        print(f"pico FFT = {pico:.1f} Hz (esperado {FREQ})")

    ok = silenciosos == 0 and abs(rms.mean() - 0.5 / np.sqrt(2)) < 0.05
    print("RESULTADO:", "OK" if ok else "REVISAR")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
