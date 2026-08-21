"""Tests de paridad funcional de la UI Qt respecto a la antigua UI Tk."""

from unittest import mock

from PySide6.QtWidgets import QApplication

from audio_enhancer.constants import DANGER, OK, WARN
from audio_enhancer.ui.qt_main_window import QtMainWindow

_APP = QApplication.instance() or QApplication([])


def _make_window():
    window = QtMainWindow()
    window.build_content()
    window.tray.hide()
    return window


def test_norm_and_fxsound_helpers():
    window = _make_window()
    try:
        assert window._norm("CABLE Input (VB-Audio)") == "cableinput(vb-audio)"
        assert window._norm("Speakers  ") == "speakers"
        assert window._is_fxsound("FxSound Output")
        assert not window._is_fxsound("Speakers")
    finally:
        window.close()


def test_route_guard_requires_selection():
    window = _make_window()
    try:
        window._route_guard()
        assert window.go is False
        assert "Selecciona" in window.route_label.text()
        assert WARN in window.route_label.styleSheet()
    finally:
        window.close()


def test_route_guard_rejects_echo_same_device():
    window = _make_window()
    try:
        window.source_combo.addItems(["CABLE Input"])
        window.output_combo.addItems(["CABLE Input"])
        window.source_combo.setCurrentText("CABLE Input")
        window.output_combo.setCurrentText("CABLE Input")
        window._route_guard()
        assert window.go is False
        assert "ECO" in window.route_label.text()
        assert DANGER in window.route_label.styleSheet()
    finally:
        window.close()


def test_route_guard_rejects_virtual_output():
    window = _make_window()
    try:
        window.source_combo.addItems(["CABLE Input"])
        window.output_combo.addItems(["CABLE Output"])
        window.source_combo.setCurrentText("CABLE Input")
        window.output_combo.setCurrentText("CABLE Output")
        window._route_guard()
        assert window.go is False
        assert "virtual" in window.route_label.text().lower()
        assert DANGER in window.route_label.styleSheet()
    finally:
        window.close()


def test_route_guard_accepts_virtual_source_to_physical_output():
    window = _make_window()
    try:
        window.source_combo.addItems(["CABLE Input (VB-Audio)"])
        window.output_combo.addItems(["Speakers (Realtek)"])
        window.source_combo.setCurrentText("CABLE Input (VB-Audio)")
        window.output_combo.setCurrentText("Speakers (Realtek)")
        window._route_guard()
        assert window.go is True
        assert "Ruteo correcto" in window.route_label.text()
        assert OK in window.route_label.styleSheet()
    finally:
        window.close()


def test_route_guard_warns_on_fxsound_capture():
    window = _make_window()
    try:
        window.source_combo.addItems(["FxSound Output"])
        window.output_combo.addItems(["Speakers (Realtek)"])
        window.source_combo.setCurrentText("FxSound Output")
        window.output_combo.setCurrentText("Speakers (Realtek)")
        window._route_guard()
        assert window.go is True
        assert "FxSound" in window.route_label.text()
        assert WARN in window.route_label.styleSheet()
    finally:
        window.close()


def test_apply_config_restores_device_selection_by_name():
    window = _make_window()
    try:
        with mock.patch("audio_enhancer.ui.qt_main_window.load_config") as load:
            load.return_value = {
                "source": "CABLE Input",
                "output": "Speakers (Realtek)",
                "preset": "Plano (sin efectos)",
                "volume": 1.0,
                "bass": 0.0,
                "treble": 0.0,
                "eq_gains": [0.0] * 9,
                "limiter": True,
                "compressor": True,
            }
            window._apply_config()
        assert window._keep_src == "CABLE Input"
        assert window._keep_out == "Speakers (Realtek)"

        window.loopbacks = [{"name": "CABLE Input", "index": 0}]
        window.speakers = [{"name": "Speakers (Realtek)", "index": 1}]
        window.source_combo.addItems(["CABLE Input"])
        window.output_combo.addItems(["Speakers (Realtek)"])
        window._restore_device_selection()

        assert window.source_combo.currentText() == "CABLE Input"
        assert window.output_combo.currentText() == "Speakers (Realtek)"
        assert window._keep_src == ""
        assert window._keep_out == ""
    finally:
        window.close()


def test_autostart_checkbox_reflects_registry_state():
    window = _make_window()
    try:
        with (
            mock.patch("audio_enhancer.ui.qt_main_window.load_config") as load,
            mock.patch.object(window, "_autostart_enabled", return_value=True),
        ):
            load.return_value = {
                "preset": "Plano (sin efectos)",
                "volume": 1.0,
                "bass": 0.0,
                "treble": 0.0,
                "eq_gains": [0.0] * 9,
                "limiter": True,
                "compressor": True,
            }
            window._apply_config()
        assert window.autostart_check.isChecked() is True
    finally:
        window.close()


def test_autostart_toggle_reports_success():
    window = _make_window()
    try:
        with mock.patch.object(window, "_set_auto_start", return_value=True):
            window._toggle_autostart(True)
        assert "activado" in window.status.text()
        assert OK in window.status.styleSheet()
    finally:
        window.close()
