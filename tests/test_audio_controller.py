"""R3-C1: ruteo y ciclo de vida en AudioController (puro, sin Qt-Widgets).

La validación de ruteo vive en el controlador (evaluate_route); la ventana
solo traduce la clave y colorea. Estas pruebas van sin QApplication."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
