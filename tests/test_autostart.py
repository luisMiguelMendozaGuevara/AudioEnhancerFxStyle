"""Autoarranque con Windows: comando entrecomillado y degradación elegante.

Regresión del hallazgo A1 de la revisión R2: la cadena del comando llegó
corrupta al registro (el código quedó DENTRO de un string literal) y el
autoarranque escribía basura sin sentido. Estas pruebas fijan el contrato
del comando y el comportamiento sin winreg (Linux/CI).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_enhancer import autostart


def test_comando_entrecomillado(monkeypatch):
    """El comando cita EJECUTABLE y SCRIPT por separado (rutas con espacios)."""
    exe = "/path con espacios/python3"
    script = "script con espacios.py"
    monkeypatch.setattr(sys, "executable", exe, raising=False)
    monkeypatch.setattr(sys, "argv", [script], raising=False)
    cmd = autostart.build_autostart_command()
    # Regresión A1: antes se escribía el LITERAL ' + sys.executable + ... ' al
    # registro; ahora hay dos tokens citados, uno por ruta.
    assert cmd == f'"{exe}" "{os.path.abspath(script)}"'
    assert cmd.count('"') == 4


def test_comando_sin_espacios_tambien_cita(monkeypatch):
    monkeypatch.setattr(sys, "executable", "python3", raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py"], raising=False)
    cmd = autostart.build_autostart_command()
    # En Windows: "python3" "C:\...\main.py"  |  En Linux: "python3" "/opt/app/main.py"
    assert cmd == f'"python3" "{os.path.abspath("main.py")}"'


def test_comando_frozen_no_duplica_la_ruta(monkeypatch):
    """Empaquetado (PyInstaller): sys.executable y sys.argv[0] son el mismo exe;
    el comando no debe citar la ruta dos veces."""
    exe = r"C:\Program Files\AudioEnhancerFxStyle\AudioEnhancerFxStyle.exe"
    monkeypatch.setattr(sys, "executable", exe, raising=False)
    monkeypatch.setattr(sys, "argv", [exe], raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = autostart.build_autostart_command()
    assert cmd == f'"{exe}"'
    assert cmd.count('"') == 2


def test_sin_winreg_degrada_sin_lanzar(monkeypatch):
    """En plataformas sin winreg (Linux/CI) is/set_enabled no lanzan."""
    import builtins

    import sys

    real_import = builtins.__import__

    def _sin_winreg(name, *args, **kwargs):
        if name == "winreg":
            raise ImportError("winreg solo existe en Windows")
        return real_import(name, *args, **kwargs)

    # Si winreg YA está en sys.modules (otro test lo importó), el import no
    # pasaría por __import__ y el test sería un falso positivo. Se elimina del
    # cache para que la simulación sea determinista e independiente del orden.
    monkeypatch.delitem(sys.modules, "winreg", raising=False)
    monkeypatch.setattr(builtins, "__import__", _sin_winreg)
    assert autostart.is_enabled() is False
    assert autostart.set_enabled(True) is False
    assert autostart.set_enabled(False) is False
