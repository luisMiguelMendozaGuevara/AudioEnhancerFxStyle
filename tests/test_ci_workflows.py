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


def _specs_from_requirements(path: Path) -> set[str]:
    """Especificaciones declaradas en un requirements*.txt (sin espacios)."""
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        out.add(line.replace(" ", ""))
    return out


def test_pyproject_version_alineada_con_constants():
    """La versión en pyproject.toml debe coincidir con APP_VERSION en constants.py."""
    import tomllib

    from audio_enhancer.constants import APP_VERSION

    root = WORKFLOWS.parent.parent
    with open(root / "pyproject.toml", "rb") as f:
        pp = tomllib.load(f)
    version = pp["project"]["version"]
    assert version == APP_VERSION, f"pyproject.toml={version} != APP_VERSION={APP_VERSION}"


def test_requirements_alineado_con_pyproject():
    """requirements*.txt son duplicados manuales de pyproject.toml; este test
    caza desincronizaciones silenciosas (la fuente de verdad es pyproject)."""
    import tomllib

    root = WORKFLOWS.parent.parent
    with open(root / "pyproject.toml", "rb") as f:
        pp = tomllib.load(f)

    runtime_specs = {d.replace(" ", "") for d in pp["project"]["dependencies"]}
    dev_specs = {d.replace(" ", "") for d in pp["project"]["optional-dependencies"]["dev"]}

    in_req_specs = _specs_from_requirements(root / "requirements.txt")
    in_req_dev_specs = _specs_from_requirements(root / "requirements-dev.txt")

    assert runtime_specs == in_req_specs, (
        f"requirements.txt desincronizado con pyproject.toml: {runtime_specs ^ in_req_specs}"
    )
    assert dev_specs == in_req_dev_specs, (
        f"requirements-dev.txt desincronizado con pyproject.toml: {dev_specs ^ in_req_dev_specs}"
    )

    # El workflow de tests NO duplica especificaciones: instala desde los
    # requirements con constraints, para que las versiones vivan en un solo
    # sitio. Duplicarlas en el YAML hacia imposible que Dependabot pudiera
    # subir versiones (su bump chocaba con el pin copiado del workflow).
    ci_tests = _read("python-application.yml")
    assert "-r requirements-dev.txt" in ci_tests, "el CI debe instalar desde requirements-dev.txt"
    assert "-c constraints.txt" in ci_tests, "el CI debe fijar versiones con constraints.txt"
    for spec in runtime_specs | dev_specs:
        assert f'"{spec}"' not in ci_tests, f"python-application.yml no debe repetir la especificacion {spec}"


def test_dependabot_aplaza_pyside6():
    """PySide6 queda clavado en 6.8 LTS a proposito: la subida a 6.12 es la
    fase 2 (salto de minor de Qt, con historial de crashes 0xC0000005). Si
    Dependabot no lo ignora, abre PRs que no se pueden mergear."""
    text = (WORKFLOWS.parent / "dependabot.yml").read_text(encoding="utf-8")
    assert "pyside6" in text.lower(), "dependabot.yml debe ignorar PySide6 hasta la fase 2"
    assert "shiboken6" in text, "shiboken6 va acoplado a PySide6: ignorarlo tambien"
