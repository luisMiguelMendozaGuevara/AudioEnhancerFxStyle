"""C1/C2: heurísticas puras de nombres de dispositivo (sin Qt ni PortAudio)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_enhancer.device_utils import is_bluetooth_name, pick_default_output


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
