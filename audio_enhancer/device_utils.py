"""Utilidades PURAS sobre nombres de dispositivos de audio (sin Qt/PortAudio).

Los nombres de dispositivo los elige Windows y cambian de una PC a otra y con
el idioma del sistema; aquí vive la heurística (testeable) para dos decisiones:

- ``is_bluetooth_name``: avisar de latencia en salidas Bluetooth (A2DP/HFP).
- ``pick_default_output``: elegir una salida razonable (parlantes/auriculares)
  en vez de, p. ej., un HDMI/SPDIF que aparece primero en la enumeración.
"""

from __future__ import annotations

from .constants import CABLE_KEYWORDS

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


def _strip_loopback_suffix(name: str) -> str:
    """'Altavoces (Synaptics HD) [Loopback]' -> 'altavoces' (clave base).

    Windows muestra el mismo dispositivo con sufijos distintos entre la lista
    de salidas y el loopback ('Altavoces (Synaptics)' vs 'Altavoces
    (Synaptics) [Loopback]' o '(Synaptics HD)'). Se normaliza a la parte antes
    del primer paréntesis, en minúsculas y sin el sufijo [Loopback]."""
    n = name.lower().replace("[loopback]", "").strip()
    n = n.split("(")[0].strip()  # descarta el fabricante/paréntesis
    return " ".join(n.split())


def pick_capture_source(loopback_names: list[str], output_name: str | None) -> int:
    """Índice del loopback a capturar, SIN depender de un cable virtual.

    Orden de preferencia:
      1. Loopback del MISMO dispositivo que la salida elegida (feedback
         controlado: procesas lo que suena; es el caso del cable virtual).
      2. Un cable virtual explícito (VB-CABLE/VoiceMeeter) si está presente.
      3. El loopback de salida más probable (parlantes/auriculares), no un
         HDMI/SPDIF que aparezca primero.
      4. El primero (fallback estable).

    Así la app funciona en modo NATIVO (sin instalar nada) capturando el
    loopback del dispositivo de reproducción activo, y sigue prefiriendo el
    cable si el usuario lo tiene (permite enrutado manual)."""
    names = [str(n) for n in loopback_names]
    if not names:
        return 0
    out_key = _strip_loopback_suffix(output_name or "")
    if out_key:
        # Comparación por CONTENCIÓN: Windows trunca los nombres (la lista de
        # salidas muestra 'Altavoces (Synaptics)' y el loopback
        # 'Altavoces (Synaptics) [Loopback]', pero en otras PCs difieren en
        # sufijos como ' HD').
        for i, n in enumerate(names):
            key = _strip_loopback_suffix(n)
            if key == out_key or key.startswith(out_key) or out_key.startswith(key):
                return i
    for i, n in enumerate(names):
        low = n.lower()
        if any(k in low for k in CABLE_KEYWORDS):
            return i
    return pick_default_output(names)
