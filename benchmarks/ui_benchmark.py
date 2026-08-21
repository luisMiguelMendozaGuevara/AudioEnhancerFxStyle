"""Benchmark comparable de la UI Tk actual y la UI Qt experimental.

Se ejecuta sin iniciar audio real: mide construcción, pintura, scroll sintético y
representación de un snapshot de spectrum. Las pruebas con audio activo siguen
siendo manuales porque dependen del dispositivo WASAPI elegido.
"""

from __future__ import annotations

import json
import os
import statistics
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def elapsed(metrics, start: str, end: str):
    try:
        return round(metrics.elapsed_ms(start, end), 2)
    except KeyError:
        return None


class ProcessSampler:
    """Muestra CPU y RSS sin bloquear el event loop de la UI."""

    def __init__(self) -> None:
        try:
            import psutil
        except ModuleNotFoundError:
            self.process = None
        else:
            self.process = psutil.Process()
        self.phase = "idle"
        self.samples: dict[str, list[float]] = {"idle": [], "ui_scroll": []}
        self.rss_mb = 0.0
        self._stop = threading.Event()
        self._thread = None
        if self.process is not None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        self.process.cpu_percent(None)
        while not self._stop.wait(0.1):
            self.samples.setdefault(self.phase, []).append(self.process.cpu_percent(None))
            self.rss_mb = max(self.rss_mb, self.process.memory_info().rss / (1024 * 1024))

    def set_phase(self, phase: str) -> None:
        self.phase = phase

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def mean(self, phase: str):
        if self.process is None:
            return None
        values = self.samples.get(phase, [])
        return round(statistics.mean(values), 2) if values else None


def benchmark_tk():
    import customtkinter as ctk

    from audio_enhancer.app import App
    from audio_enhancer.startup_metrics import StartupMetrics

    root = ctk.CTk()
    metrics = StartupMetrics()
    metrics.mark("root_created")
    app = App(root, startup_metrics=metrics)
    state = {"frames": 0, "scroll": []}
    sampler = ProcessSampler()

    def begin_visual_probe():
        sampler.set_phase("ui_scroll")
        app.enhancer.spectrum = [-30.0] * 64
        app.running = True
        original_draw = app._draw_spectrum

        def counted_draw():
            state["frames"] += 1
            original_draw()

        app._draw_spectrum = counted_draw
        for _ in range(100):
            start = time.perf_counter()
            app.body.canvas.yview_scroll(1, "units")
            state["scroll"].append((time.perf_counter() - start) * 1000.0)

    root.after(1000, begin_visual_probe)
    root.after(3000, root.destroy)
    started = time.perf_counter()
    root.mainloop()
    sampler.stop()
    duration = max(0.001, time.perf_counter() - started - 1.0)
    return {
        "toolkit": "CustomTkinter",
        "root_to_shell_ms": elapsed(metrics, "root_created", "shell"),
        "root_to_first_paint_ms": elapsed(metrics, "root_created", "first_paint"),
        "root_to_ui_ready_ms": elapsed(metrics, "root_created", "ui_ready"),
        "root_to_devices_ready_ms": elapsed(metrics, "root_created", "devices_ready"),
        "spectrum_fps": round(state["frames"] / duration, 2),
        "scroll_max_latency_ms": round(max(state["scroll"], default=0.0), 4),
        "cpu_idle": sampler.mean("idle"),
        "cpu_audio": None,
        "cpu_audio_scroll": sampler.mean("ui_scroll"),
        "memory_mb": round(sampler.rss_mb, 2) if sampler.process is not None else None,
        "notes": "audio no iniciado; scroll sintético y snapshot fijo",
    }


def benchmark_qt():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from audio_enhancer.startup_metrics import StartupMetrics
    from audio_enhancer.ui.qt_main_window import QtMainWindow

    qt_app = QApplication.instance() or QApplication([])
    metrics = StartupMetrics()
    window = QtMainWindow(metrics)
    state = {"scroll": [], "started": 0.0}
    sampler = ProcessSampler()

    def begin_visual_probe():
        sampler.set_phase("ui_scroll")
        window.spectrum_widget.set_spectrum([-30.0] * 64)
        render_timer = QTimer(window)
        render_timer.setInterval(33)
        render_timer.timeout.connect(lambda: window.spectrum_widget.set_spectrum([-30.0] * 64))
        render_timer.start()
        bar = window.scroll.verticalScrollBar()
        for _ in range(100):
            start = time.perf_counter()
            bar.setValue(bar.value() + 1)
            state["scroll"].append((time.perf_counter() - start) * 1000.0)
        state["started"] = time.perf_counter()

    window.show()
    QTimer.singleShot(0, window.build_content)
    QTimer.singleShot(1000, begin_visual_probe)
    QTimer.singleShot(3000, qt_app.quit)
    qt_app.exec()
    sampler.stop()
    duration = max(0.001, time.perf_counter() - state["started"])
    frames = window.spectrum_widget.painted_frames
    window.close()
    return {
        "toolkit": "PySide6",
        "root_to_shell_ms": elapsed(metrics, "root_created", "shell"),
        "root_to_first_paint_ms": elapsed(metrics, "root_created", "first_paint"),
        "root_to_ui_ready_ms": elapsed(metrics, "root_created", "ui_ready"),
        "root_to_devices_ready_ms": elapsed(metrics, "root_created", "devices_ready"),
        "spectrum_fps": round(frames / duration, 2),
        "scroll_max_latency_ms": round(max(state["scroll"], default=0.0), 4),
        "cpu_idle": sampler.mean("idle"),
        "cpu_audio": None,
        "cpu_audio_scroll": sampler.mean("ui_scroll"),
        "memory_mb": round(sampler.rss_mb, 2) if sampler.process is not None else None,
        "notes": "audio no iniciado; scroll sintético y snapshot fijo",
    }


if __name__ == "__main__":
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "qt"
    print(json.dumps(benchmark_qt() if mode == "qt" else benchmark_tk(), ensure_ascii=False))
