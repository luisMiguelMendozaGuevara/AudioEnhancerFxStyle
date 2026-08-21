from PySide6.QtWidgets import QApplication

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
