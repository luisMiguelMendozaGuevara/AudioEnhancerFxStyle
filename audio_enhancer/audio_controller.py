"""Controlador de audio: ciclo de vida del motor, ruteo y prefill.

(R3-C1) Extraído de NewMainWindow para separar responsabilidades: la
ventana decide QUÉ hacer con la interfaz; el controlador sabe CÓMO hacer
vivir el audio. No importa Qt-Widgets ni toca un solo widget: se comunica
por señales (started/output_ready/start_failed/stopped) y acepta
excepciones de PortAudio en start() para que la capa visual las presente.

Responsabilidades:
    - negociación de tasa con la FUENTE (loopback) y reset de estado DSP
    - apertura/cierre de captura y salida (instancia PyAudio única)
    - prefill del ring hasta la consigna de deriva (QTimer interno)
    - evaluación de ruteo (pura, testeable sin Qt: evaluate_route)
    - recuperación de errores: si la salida falla, cierra la captura

Lo que NO sabe: cómo se dibuja un botón.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal

from .constants import CHUNK, VIRTUAL_CABLE_KEYWORDS
from .engine import AudioEngine, _pa

logger = logging.getLogger("audio_enhancer.audio_controller")


def _norm(name: str) -> str:
    """Normaliza nombres de dispositivo para comparar (sin espacios/mayúsculas)."""
    return "".join(name.lower().split())


class AudioController(QObject):
    """Dueño del AudioEngine y de su ciclo de vida (ver docstring del módulo)."""

    # La captura arrancó: (nombre_fuente, nombre_salida, tasa)
    started = Signal(str, str, int)
    # Salida abierta tras el prefill: latencia real percibida (ms)
    output_ready = Signal(float)
    # Fallo en cualquier fase (captura o salida): mensaje de la excepción
    start_failed = Signal(str)
    # El usuario (o un fallo) detuvo el audio
    stopped = Signal()
    # Un stream dejó de estar activo (dispositivo desconectado, error de
    # driver): la ventana avisa y refresca la lista de dispositivos.
    stream_lost = Signal(str)

    def __init__(self, enhancer, parent=None) -> None:
        super().__init__(parent)
        self.enhancer = enhancer
        self.engine = AudioEngine(enhancer)
        self.pa = None  # instancia PyAudio única de la app
        self.running = False
        self.go = False  # ruteo validado por evaluate_route
        self._open_output_args: tuple[int, int] | None = None
        self._prefill_deadline = 0.0
        self._prefill_timer = QTimer(self)
        self._prefill_timer.setInterval(10)
        self._prefill_timer.timeout.connect(self._poll_prefill)
        # Watchdog de streams: cada 2 s comprueba que captura y salida siguen
        # activos. Si un USB/Bluetooth se desconecta o el driver falla, el
        # callback deja de llegar y antes la app quedaba en silencio sin avisar.
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(2000)
        self._watchdog.timeout.connect(self._on_watchdog)
        # Interruptor del watchdog (página Config): apagado, un dispositivo
        # desconectado ya no detiene el audio automáticamente.
        self.watchdog_enabled = True

    # ---------- ruteo (puro: sin estado, sin Qt, testeable en seco) ----------

    @staticmethod
    def evaluate_route(src_name: str, out_name: str) -> tuple[bool, str]:
        """Valida el par fuente->salida. Devuelve (permitido, clave).

        Claves de mensaje (la UI las traduce y colorea):
            ROUTE_MISSING       falta fuente o salida
            ROUTE_ECO           capturar y reproducir el mismo dispositivo
            ROUTE_VIRTUAL_OUT   la salida es un cable virtual
            ROUTE_OK_VIRTUAL    cable virtual -> salida física (caso ideal)
            ROUTE_OK_FISICA     captura de parlante físico (funciona, con matiz)
        """
        if not src_name or not out_name:
            return False, "ROUTE_MISSING"
        if _norm(src_name) == _norm(out_name):
            return False, "ROUTE_ECO"
        out_is_virtual = any(k in out_name.lower() for k in VIRTUAL_CABLE_KEYWORDS)
        if out_is_virtual:
            return False, "ROUTE_VIRTUAL_OUT"
        src_is_virtual = any(k in src_name.lower() for k in VIRTUAL_CABLE_KEYWORDS)
        return (True, "ROUTE_OK_VIRTUAL") if src_is_virtual else (True, "ROUTE_OK_FISICA")

    # ---------- ciclo de vida ----------

    def ensure_pa(self):
        """Instancia PyAudio única para toda la vida de la app."""
        if self.pa is None:
            self.pa = _pa().PyAudio()
        return self.pa

    def terminate_pa(self) -> None:
        """Cierra la instancia PyAudio (apagado de la app)."""
        if self.pa is not None:
            try:
                self.pa.terminate()
            except Exception:
                logger.debug("PyAudio.terminate falló en el apagado", exc_info=True)
            self.pa = None

    # ---------- negociación de tasa ----------

    @staticmethod
    def negotiate_rate(source: dict, output: dict) -> int:
        """Tasa común de captura y salida, robusta ante datos raros.

        Regla (código H5): manda la FUENTE (loopback); si su tasa falta o es
        absurda, se usa la de la SALIDA; si tampoco, 48000. Captura y salida
        SIEMPRE abren a la MISMA tasa: el control de deriva solo corrige ±8
        frames/bloque, no una diferencia de tasa completa (p. ej. 44.1k vs 48k).

        Un dispositivo WASAPI en modo compartido remuestrea a su tasa nativa,
        así que abrir a la tasa de la fuente es válido aunque el hardware use
        otra."""

        def _valid(raw) -> int | None:
            try:
                r = int(raw)
            except (TypeError, ValueError):
                return None
            return r if 8000 <= r <= 384000 else None

        src = _valid(source.get("defaultSampleRate")) if source else None
        out = _valid(output.get("defaultSampleRate")) if output else None
        return src or out or 48000

    def start(self, source: dict, output: dict, drift_target_ms: int) -> None:
        """Arranca captura (loopback) y deja armado el prefill de la salida.

        Lanza la excepción de PortAudio si la captura no se puede abrir: la
        capa visual la presenta. La tasa la fija la FUENTE (código H5: abrir
        la captura a la tasa del output desincronizaba relojes)."""
        logger.info("Auto/manual start: %s -> %s", source["name"], output["name"])
        # Idempotencia: si ya hay streams abiertos (doble start por auto-arranque
        # + clic), cerrarlos antes de reabrir. Dos capturas activas duplican los
        # callbacks (capturados) y desbordan el ring -> cortes de audio.
        if self.running or self.engine.stream is not None or self.engine.out_stream is not None:
            logger.warning("start() con audio ya activo: se reinicia la cadena")
            self.engine.stop()
            self._prefill_timer.stop()
            self._watchdog.stop()
            self._open_output_args = None
            self.running = False
        self.ensure_pa()
        rate = self.negotiate_rate(source, output)
        if rate != self.enhancer.sample_rate:
            # Cambio de tasa: los estados zi de biquads y compresor (y las
            # rampas) son historial de OTRA tasa; continuar con ellos inyecta
            # artefactos al arrancar (M3). reset_state también limpia caches
            # del analizador.
            self.enhancer.sample_rate = rate
            self.enhancer.reset_state()
        self.engine.configure_ring(rate, drift_target_ms=drift_target_ms)
        self.engine.start_capture(self.pa, source["index"], rate, device_info=source)
        self.running = True
        self._prefill_deadline = time.time() + 0.5
        self._open_output_args = (output["index"], rate)
        self.started.emit(source["name"], output["name"], rate)
        self._prefill_timer.start()
        if self.watchdog_enabled:
            self._watchdog.start()

    def _poll_prefill(self) -> None:
        if not self.running:
            self._prefill_timer.stop()
            return
        # Pre-cargar hasta la CONSIGNA de deriva (latencia objetivo), no a la
        # mitad del ring: la salida arranca ya en el punto de equilibrio.
        if self.engine.fill() >= self.engine.drift_target or time.time() > self._prefill_deadline:
            self._prefill_timer.stop()
            self._open_output()

    def _open_output(self) -> None:
        if self._open_output_args is None:
            return
        out_index, rate = self._open_output_args
        self._open_output_args = None
        try:
            self.engine.open_output(out_index, rate)
            # Latencia REAL percibida: consigna del ring + un bloque de salida.
            # Reportar nframes/rate (código viejo) mostraba 200 ms cuando el
            # punto de operación real está en drift_target (~60 ms).
            latency = ((self.engine.drift_target + CHUNK) / rate) * 1000.0
            logger.info(
                "Audio activo: ring a %d Hz, consigna %d frames, latencia %.1f ms",
                rate,
                self.engine.drift_target,
                latency,
            )
            self.output_ready.emit(latency)
        except Exception as exc:
            # Recuperación: sin salida no hay cadena; liberar la captura.
            self.engine.stop()
            self.running = False
            self.start_failed.emit(str(exc))

    def set_watchdog_enabled(self, enabled: bool) -> None:
        """Activa/desactiva el watchdog en caliente (sin reiniciar el audio)."""
        self.watchdog_enabled = bool(enabled)
        if not self.running:
            return
        if self.watchdog_enabled:
            self._watchdog.start()
        else:
            self._watchdog.stop()

    def set_latency(self, drift_target_ms: int) -> float:
        """Cambia la latencia objetivo en caliente y devuelve la nueva (ms).

        Solo mueve la consigna de llenado del ring; el control de deriva
        converge gradualmente sin glitch."""
        self.engine.set_drift_target_ms(drift_target_ms)
        rate = self.engine.rate or self.enhancer.sample_rate
        return ((self.engine.drift_target + CHUNK) / rate) * 1000.0

    def stop(self) -> None:
        """Detiene y cierra ambos streams (idempotente)."""
        logger.info("Audio detenido por el usuario")
        self._prefill_timer.stop()
        self._watchdog.stop()
        self._open_output_args = None
        self.engine.stop()
        was_running = self.running
        self.running = False
        if was_running:
            self.stopped.emit()

    # ---------- watchdog de streams (dispositivo desconectado / driver) ----------

    def check_streams(self) -> str | None:
        """Motivo si un stream dejó de estar activo, o None si todo sigue vivo.

        Puro respecto a Qt (solo lee ``is_active()`` de los streams): se puede
        testear con un engine falso. Devuelve "captura"/"salida"."""
        if not self.running:
            return None
        for label, stream in (("captura", self.engine.stream), ("salida", self.engine.out_stream)):
            if stream is None:
                continue
            try:
                active = bool(stream.is_active())
            except Exception:
                active = False  # stream muerto tras desconectar el dispositivo
            if not active:
                return label
        return None

    def _on_watchdog(self) -> None:
        if not self.watchdog_enabled:
            return
        reason = self.check_streams()
        if reason is None:
            return
        logger.warning("Stream de %s detenido inesperadamente; se detiene el audio", reason)
        self.stop()
        self.stream_lost.emit(reason)
