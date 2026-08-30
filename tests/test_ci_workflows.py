"""Sanidad de los workflows de GitHub Actions (revisión R2, hallazgo A3).

Regresión: el workflow de PyInstaller instalaba dependencias desde
``requirements.txt``, un fichero ELIMINADO en la Fase 0 (ahora la fuente de
verdad es pyproject.toml), por lo que el job fallaba siempre. Este test
fija el contrato: los triggers apuntan a la rama principal y ningún
workflow referencia el requirements.txt purgado.

Las agujas se construyen por concatenación para que el literal de la rama
nunca aparezca entero en el código fuente (inmune a mangling de transporte).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

BRANCH_TRIGGER = "branches: [" + "m" + "ain" + "]"


def _read(name: str) -> str:
    path = WORKFLOWS / name
    assert path.exists(), f"falta el workflow {name}"
    return path.read_text(encoding="utf-8")


def test_triggers_de_tests_y_lint_apuntan_a_principal():
    for name in ("python-application.yml", "ruff.yml"):
        text = _read(name)
        assert text.count(BRANCH_TRIGGER) == 2, f"{name}: push y pull_request deben disparar en la rama principal"


def test_pyinstaller_instala_desde_pyproject():
    text = _read("pyinstaller-build.yml")
    assert "requirements.txt" not in text, "requirements.txt fue purgado en Fase 0"
    assert "pyproject.toml" in text, "la cache de pip debe seguir a pyproject.toml"
    assert "pip install . pyinstaller" in text
