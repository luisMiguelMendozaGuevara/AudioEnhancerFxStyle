# -*- coding: utf-8 -*-
"""Widgets reutilizables (customtkinter/tk)."""

import tkinter as tk

import customtkinter as ctk


class ToolTip:
    """Tooltip ligero basado en eventos, sin sondeo periódico de la UI."""

    def __init__(self, widget, text, delay=550, max_seconds=8):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.max_ms = max_seconds * 1000
        self.tip = None
        self._after_id = None
        self._hide_after_id = None
        target = widget
        for attr in ("_canvas", "_draw_engine"):
            inner = getattr(widget, attr, None)
            if inner is not None and hasattr(inner, "bind"):
                target = inner
                break
        self.target = target
        target.bind("<Enter>", self._on_enter, add="+")
        target.bind("<Leave>", self._on_leave, add="+")
        target.bind("<ButtonPress>", self._hide_now, add="+")

    def _on_enter(self, _=None):
        self._cancel_scheduled()
        if self.text:
            self._after_id = self.widget.after(self.delay, self._show_now)

    def _on_leave(self, _=None):
        self._cancel_scheduled()
        self._hide_now()

    def _cancel_scheduled(self):
        for attr in ("_after_id", "_hide_after_id"):
            ident = getattr(self, attr)
            if ident is not None:
                try:
                    self.widget.after_cancel(ident)
                except Exception:
                    pass
                setattr(self, attr, None)

    # ---------- mostrar / ocultar ----------

    def _show_now(self):
        if self.tip is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 22
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
            self.tip = ctk.CTkToplevel(self.widget.winfo_toplevel())
            self.tip.wm_overrideredirect(True)
            self.tip.geometry("+%d+%d" % (x, y))
            self.tip.attributes("-topmost", True)
            label = ctk.CTkLabel(self.tip, text=self.text, wraplength=300,
                                 justify="left", font=("Segoe UI", 11),
                                 fg_color=("gray88", "gray18"),
                                 text_color=("gray10", "gray90"),
                                 corner_radius=8)
            label.pack(padx=8, pady=6)
            self._make_click_through(label)
            self.tip.lift()
            self._after_id = None
            self._hide_after_id = self.widget.after(self.max_ms, self._hide_now)
        except Exception:
            self.tip = None

    @staticmethod
    def _make_click_through(label):
        """La ventana del tooltip no intercepta clics (WS_EX_TRANSPARENT)."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetAncestor(
                ctypes.windll.user32.GetParent(label.winfo_id()), 2)  # GA_ROOT=2
            gwl = -20  # GWL_EXSTYLE
            wsex = ctypes.windll.user32.GetWindowLongW(hwnd, gwl)
            # WS_EX_TRANSPARENT(0x20) | WS_EX_NOACTIVATE(0x08000000) | WS_EX_TOOLWINDOW(0x80)
            ctypes.windll.user32.SetWindowLongW(hwnd, gwl, wsex | 0x20 | 0x08000000 | 0x80)
        except Exception:
            pass

    def _hide_now(self, _=None):
        if self._after_id is not None or self._hide_after_id is not None:
            self._cancel_scheduled()
        if self.tip is not None:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


class ScrollBody(ctk.CTkFrame):
    """Contenedor con scroll vertical fluido (tk.Canvas + frame interno).

    En lugar de CTkScrollableFrame (lento y con artefactos en Windows) usa un
    canvas plano: los widgets se colocan de una vez en ``inner`` y el scroll
    solo mueve la vista. El scrollbar se oculta cuando el contenido cabe.
    """

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(
            self, highlightthickness=0, bd=0,
            bg=ctk.ThemeManager.theme["CTk"]["fg_color"][1],
            yscrollincrement=24)
        self._vsb = ctk.CTkScrollbar(self, orientation="vertical",
                                     command=self.canvas.yview, corner_radius=8)
        self.canvas.configure(yscrollcommand=self._on_vsb_needed)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._vsb.pack(side="right", fill="y", padx=(2, 1))
        self._vsb_visible = True
        self.inner = ctk.CTkFrame(self.canvas, fg_color="transparent")
        self._win_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_cfg)
        self.canvas.bind("<Configure>", self._on_canvas_cfg)
        self.canvas.bind("<Enter>", lambda _: self._bind_wheel(True))
        self.canvas.bind("<Leave>", lambda _: self._bind_wheel(False))
        self.canvas.bind("<Button-4>", self._wheel_up)   # soporte Linux
        self.canvas.bind("<Button-5>", self._wheel_down)
        self.canvas.bind("<MouseWheel>", self._on_wheel)

    def _on_vsb_needed(self, first, last):
        self._vsb.set(first, last)
        # No repaquetar el scrollbar en cada evento de scroll: en Windows
        # provocaba relayout y tirones visibles durante la rueda.
        try:
            needed = not (float(first) <= 0.0 and float(last) >= 1.0)
            if needed == self._vsb_visible:
                return
            self._vsb_visible = needed
            if needed:
                self._vsb.pack(side="right", fill="y", padx=(2, 1))
            else:
                self._vsb.pack_forget()
        except Exception:
            pass

    def _on_inner_cfg(self, _):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_cfg(self, e):
        self.canvas.itemconfigure(self._win_id, width=e.width)

    def _bind_wheel(self, on):
        if on:
            self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        else:
            self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def _wheel_up(self, _):
        self.canvas.yview_scroll(-1, "units")

    def _wheel_down(self, _):
        self.canvas.yview_scroll(1, "units")