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
    # Instala desde el extra [dev] de pyproject (runtime + pytest/ruff/pyinstaller):
    # las versiones viven en UN solo lugar (pyproject.toml), no en el workflow.
    assert '".[dev]"' in text


def _dep_names(specs) -> set[str]:
    """Nombres de paquete normalizados ('numpy>=1.26' -> 'numpy')."""
    import re

    return {re.split(r"[<>=!\[; ]", d.strip())[0].lower() for d in specs if d.strip()}


def _names_from_requirements(path: Path) -> set[str]:
    """Nombres declarados en un requirements*.txt (ignora comentarios/opciones)."""
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        out |= _dep_names([line])
    return out


def test_build_produce_instalador_y_portable():
    """El CI debe construir AMBOS artefactos desde specs que existen: el
    instalador (onedir + Inno Setup) y el portable (onefile)."""
    text = _read("pyinstaller-build.yml")
    root = WORKFLOWS.parent.parent
    # Specs referenciados por el workflow y presentes en el repo.
    assert "AudioEnhancerFxStyle.spec" in text
    assert "AudioEnhancerFxStyle-onefile.spec" in text
    assert (root / "AudioEnhancerFxStyle.spec").exists()
    assert (root / "AudioEnhancerFxStyle-onefile.spec").exists()
    # Inno Setup produce el instalador (no se usa --onefile a mano en el CI).
    assert "ISCC.exe" in text
    assert "--onefile" not in text
    # Publica ambos en la release.
    assert text.count("AudioEnhancerFxStyle-Setup-") >= 1


def test_iss_version_alineada_con_constants():
    """El default del instalador (.iss) debe coincidir con APP_VERSION; el CI
    además pasa /DAppVersion desde constants.py, así que un desajuste del
    default local es lo único que este test puede cazar."""
    import re

    from audio_enhancer.constants import APP_VERSION

    root = WORKFLOWS.parent.parent
    iss = (root / "installer" / "AudioEnhancerFxStyle.iss").read_text(encoding="utf-8")
    m = re.search(r'#define AppVersion "([^"]+)"', iss)
    assert m, "el .iss no define AppVersion"
    assert m.group(1) == APP_VERSION, f".iss={m.group(1)} != APP_VERSION={APP_VERSION}"
    # El workflow debe pasar la versión explícitamente (no confiar en el default).
    assert "/DAppVersion=" in _read("pyinstaller-build.yml")


def test_readme_documenta_ambos_artefactos():
    root = WORKFLOWS.parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "AudioEnhancerFxStyle.spec" in readme
    assert "AudioEnhancerFxStyle-onefile.spec" in readme
    assert "--onefile" not in readme.split("## Building an Executable")[0]


def test_requirements_alineado_con_pyproject():
    """requirements*.txt son duplicados manuales de pyproject.toml; este test
    caza desincronizaciones silenciosas (la fuente de verdad es pyproject)."""
    import tomllib

    root = WORKFLOWS.parent.parent
    with open(root / "pyproject.toml", "rb") as f:
        pp = tomllib.load(f)

    runtime = _dep_names(pp["project"]["dependencies"])
    dev = _dep_names(pp["project"]["optional-dependencies"]["dev"])

    in_req = _names_from_requirements(root / "requirements.txt")
    in_req_dev = _names_from_requirements(root / "requirements-dev.txt")

    assert runtime <= in_req, f"requirements.txt desincronizado: faltan {runtime - in_req}"
    assert dev <= in_req_dev, f"requirements-dev.txt desincronizado: faltan {dev - in_req_dev}"
