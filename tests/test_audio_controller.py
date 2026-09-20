"""R3-C1: ruteo y ciclo de vida en AudioController (puro, sin Qt-Widgets).

La validación de ruteo vive en el controlador (evaluate_route); la ventana
solo traduce la clave y colorea. Estas pruebas van sin QApplication."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from audio_enhancer.audio_controller import AudioController


def test_ruta_incompleta_rechazada():
    go, key = AudioController.evaluate_route("", "Speakers")
    assert go is False and key == "ROUTE_MISSING"
    go, key = AudioController.evaluate_route("CABLE Input", "")
    assert go is False and key == "ROUTE_MISSING"


def test_ruta_eco_mismo_dispositivo():
    go, key = AudioController.evaluate_route("Speakers (Realtek)", "speakers(REALTEK)")
    # Normalización: mayúsculas y espacios no engañan al guardia de eco.
    assert go is False and key == "ROUTE_ECO"


def test_ruta_salida_virtual_rechazada():
    go, key = AudioController.evaluate_route("CABLE Input", "CABLE Output")
    assert go is False and key == "ROUTE_VIRTUAL_OUT"


def test_ruta_virtual_a_fisica_es_la_ideal():
    go, key = AudioController.evaluate_route("CABLE Input", "Speakers (Realtek)")
    assert go is True and key == "ROUTE_OK_VIRTUAL"


def test_ruta_fisica_a_fisica_permitida_con_matiz():
    go, key = AudioController.evaluate_route("Microfono", "Speakers (Realtek)")
    assert go is True and key == "ROUTE_OK_FISICA"


def test_controlador_arranca_detenido_sin_pyaudio():
    """Sin dispositivos (entorno de tests) el controlador nace parado."""
    from audio_enhancer.dsp import Enhancer

    ctrl = AudioController(Enhancer())
    assert ctrl.running is False
    assert ctrl.go is False
    assert ctrl.engine is not None
    ctrl.stop()  # idempotente: no debe lanzar ni emitir running residual
    assert ctrl.running is False


# ---------- watchdog de streams (dispositivo desconectado / driver) ----------


class _FakeStream:
    def __init__(self, active: bool) -> None:
        self._active = active

    def is_active(self) -> bool:
        return self._active


def _ctrl_running():
    from audio_enhancer.dsp import Enhancer

    ctrl = AudioController(Enhancer())
    ctrl.running = True
    return ctrl


def test_check_streams_todo_vivo():
    ctrl = _ctrl_running()
    ctrl.engine.stream = _FakeStream(True)
    ctrl.engine.out_stream = _FakeStream(True)
    assert ctrl.check_streams() is None


def test_check_streams_detecta_salida_caida():
    ctrl = _ctrl_running()
    ctrl.engine.stream = _FakeStream(True)
    ctrl.engine.out_stream = _FakeStream(False)
    assert ctrl.check_streams() == "salida"


def test_check_streams_detecta_captura_caida():
    ctrl = _ctrl_running()
    ctrl.engine.stream = _FakeStream(False)
    ctrl.engine.out_stream = _FakeStream(True)
    assert ctrl.check_streams() == "captura"


def test_check_streams_is_active_que_lanza_cuenta_como_caido():
    ctrl = _ctrl_running()

    class _Boom:
        def is_active(self):
            raise RuntimeError("device gone")

    ctrl.engine.stream = _Boom()
    ctrl.engine.out_stream = _FakeStream(True)
    assert ctrl.check_streams() == "captura"


def test_check_streams_no_inspecciona_si_no_corre():
    ctrl = _ctrl_running()
    ctrl.engine.stream = _FakeStream(False)
    ctrl.running = False
    assert ctrl.check_streams() is None


def test_watchdog_desactivado_no_detiene_el_audio():
    """El watchdog es configurable: apagado, un stream caído ya no detiene el
    audio ni emite stream_lost."""
    ctrl = _ctrl_running()
    ctrl.engine.stream = _FakeStream(False)  # captura caída
    ctrl.engine.out_stream = _FakeStream(True)
    ctrl.set_watchdog_enabled(False)
    assert ctrl.watchdog_enabled is False
    perdidos: list[str] = []
    ctrl.stream_lost.connect(perdidos.append)
    ctrl._on_watchdog()
    assert perdidos == []
    assert ctrl.running is True


def test_set_latency_mueve_la_consigna_y_devuelve_ms():
    ctrl = _ctrl_running()
    ctrl.engine.configure_ring(48000, drift_target_ms=60)
    latency = ctrl.set_latency(100)
    assert ctrl.engine.drift_target == int(48000 * 0.100)
    # Latencia reportada = consigna + un bloque de salida.
    assert latency == pytest.approx(((ctrl.engine.drift_target + 1024) / 48000) * 1000.0)


def test_watchdog_detiene_y_emite_stream_lost():
    ctrl = _ctrl_running()
    ctrl.engine.stream = _FakeStream(False)
    ctrl.engine.out_stream = _FakeStream(True)
    perdidos: list[str] = []
    detenidos: list[bool] = []
    ctrl.stream_lost.connect(perdidos.append)
    ctrl.stopped.connect(lambda: detenidos.append(True))
    ctrl._on_watchdog()
    assert perdidos == ["captura"]
    assert detenidos == [True]
    assert ctrl.running is False


# ---------- negociación de tasa (C6) ----------


def test_negotiate_rate_manda_la_fuente():
    assert AudioController.negotiate_rate({"defaultSampleRate": 44100}, {"defaultSampleRate": 48000}) == 44100


def test_negotiate_rate_fuente_invalida_usa_salida():
    assert AudioController.negotiate_rate({}, {"defaultSampleRate": 96000}) == 96000
    assert AudioController.negotiate_rate({"defaultSampleRate": "x"}, {"defaultSampleRate": 88200}) == 88200
    assert AudioController.negotiate_rate({"defaultSampleRate": 1000}, {"defaultSampleRate": 48000}) == 48000


def test_negotiate_rate_sin_datos_usa_48000():
    assert AudioController.negotiate_rate({}, {}) == 48000
    assert AudioController.negotiate_rate(None, None) == 48000
    assert AudioController.negotiate_rate({"defaultSampleRate": 999999}, {"defaultSampleRate": -5}) == 48000
