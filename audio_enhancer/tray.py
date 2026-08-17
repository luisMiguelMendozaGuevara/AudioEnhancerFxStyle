"""Icono de bandeja del sistema (pystray).

Los handlers de pystray se ejecutan en un hilo distinto al de Tk: todos
arrancan con ``root.after(0, ...)`` para marshallar la llamada al hilo
principal y evitar el crash de Tk (ver Fase 1 del plan).
"""

import logging
import threading

from .constants import WINDOW_TITLE
from .i18n import detect_system_language, translate

logger = logging.getLogger("audio_enhancer.tray")


class TrayIcon:
    """Wrapper de pystray con callbacks marshaled al hilo de Tk."""

    def __init__(self, root, image, show_hide, toggle_audio, on_quit):
        self.root = root
        self.image = image
        self.show_hide = show_hide
        self.toggle_audio = toggle_audio
        self.on_quit = on_quit
        self._icon = None

    def start(self):
        """Crea el icono y lo corre en su propio hilo daemon."""
        import pystray

        if self._icon is not None:
            return
        lang = detect_system_language()
        menu = pystray.Menu(
            pystray.MenuItem(translate("Mostrar / Ocultar", lang), self._toggle_show, default=True),
            pystray.MenuItem(translate("Iniciar / Detener", lang), self._toggle_audio_cb),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(translate("Salir", lang), self._quit_cb),
        )
        icon = pystray.Icon("AudioEnhancerFxStyle", self.image, WINDOW_TITLE, menu)
        self._icon = icon
        threading.Thread(target=icon.run, daemon=True).start()

    def stop(self):
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                logger.debug("Error al detener el icono de bandeja", exc_info=True)

    # ---------- handlers (marshaled al hilo de Tk) ----------

    def _toggle_show(self, icon=None, item=None):
        self.root.after(0, self.show_hide)

    def _toggle_audio_cb(self, icon=None, item=None):
        self.root.after(0, self.toggle_audio)

    def _quit_cb(self, icon=None, item=None):
        self.root.after(0, self.on_quit)
