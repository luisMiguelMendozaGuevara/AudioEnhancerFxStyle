"""Instancia unica: degradacion elegante y logica de guarda.

`single_instance` es la PRIMERA cosa que corre en `main()` y no tenia tests
(11% de cobertura): un fallo aqui deja la app sin arrancar o abriendo ventanas
duplicadas. Se prueba sin depender de una segunda instancia real."""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from audio_enhancer import single_instance


def test_setup_logging_crea_archivo_rotativo(tmp_path, monkeypatch):
    """Configura un handler rotativo a INFO en la ruta de log."""
    log_file = tmp_path / "sub" / "app.log"
    monkeypatch.setattr(single_instance, "LOG_FILE", str(log_file))
    root = logging.getLogger()
    prev_handlers = root.handlers[:]
    prev_level = root.level
    root.handlers = []  # simular proceso limpio
    try:
        single_instance.setup_logging()
        assert log_file.parent.exists()  # crea el directorio
        assert root.level == logging.INFO
        assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)
        logging.getLogger("test").info("hola")
        for h in root.handlers:
            h.flush()
        assert "hola" in log_file.read_text(encoding="utf-8")
    finally:
        for h in root.handlers:
            h.close()
        root.handlers = prev_handlers
        root.setLevel(prev_level)


def test_setup_logging_idempotente_no_duplica_handlers(tmp_path, monkeypatch):
    monkeypatch.setattr(single_instance, "LOG_FILE", str(tmp_path / "app.log"))
    root = logging.getLogger()
    prev_handlers = root.handlers[:]
    root.handlers = []
    try:
        single_instance.setup_logging()
        n = len(root.handlers)
        single_instance.setup_logging()  # segunda llamada
        assert len(root.handlers) == n  # no añade otro
    finally:
        for h in root.handlers:
            h.close()
        root.handlers = prev_handlers


@pytest.mark.skipif(sys.platform != "win32", reason="requiere winreg/ctypes de Windows")
def test_acquire_single_instance_devuelve_handle(monkeypatch):
    """En Windows, sin otra instancia, devuelve un handle (no None)."""
    monkeypatch.setattr(single_instance, "_bring_existing_to_front", lambda: False)
    handle = single_instance.acquire_single_instance()
    assert handle is not None


@pytest.mark.skipif(sys.platform != "win32", reason="requiere winreg/ctypes de Windows")
def test_acquire_sale_si_hay_ventana_existente(monkeypatch):
    """Si hay una ventana de otra instancia, devuelve None (esta copia sale)."""
    monkeypatch.setattr(single_instance, "_bring_existing_to_front", lambda: True)
    assert single_instance.acquire_single_instance() is None


def test_acquire_degrada_sin_ctypes(monkeypatch):
    """Sin ctypes disponible (plataforma rara), deja pasar con un objeto
    centinela en vez de lanzar."""
    import builtins

    real_import = builtins.__import__

    def _sin_ctypes(name, *args, **kwargs):
        if name == "ctypes":
            raise ImportError("ctypes no disponible")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _sin_ctypes)
    handle = single_instance.acquire_single_instance()
    assert handle is not None  # centinela, no None (no debe bloquear el arranque)


def test_bring_existing_degrada_sin_lanzar(monkeypatch):
    """`_bring_existing_to_front` nunca lanza: devuelve False si algo falla."""
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "ctypes":
            raise OSError("user32 no disponible")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert single_instance._bring_existing_to_front() is False
