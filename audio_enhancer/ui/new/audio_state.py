from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ...constants import LATENCY_CHOICES_MS

# Umbrales de dedupe por métrica (R3-B1): los medidores se refrescan a ~30
# Hz y cada emit termina en widget.update() (repaint). Re-emitir el mismo
# valor — o ruido sub-píxel — paga CPU sin cambio visible. Cada métrica
# tiene su sensibilidad: el RMS de entrada es una señal ya suavizada
# (tau~85 ms) y basta un umbral fino; el pico de salida oscila más y usa
# uno algo mayor. Ambos quedan por debajo de la resolución de 1 px del
# widget (~0,002-0,008 de FS según ancho), así el ojo no nota el recorte.
_LEVEL_EPS_RMS = 0.001
_LEVEL_EPS_PEAK = 0.002


class AudioState(QObject):
    """Estado centralizado de la aplicacion.

    La UI lee de aqui; el audio engine escribe aqui a traves de
    actualizaciones periodicas. Ningun widget accede directamente
    al Enhancer o al AudioEngine.
    """

    # Senales para que los widgets se actualicen.
    processing_changed = Signal(bool)
    input_device_changed = Signal(str)
    output_device_changed = Signal(str)
    input_level_changed = Signal(float)
    output_level_changed = Signal(float)
    # Niveles por canal (L, R) para los medidores estéreo.
    input_levels_changed = Signal(float, float)
    output_levels_changed = Signal(float, float)
    # Reducción de ganancia del limitador (dB, <=0).
    output_gr_changed = Signal(float)
    latency_changed = Signal(float)
    sample_rate_changed = Signal(int)
    spectrum_changed = Signal(object)
    preset_changed = Signal(str)
    ab_changed = Signal(bool)
    volume_changed = Signal(float)
    bass_changed = Signal(float)
    treble_changed = Signal(float)
    eq_changed = Signal(object)
    limiter_changed = Signal(bool)
    compressor_changed = Signal(bool)
    true_peak_changed = Signal(bool)
    safety_ceiling_changed = Signal(bool)
    final_clip_changed = Signal(bool)
    latency_pref_changed = Signal(int)
    status_message_changed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Estado de procesamiento
        self._processing: bool = False
        self._input_device: str = ""
        self._output_device: str = ""
        # Niveles
        self._input_level: float = 0.0
        self._output_level: float = 0.0
        self._input_levels: tuple[float, float] = (0.0, 0.0)
        self._output_levels: tuple[float, float] = (0.0, 0.0)
        self._output_gr: float = 0.0
        # Tecnico
        self._latency_ms: float = 0.0
        self._sample_rate: int = 48000
        self._buffer_size: int = 1024
        # Espectro
        self._spectrum: list[float] | None = None
        # Preset / A/B
        self._preset_name: str = "Plano (sin efectos)"
        self._ab_enabled: bool = True
        # Controles DSP
        self._volume: float = 1.0
        self._bass: float = 0.0
        self._treble: float = 0.0
        self._eq_gains: list[float] = [0.0] * 9
        self._limiter: bool = True
        self._compressor: bool = True
        # True-peak: limitador con sobremuestreo x4 (caza picos inter-muestra).
        # CPU-intensivo: configurable para laptops flojas.
        self._true_peak: bool = True
        self._safety_ceiling: bool = True
        self._final_clip: bool = True
        # Preferencia de latencia (ms) elegida en la página Audio.
        self._latency_pref: int = LATENCY_CHOICES_MS[1]  # 60 ms

    # --- Properties ---

    @property
    def processing(self) -> bool:
        return self._processing

    @processing.setter
    def processing(self, value: bool) -> None:
        if self._processing != value:
            self._processing = value
            self.processing_changed.emit(value)

    @property
    def input_device(self) -> str:
        return self._input_device

    @input_device.setter
    def input_device(self, value: str) -> None:
        if self._input_device != value:
            self._input_device = value
            self.input_device_changed.emit(value)

    @property
    def output_device(self) -> str:
        return self._output_device

    @output_device.setter
    def output_device(self, value: str) -> None:
        if self._output_device != value:
            self._output_device = value
            self.output_device_changed.emit(value)

    @property
    def input_level(self) -> float:
        return self._input_level

    @input_level.setter
    def input_level(self, value: float) -> None:
        if abs(value - self._input_level) >= _LEVEL_EPS_RMS:
            self._input_level = value
            self.input_level_changed.emit(value)

    @property
    def output_level(self) -> float:
        return self._output_level

    @output_level.setter
    def output_level(self, value: float) -> None:
        if abs(value - self._output_level) >= _LEVEL_EPS_PEAK:
            self._output_level = value
            self.output_level_changed.emit(value)

    @property
    def input_levels(self) -> tuple[float, float]:
        """Nivel (L, R) de entrada para el medidor estéreo."""
        return self._input_levels

    @input_levels.setter
    def input_levels(self, value: tuple[float, float]) -> None:
        left, right = float(value[0]), float(value[1])
        if abs(left - self._input_levels[0]) >= _LEVEL_EPS_RMS or abs(right - self._input_levels[1]) >= _LEVEL_EPS_RMS:
            self._input_levels = (left, right)
            self.input_levels_changed.emit(left, right)

    @property
    def output_levels(self) -> tuple[float, float]:
        """Nivel (L, R) de salida para el medidor estéreo."""
        return self._output_levels

    @output_levels.setter
    def output_levels(self, value: tuple[float, float]) -> None:
        left, right = float(value[0]), float(value[1])
        changed = (
            abs(left - self._output_levels[0]) >= _LEVEL_EPS_PEAK
            or abs(right - self._output_levels[1]) >= _LEVEL_EPS_PEAK
        )
        if changed:
            self._output_levels = (left, right)
            self.output_levels_changed.emit(left, right)

    @property
    def output_gr(self) -> float:
        """Reducción de ganancia del limitador en dB (<=0)."""
        return self._output_gr

    @output_gr.setter
    def output_gr(self, value: float) -> None:
        # Umbral 0.5 dB: por debajo de eso el medidor no cambia de forma visible.
        if abs(value - self._output_gr) >= 0.5:
            self._output_gr = value
            self.output_gr_changed.emit(value)

    @property
    def latency_ms(self) -> float:
        return self._latency_ms

    @latency_ms.setter
    def latency_ms(self, value: float) -> None:
        self._latency_ms = value
        self.latency_changed.emit(value)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, value: int) -> None:
        if self._sample_rate != value:
            self._sample_rate = value
            self.sample_rate_changed.emit(value)

    @property
    def spectrum(self) -> list[float] | None:
        return self._spectrum

    @spectrum.setter
    def spectrum(self, value: list[float] | None) -> None:
        self._spectrum = value
        self.spectrum_changed.emit(value)

    @property
    def preset_name(self) -> str:
        return self._preset_name

    @preset_name.setter
    def preset_name(self, value: str) -> None:
        self._preset_name = value
        self.preset_changed.emit(value)

    @property
    def ab_enabled(self) -> bool:
        return self._ab_enabled

    @ab_enabled.setter
    def ab_enabled(self, value: bool) -> None:
        if self._ab_enabled != value:
            self._ab_enabled = value
            self.ab_changed.emit(value)

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float) -> None:
        self._volume = value
        self.volume_changed.emit(value)

    @property
    def bass(self) -> float:
        return self._bass

    @bass.setter
    def bass(self, value: float) -> None:
        self._bass = value
        self.bass_changed.emit(value)

    @property
    def treble(self) -> float:
        return self._treble

    @treble.setter
    def treble(self, value: float) -> None:
        self._treble = value
        self.treble_changed.emit(value)

    @property
    def eq_gains(self) -> list[float]:
        return self._eq_gains

    @eq_gains.setter
    def eq_gains(self, value: list[float]) -> None:
        self._eq_gains = list(value)
        self.eq_changed.emit(self._eq_gains)

    @property
    def limiter(self) -> bool:
        return self._limiter

    @limiter.setter
    def limiter(self, value: bool) -> None:
        if self._limiter != value:
            self._limiter = value
            self.limiter_changed.emit(value)

    @property
    def compressor(self) -> bool:
        return self._compressor

    @compressor.setter
    def compressor(self, value: bool) -> None:
        if self._compressor != value:
            self._compressor = value
            self.compressor_changed.emit(value)

    @property
    def true_peak(self) -> bool:
        return self._true_peak

    @true_peak.setter
    def true_peak(self, value: bool) -> None:
        if self._true_peak != value:
            self._true_peak = value
            self.true_peak_changed.emit(value)

    @property
    def safety_ceiling(self) -> bool:
        return self._safety_ceiling

    @safety_ceiling.setter
    def safety_ceiling(self, value: bool) -> None:
        if self._safety_ceiling != value:
            self._safety_ceiling = value
            self.safety_ceiling_changed.emit(value)

    @property
    def final_clip(self) -> bool:
        return self._final_clip

    @final_clip.setter
    def final_clip(self, value: bool) -> None:
        if self._final_clip != value:
            self._final_clip = value
            self.final_clip_changed.emit(value)

    @property
    def latency_pref(self) -> int:
        return self._latency_pref

    @latency_pref.setter
    def latency_pref(self, value: int) -> None:
        value = int(value)
        if value not in LATENCY_CHOICES_MS:
            value = LATENCY_CHOICES_MS[1]
        if self._latency_pref != value:
            self._latency_pref = value
            self.latency_pref_changed.emit(value)

    # (C5) route_ok/route_warning eliminados: nadie los leía ni escribía —
    # el aviso de ruteo lo pinta AudioPage.set_route_warning directamente.
    # (R3-A) update_levels_from_enhancer eliminado: nadie lo llamaba —
    # el camino vivo es main_window._refresh_visuals, que escribe
    # input_level/output_level directamente cada tick del timer.

    def sync_from_enhancer(self, enhancer) -> None:
        """Lee todo el estado del Enhancer y emite senales."""
        self.volume = float(enhancer.volume)
        self.bass = float(enhancer.bass)
        self.treble = float(enhancer.treble)
        self.eq_gains = [float(g) for g in enhancer.eq_gains]
        self.limiter = bool(enhancer.limiter)
        self.compressor = bool(enhancer.compressor)
        self.true_peak = bool(enhancer.true_peak)
        self.safety_ceiling = bool(enhancer.safety_ceiling_enabled)
        self.final_clip = bool(enhancer.final_clip_enabled)
        self.ab_enabled = float(enhancer.blend) > 0.5
