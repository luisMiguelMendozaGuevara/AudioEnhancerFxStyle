from __future__ import annotations

from PySide6.QtCore import QObject, Signal


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
    peak_level_changed = Signal(float)
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
        self._peak_level: float = 0.0
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
        # Estado de ruta
        self._route_ok: bool = False
        self._route_warning: str = ""

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
        self._input_level = value
        self.input_level_changed.emit(value)

    @property
    def output_level(self) -> float:
        return self._output_level

    @output_level.setter
    def output_level(self, value: float) -> None:
        self._output_level = value
        self.output_level_changed.emit(value)

    @property
    def peak_level(self) -> float:
        return self._peak_level

    @peak_level.setter
    def peak_level(self, value: float) -> None:
        self._peak_level = value
        self.peak_level_changed.emit(value)

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
    def route_ok(self) -> bool:
        return self._route_ok

    @route_ok.setter
    def route_ok(self, value: bool) -> None:
        self._route_ok = value

    @property
    def route_warning(self) -> str:
        return self._route_warning

    @route_warning.setter
    def route_warning(self, value: str) -> None:
        self._route_warning = value

    def sync_from_enhancer(self, enhancer) -> None:
        """Lee todo el estado del Enhancer y emite senales."""
        self.volume = float(enhancer.volume)
        self.bass = float(enhancer.bass)
        self.treble = float(enhancer.treble)
        self.eq_gains = [float(g) for g in enhancer.eq_gains]
        self.limiter = bool(enhancer.limiter)
        self.compressor = bool(enhancer.compressor)
        self.ab_enabled = float(enhancer.blend) > 0.5

    def update_levels_from_enhancer(self, enhancer) -> None:
        """Actualiza niveles y espectro desde el Enhancer (llamada periodica)."""
        self.input_level = float(enhancer.level_peak)
        self.output_level = float(enhancer.level_peak)
        self.peak_level = float(enhancer.level_peak)
