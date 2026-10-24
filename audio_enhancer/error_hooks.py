"""Hooks de errores: excepciones no capturadas y avisos de Qt van al log.

En el EXE (console=False) nadie ve el stderr: sin estos hooks, una excepcion
en un slot de Qt o en un hilo de trabajo se pierde sin rastro en el log y el
diagnostico queda a ciegas.
"""

from __future__ import annotations

import logging
import sys
import threading


def install_exception_hooks() -> None:
    """Redirige sys.excepthook y threading.excepthook al log (nivel ERROR)."""
    error_logger = logging.getLogger("audio_enhancer.errors")

    def _excepthook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        error_logger.error("Excepcion no capturada", exc_info=(exc_type, exc, tb))

    def _thread_hook(args) -> None:
        error_logger.error(
            "Excepcion en el hilo %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook


def install_qt_message_handler() -> None:
    """Manda los mensajes de Qt al log (warning/error; debug filtrado).

    Debe llamarse despues de crear QApplication (y antes de construir la UI)
    para capturar tambien los avisos de construccion de la ventana."""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    qt_logger = logging.getLogger("audio_enhancer.qt")

    def _handler(mode, context, message) -> None:
        where = f"{context.file or '?'}:{context.line or 0}"
        if mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            qt_logger.error("%s (%s)", message, where)
        elif mode == QtMsgType.QtWarningMsg:
            qt_logger.warning("%s (%s)", message, where)
        else:
            qt_logger.debug("%s", message)

    qInstallMessageHandler(_handler)
