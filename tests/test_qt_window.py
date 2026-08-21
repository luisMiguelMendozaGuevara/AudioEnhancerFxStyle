from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from audio_enhancer.constants import WINDOW_TITLE
from audio_enhancer.ui.qt_main_window import QtMainWindow

_APP = QApplication.instance() or QApplication([])


def test_qt_window_exposes_the_main_control_groups():
    window = QtMainWindow()
    window.build_content()

    assert window.source_combo is not None
    assert window.output_combo is not None
    assert len(window.eq_sliders) == 9
    assert window.spectrum_widget is not None

    window.close()


def test_qt_window_uses_the_canonical_title_and_has_tray():
    window = QtMainWindow()

    assert window.windowTitle() == WINDOW_TITLE
    assert isinstance(window.tray, QSystemTrayIcon)

    window.close()


def test_qt_window_close_without_tray_shuts_down():
    window = QtMainWindow()
    window.build_content()
    window.tray.hide()  # offscreen: la bandeja nunca es visible

    window.close()
    # cerrar sin bandeja no debe dejar hilos de spectrum corriendo
    assert not window._spectrum_worker.isRunning()
