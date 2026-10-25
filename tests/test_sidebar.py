"""Sidebar: clic -> navegacion, estado activo, compacto y repintado."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_sidebar_clic_navega_y_marca_activo(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from audio_enhancer.ui.new.widgets.sidebar import Sidebar

    sidebar = Sidebar()
    sidebar.show()
    got: list[str] = []
    sidebar.page_requested.connect(got.append)

    QTest.mouseClick(sidebar._items[1], Qt.MouseButton.LeftButton)
    assert got == ["equalizer"]
    assert sidebar._current_id == "equalizer"
    assert sidebar._items[1]._active is True
    assert sidebar._items[0]._active is False

    # Clic en la pagina ya activa: no vuelve a emitir.
    QTest.mouseClick(sidebar._items[1], Qt.MouseButton.LeftButton)
    assert got == ["equalizer"]
    sidebar.close()


def test_sidebar_compacto_retranslate_y_pintado(qapp):
    from audio_enhancer.ui.new.theme.colors import Theme
    from audio_enhancer.ui.new.widgets.sidebar import Sidebar

    sidebar = Sidebar()
    sidebar.set_active("home")
    assert sidebar._items[0]._active is True

    sidebar.retranslate(lambda texto: "EN:" + texto)
    assert sidebar._items[0]._text_label.text().startswith("EN:")

    sidebar.set_compact(True)
    assert sidebar.width() == Theme.SIDEBAR_COMPACT_WIDTH
    assert sidebar._items[0]._text_label.isHidden() is True
    sidebar.set_compact(False)
    assert sidebar._items[0]._text_label.isHidden() is False

    # Pintado real del item activo (cubre paintEvent y el tinte del acento).
    sidebar._items[0].grab()
    sidebar.close()
