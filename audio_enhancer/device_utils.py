"""Utilidades PURAS sobre nombres de dispositivos de audio (sin Qt/PortAudio).

Los nombres de dispositivo los elige Windows y cambian de una PC a otra y con
el idioma del sistema; aquí vive la heurística (testeable) para dos decisiones:

- ``is_bluetooth_name``: avisar de latencia en salidas Bluetooth (A2DP/HFP).
- ``pick_default_output``: elegir una salida razonable (parlantes/auriculares)
  en vez de, p. ej., un HDMI/SPDIF que aparece primero en la enumeración.
"""

from __future__ import annotations

# Marcas de que el dispositivo es inalámbrico (Bluetooth). Se comparan en
# minúsculas y son marcas técnicas estándar (independientes del idioma), más
# el nombre en español de Windows.
BLUETOOTH_KEYWORDS = (
    "bluetooth",
    "a2dp",
    "avrcp",
    "hands-free",
    "handsfree",
    "manos libres",
    "manos-libres",
    "hfp",
)

# Salidas que NO son el destino natural (monitor externo / digital).
_NON_PRIMARY_KEYWORDS = ("hdmi", "displayport", "spdif", "digital", "nvidia", "amd hdmi")

# Señales de salida principal en ES/EN y fabricantes típicos de laptop.
_PRIMARY_HINTS = (
    "altavoces",
    "speakers",
    "auriculares",
    "headphones",
    "realtek",
    "conexant",
    "synaptics",
    "cirrus",
)


def is_bluetooth_name(name: str | None) -> bool:
    """True si el nombre del dispositivo parece una salida Bluetooth."""
    n = (name or "").lower()
    return any(k in n for k in BLUETOOTH_KEYWORDS)


def output_preference_score(name: str | None) -> int:
    """Puntuación de preferencia como salida principal (mayor = mejor).

    No es una verdad absoluta: es una heurística para el auto-select inicial;
    el usuario siempre puede cambiar la salida en la página Audio."""
    n = (name or "").lower()
    score = 0
    if any(k in n for k in _PRIMARY_HINTS):
        score += 2
    if any(k in n for k in _NON_PRIMARY_KEYWORDS):
        score -= 3
    return score


def pick_default_output(names: list[str]) -> int:
    """Índice de la salida más probable como principal (0 si la lista está vacía).

    Empate: gana el primero (estable respecto al orden de Windows)."""
    if not names:
        return 0
    best_idx, best_score = 0, output_preference_score(names[0])
    for i, name in enumerate(names[1:], start=1):
        s = output_preference_score(name)
        if s > best_score:
            best_idx, best_score = i, s
    return best_idx
