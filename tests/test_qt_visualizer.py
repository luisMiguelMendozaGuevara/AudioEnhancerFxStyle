from PySide6.QtWidgets import QApplication

from audio_enhancer.ui.qt_visualizer import SpectrumWidget

_APP = QApplication.instance() or QApplication([])


def test_spectrum_widget_keeps_a_compact_snapshot():
    widget = SpectrumWidget()
    values = [-60.0, -30.0, -10.0, 0.0]

    widget.set_spectrum(values)

    assert widget.spectrum == values
    assert widget.bar_count == 4


def test_spectrum_widget_accepts_empty_snapshot():
    widget = SpectrumWidget()

    widget.set_spectrum(None)

    assert widget.spectrum == []
    assert widget.bar_count == 0
