"""C1/C2: heurísticas puras de nombres de dispositivo (sin Qt ni PortAudio)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_enhancer.device_utils import is_bluetooth_name, pick_capture_source, pick_default_output


def test_bluetooth_por_marca_y_por_nombre_windows():
    assert is_bluetooth_name("Auriculares Bluetooth") is True
    assert is_bluetooth_name("Headphones (A2DP)") is True
    assert is_bluetooth_name("Manos libres - HFP") is True
    assert is_bluetooth_name("Altavoces (Realtek)") is False
    assert is_bluetooth_name("") is False
    assert is_bluetooth_name(None) is False


def test_pick_default_output_prefiere_parlantes_sobre_hdmi():
    # HDMI aparece primero, pero la salida natural es la de parlantes.
    names = ["Monitor (HDMI)", "Altavoces (Realtek High Definition Audio)", "SPDIF Digital"]
    assert pick_default_output(names) == 1


def test_pick_default_output_empate_gana_el_primero():
    names = ["Salida A", "Salida B"]
    assert pick_default_output(names) == 0


def test_pick_default_output_lista_vacia():
    assert pick_default_output([]) == 0


def test_pick_default_output_sin_pistas_elige_primera():
    names = ["Line Out 1", "Line Out 2"]
    assert pick_default_output(names) == 0


# ---------- pick_capture_source (modo nativo sin cable virtual) ----------


def test_pick_capture_source_prefiere_loopback_de_la_salida_elegida():
    """Sin cable, captura el loopback del MISMO dispositivo de salida (nativo)."""
    loopbacks = [
        "Altavoces (Synaptics) [Loopback]",
        "CABLE Input (VB-Audio) [Loopback]",
        "Monitor HDMI [Loopback]",
    ]
    # Salida = Altavoces -> debe elegir su loopback, NO el CABLE ni el primero.
    assert pick_capture_source(loopbacks, "Altavoces (Synaptics HD)") == 0
    assert pick_capture_source(loopbacks, "Monitor HDMI") == 2


def test_pick_capture_source_prefiere_cable_si_no_hay_match_de_salida():
    """Si la salida no tiene loopback propio, prefiere un cable virtual."""
    loopbacks = ["Altavoces (Realtek) [Loopback]", "CABLE Input (VB-Audio) [Loopback]"]
    assert pick_capture_source(loopbacks, "Salida Bluetooth XYZ") == 1


def test_pick_capture_source_heuristica_si_no_hay_cable_ni_match():
    """Sin cable ni coincidencia de salida: loopback de parlantes sobre HDMI."""
    loopbacks = ["Monitor (HDMI) [Loopback]", "Altavoces (Realtek) [Loopback]"]
    assert pick_capture_source(loopbacks, "") == 1


def test_pick_capture_source_lista_vacia():
    assert pick_capture_source([], "Altavoces") == 0


def test_pick_capture_source_match_tiene_prioridad_sobre_cable():
    """El match con la salida elegida manda sobre el cable (feedback correcto)."""
    loopbacks = ["CABLE Input (VB-Audio) [Loopback]", "Altavoces (Synaptics) [Loopback]"]
    assert pick_capture_source(loopbacks, "Altavoces (Synaptics)") == 1
