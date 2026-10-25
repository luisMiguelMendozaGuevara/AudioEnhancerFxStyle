"""Hooks de errores: las excepciones no capturadas llegan al log.

En el EXE sin consola, sin estos hooks un fallo en un slot de Qt o en un hilo
se pierde sin rastro; el test verifica que quedan instalados y no revientan."""

from __future__ import annotations

import logging
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_enhancer.error_hooks import install_exception_hooks, make_qt_message_handler


def test_install_exception_hooks_registra_en_el_log(caplog):
    orig_sys, orig_thread = sys.excepthook, threading.excepthook
    try:
        install_exception_hooks()
        assert sys.excepthook is not orig_sys
        assert threading.excepthook is not orig_thread
        with caplog.at_level(logging.ERROR, logger="audio_enhancer.errors"):
            try:
                raise ValueError("boom de prueba")
            except ValueError:
                sys.excepthook(*sys.exc_info())
            args = threading.ExceptHookArgs((RuntimeError, RuntimeError("hilo"), None, threading.current_thread()))
            threading.excepthook(args)
        texto = caplog.text
        assert "Excepcion no capturada" in texto
        assert "boom de prueba" in texto
        assert "hilo" in texto
    finally:
        sys.excepthook, threading.excepthook = orig_sys, orig_thread


def test_qt_message_handler_mapea_niveles(caplog):
    """warning->WARNING, critical->ERROR y debug->DEBUG con archivo:linea."""
    import logging
    from types import SimpleNamespace

    from PySide6.QtCore import QtMsgType

    handler = make_qt_message_handler()
    ctx = SimpleNamespace(file="vista.py", line=42)
    with caplog.at_level(logging.DEBUG, logger="audio_enhancer.qt"):
        handler(QtMsgType.QtWarningMsg, ctx, "aviso")
        handler(QtMsgType.QtCriticalMsg, ctx, "critico")
        handler(QtMsgType.QtDebugMsg, ctx, "detalle")

    por_nivel = {(r.levelno, r.getMessage().split(" (")[0]) for r in caplog.records}
    assert (logging.WARNING, "aviso") in por_nivel
    assert (logging.ERROR, "critico") in por_nivel
    assert (logging.DEBUG, "detalle") in por_nivel
    assert all("vista.py:42" in r.getMessage() for r in caplog.records if " (" in r.getMessage())
