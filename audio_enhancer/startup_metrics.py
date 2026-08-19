"""Métricas ligeras para medir las fases de arranque de la interfaz."""

from __future__ import annotations

import time
from collections.abc import Callable


class StartupMetrics:
    """Registra hitos con un reloj monotónico inyectable para pruebas."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.perf_counter
        self._marks: dict[str, float] = {}

    def mark(self, name: str) -> float:
        value = float(self._clock())
        self._marks[name] = value
        return value

    def elapsed_ms(self, start: str, end: str) -> float:
        return (self._marks[end] - self._marks[start]) * 1000.0

    def summary(self) -> dict[str, float]:
        return dict(self._marks)
