"""Smoke test: los modulos de la app importan y las constantes son coherentes
(sin instanciar la GUI, que requiere pantalla/Qt)."""

import importlib
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _dep_names(specs) -> set[str]:
    return {re.split(r"[<>=!\[; ]", d.strip())[0].lower() for d in specs if d.strip()}


def _names_from_requirements(path: Path) -> set[str]:
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r", "--")):
            continue
        out |= _dep_names([line])
    return out


def test_importan_todos_los_modulos():
    for nombre in (
        "audio_enhancer.autostart",
        "audio_enhancer.constants",
        "audio_enhancer.audio_controller",
        "audio_enhancer.config",
        "audio_enhancer.config_manager",
        "audio_enhancer.device_utils",
        "audio_enhancer.dsp",
        "audio_enhancer.engine",
        "audio_enhancer.i18n",
        "audio_enhancer.main",
        "audio_enhancer.single_instance",
        "audio_enhancer.startup_metrics",
        "audio_enhancer.ui",
        "audio_enhancer.ui.new",
        "audio_enhancer.ui.new.main_window",
    ):
        importlib.import_module(nombre)


def test_constantes_coherentes():
    from audio_enhancer.constants import (
        APP_VERSION,
        DEFAULT_PRESET,
        SAMPLE_RATE,
        WINDOW_TITLE,
    )
    from audio_enhancer.i18n import PRESETS

    assert isinstance(APP_VERSION, str) and APP_VERSION
    assert SAMPLE_RATE > 0
    assert WINDOW_TITLE
    assert DEFAULT_PRESET in PRESETS


def test_version_pyproject_igual_a_constantes():
    """La versión vive en dos sitios (pyproject + APP_VERSION): no deben derivar."""
    with open(ROOT / "pyproject.toml", "rb") as f:
        project = tomllib.load(f)["project"]
    from audio_enhancer.constants import APP_VERSION

    assert project["version"] == APP_VERSION


def test_requirements_alineados_con_pyproject():
    """requirements*.txt declara los mismos paquetes que pyproject (sin deriva manual)."""
    with open(ROOT / "pyproject.toml", "rb") as f:
        project = tomllib.load(f)["project"]
    esperados = _dep_names(project["dependencies"])
    esperados |= _dep_names(project["optional-dependencies"]["dev"])
    for nombre in ("requirements.txt", "requirements-dev.txt"):
        assert _names_from_requirements(ROOT / nombre) <= esperados, nombre
