"""Sistema de diseño centralizado para la nueva UI.

Dirección visual: "instrumento de rack" — neutros de un solo tono con
escalera de luminosidad sutil, acento único fósforo teal (herencia de
osciloscopio) usado solo en acción activa/valor/estado ON, y bordes rgba que
desaparecen cuando no se les busca.

Dos temas: "dark" (carbón) y "light" (blanco). Cambiar con Theme.set_mode()
y reaplicar Theme.stylesheet() en la ventana principal.

Reglas del sistema:
- Un solo tono en superficies: solo cambia la luminosidad (+4-7% por nivel).
- 60/30/10: neutro dominante, gris estructura, teal ~10%.
- Jerarquía de texto en 4 niveles por peso+opacidad, no solo tamaño.
- Inputs más oscuros que su contenedor en dark (inset = "escribe aquí");
  en light, blancos con borde visible.
- Los colores rojo/ámbar son SEMÁNTICOS (clip/peligro), nunca decorativos.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QColor, QFont

# Formato rgba() para QSS. Para QPainter NUNCA usar hex de 9 dígitos:
# Qt lo interpreta como #AARRGGBB. Usar accent_subtle_color().
_PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        # Fondo / superficie (carbón, un solo tono)
        "BACKGROUND": "#131316",
        "SURFACE": "#19191d",
        "SURFACE_ELEVATED": "#202025",
        "SURFACE_HOVER": "#26262c",
        "BORDER": "rgba(255, 255, 255, 0.07)",
        "BORDER_SOLID": "#2a2a30",
        "BORDER_STRONG": "#3a3a42",
        "BORDER_FOCUS": "#3ddad7",
        # Texto (4 niveles)
        "TEXT": "#ececee",
        "TEXT_SECONDARY": "#b8bac1",
        "TEXT_MUTED": "#8a8c94",
        "TEXT_DIM": "#5f6067",
        "TEXT_ON_ACCENT": "#0d2120",
        # Acento (fósforo teal)
        "ACCENT": "#3ddad7",
        "ACCENT_HOVER": "#63e3e0",
        "ACCENT_PRESSED": "#2bbfbc",
        "ACCENT_SUBTLE": "rgba(61, 218, 215, 0.10)",
        # Estado (semántico, desaturado)
        "SUCCESS": "#48b271",
        "SUCCESS_BG": "rgba(72, 178, 113, 0.12)",
        "WARNING": "#e0b45f",
        "WARNING_BG": "rgba(224, 180, 95, 0.12)",
        "DANGER": "#e0564d",
        "DANGER_BG": "rgba(224, 86, 77, 0.12)",
        "INFO": "#3ddad7",
        "INFO_BG": "rgba(61, 218, 215, 0.10)",
        # Controles (inset)
        "CONTROL_BG": "#141418",
        "CONTROL_BORDER": "#2a2a30",
        # Espectro (rampa monocroma del acento)
        "SPECTRUM_BG": "#101013",
        "SPECTRUM_BAR_LOW": "#23807e",
        "SPECTRUM_BAR_MID": "#2fb3b0",
        "SPECTRUM_BAR_HIGH": "#3ddad7",
        "SPECTRUM_BAR_PEAK": "#9ff4f2",
        "SPECTRUM_GRID": "#232329",
        "SPECTRUM_LABEL": "#6a6c73",
        # Meter (semántica de nivel)
        "METER_BG": "#101013",
        "METER_LOW": "#57b884",
        "METER_MID": "#7fc46a",
        "METER_HIGH": "#dfb45c",
        "METER_PEAK": "#e0684f",
        "METER_CLIP": "#ff4040",
        # EQ
        "EQ_CURVE": "#3ddad7",
        "EQ_GRID": "#232329",
        "EQ_BAND_DOT": "#3ddad7",
        "EQ_BAND_DOT_HOVER": "#63e3e0",
        "EQ_FILL": "rgba(61, 218, 215, 0.05)",
        # Sidebar (mismo fondo que el canvas)
        "SIDEBAR_BG": "#131316",
    },
    "light": {
        # Fondo / superficie (blanco/gris claro, un solo tono)
        "BACKGROUND": "#f5f6f7",
        "SURFACE": "#ffffff",
        "SURFACE_ELEVATED": "#ffffff",
        "SURFACE_HOVER": "#eceef0",
        "BORDER": "rgba(0, 0, 0, 0.10)",
        "BORDER_SOLID": "#d9dbdf",
        "BORDER_STRONG": "#c3c6cb",
        "BORDER_FOCUS": "#0ea5a2",
        # Texto (4 niveles)
        "TEXT": "#191b1e",
        "TEXT_SECONDARY": "#43464b",
        "TEXT_MUTED": "#6d7076",
        "TEXT_DIM": "#9a9da3",
        "TEXT_ON_ACCENT": "#062a29",
        # Acento (teal oscurecido para contraste sobre blanco)
        "ACCENT": "#0ea5a2",
        "ACCENT_HOVER": "#26b7b4",
        "ACCENT_PRESSED": "#0a918e",
        "ACCENT_SUBTLE": "rgba(14, 165, 162, 0.10)",
        # Estado (semántico)
        "SUCCESS": "#2f9e63",
        "SUCCESS_BG": "rgba(47, 158, 99, 0.12)",
        "WARNING": "#b8862f",
        "WARNING_BG": "rgba(184, 134, 47, 0.12)",
        "DANGER": "#cc4a42",
        "DANGER_BG": "rgba(204, 74, 66, 0.12)",
        "INFO": "#0ea5a2",
        "INFO_BG": "rgba(14, 165, 162, 0.10)",
        # Controles (blancos con borde visible)
        "CONTROL_BG": "#ffffff",
        "CONTROL_BORDER": "#d9dbdf",
        # Espectro (rampa monocroma del acento)
        "SPECTRUM_BG": "#eef0f2",
        "SPECTRUM_BAR_LOW": "#a9ded9",
        "SPECTRUM_BAR_MID": "#4cc4c0",
        "SPECTRUM_BAR_HIGH": "#0ea5a2",
        "SPECTRUM_BAR_PEAK": "#067d7a",
        "SPECTRUM_GRID": "#e2e4e8",
        "SPECTRUM_LABEL": "#8a8d93",
        # Meter (semántica de nivel)
        "METER_BG": "#eef0f2",
        "METER_LOW": "#2f9e63",
        "METER_MID": "#6ba53a",
        "METER_HIGH": "#c29237",
        "METER_PEAK": "#c25a3d",
        "METER_CLIP": "#e03030",
        # EQ
        "EQ_CURVE": "#0ea5a2",
        "EQ_GRID": "#e2e4e8",
        "EQ_BAND_DOT": "#0ea5a2",
        "EQ_BAND_DOT_HOVER": "#26b7b4",
        "EQ_FILL": "rgba(14, 165, 162, 0.08)",
        # Sidebar (mismo fondo que el canvas)
        "SIDEBAR_BG": "#f5f6f7",
    },
}


class Theme:
    """Constantes visuales de la aplicacion. Consultar, no instanciar."""

    mode = "dark"

    # ---------- Espaciado ----------
    SPACING_XS = 4
    SPACING_SM = 8
    SPACING_MD = 12
    SPACING_LG = 16
    SPACING_XL = 24
    SPACING_XXL = 32
    MARGIN = 16

    # ---------- Radio (concéntrico: outer = inner + padding) ----------
    RADIUS_SM = 4
    RADIUS_MD = 8
    RADIUS_LG = 12
    RADIUS_XL = 16

    # ---------- Tipografia (jerarquía por peso+opacidad sobre todo) ----------
    FONT_FAMILY = "Segoe UI"
    FONT_SIZE_XS = 10
    FONT_SIZE_SM = 11
    FONT_SIZE_MD = 12
    FONT_SIZE_LG = 14
    FONT_SIZE_XL = 18
    FONT_SIZE_TITLE = 22
    FONT_SIZE_HEADER = 26
    FONT_SIZE_HERO = 28
    FONT_WEIGHT_NORMAL = 400
    FONT_WEIGHT_MEDIUM = 500
    FONT_WEIGHT_SEMIBOLD = 600
    FONT_WEIGHT_BOLD = 700

    # ---------- Tamanos ----------
    STATUS_BAR_HEIGHT = 28
    HEADER_HEIGHT = 48
    SLIDER_HEIGHT = 5
    SLIDER_HANDLE = 16
    METER_WIDTH = 8
    TOGGLE_WIDTH = 40
    TOGGLE_HEIGHT = 22

    # ---------- Sidebar ----------
    SIDEBAR_WIDTH = 180
    SIDEBAR_COMPACT_WIDTH = 56
    SIDEBAR_ITEM_HEIGHT = 42
    SIDEBAR_ITEM_RADIUS = 8

    # ---------- Ventana ----------
    MIN_WIDTH = 900
    MIN_HEIGHT = 600
    DEFAULT_WIDTH = 1100
    DEFAULT_HEIGHT = 720

    # ---------- Animacion (ms; <300, ease-out siempre) ----------
    ANIM_FAST = 150
    ANIM_NORMAL = 250
    ANIM_SLOW = 400

    @classmethod
    def set_mode(cls, mode: str) -> None:
        """Aplica la paleta indicada ("dark" o "light") sobre los atributos.

        Los pintores que leen Theme.X en paintEvent toman el nuevo valor en
        el siguiente repaint; el QSS debe reaplicarse en la ventana."""
        if mode not in _PALETTES:
            return
        cls.mode = mode
        for name, value in _PALETTES[mode].items():
            setattr(cls, name, value)

    @classmethod
    def stylesheet(cls) -> str:
        """Genera la hoja de estilos base para toda la aplicacion."""
        return f"""
        /* --- Base --- */
        QMainWindow, QWidget {{
            background: {cls.BACKGROUND};
            color: {cls.TEXT};
            font-family: '{cls.FONT_FAMILY}';
            font-size: {cls.FONT_SIZE_MD}px;
        }}

        /* --- ScrollArea --- */
        QScrollArea {{
            background: transparent;
            border: none;
        }}
        QScrollArea > QWidget > QWidget {{
            background: transparent;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {cls.BORDER_STRONG};
            min-height: 24px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {cls.TEXT_DIM};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: none;
        }}

        /* --- ComboBox (inset) --- */
        QComboBox {{
            background: {cls.CONTROL_BG};
            border: 1px solid {cls.CONTROL_BORDER};
            border-radius: {cls.RADIUS_MD}px;
            padding: 6px 10px;
            color: {cls.TEXT};
            min-height: 22px;
            font-size: {cls.FONT_SIZE_MD}px;
        }}
        QComboBox:hover {{
            border-color: {cls.BORDER_STRONG};
        }}
        QComboBox:focus {{
            border-color: {cls.BORDER_FOCUS};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 24px;
        }}
        QComboBox::down-arrow {{
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid {cls.TEXT_MUTED};
            margin-right: 8px;
        }}
        QComboBox QAbstractItemView {{
            background: {cls.SURFACE_ELEVATED};
            border: 1px solid {cls.BORDER_STRONG};
            border-radius: {cls.RADIUS_MD}px;
            color: {cls.TEXT};
            selection-background-color: {cls.ACCENT_SUBTLE};
            selection-color: {cls.ACCENT};
            outline: none;
        }}

        /* --- PushButton: secundario por defecto (superficie + borde rgba) ---
           El CTA primario se marca con propiedad dinamica variant="primary". */
        QPushButton {{
            background: {cls.SURFACE_ELEVATED};
            border: 1px solid {cls.BORDER_STRONG};
            border-radius: {cls.RADIUS_MD}px;
            padding: 8px 16px;
            color: {cls.TEXT};
            font-weight: {cls.FONT_WEIGHT_MEDIUM};
        }}
        QPushButton:hover {{
            background: {cls.SURFACE_HOVER};
            border-color: {cls.TEXT_DIM};
        }}
        QPushButton:pressed {{
            background: {cls.SURFACE};
        }}
        QPushButton:disabled {{
            background: {cls.SURFACE};
            color: {cls.TEXT_DIM};
            border-color: {cls.BORDER_SOLID};
        }}
        QPushButton:focus-visible {{
            border: 1px solid {cls.BORDER_FOCUS};
            outline: none;
        }}
        QPushButton[variant="primary"] {{
            background: {cls.ACCENT};
            color: {cls.TEXT_ON_ACCENT};
            border: none;
            font-weight: {cls.FONT_WEIGHT_SEMIBOLD};
        }}
        QPushButton[variant="primary"]:hover {{
            background: {cls.ACCENT_HOVER};
        }}
        QPushButton[variant="primary"]:pressed {{
            background: {cls.ACCENT_PRESSED};
        }}
        QPushButton[variant="danger"] {{
            color: {cls.DANGER};
        }}
        QPushButton[variant="danger"]:hover {{
            border-color: {cls.DANGER};
        }}

        /* --- LineEdit (inset) --- */
        QLineEdit {{
            background: {cls.CONTROL_BG};
            border: 1px solid {cls.CONTROL_BORDER};
            border-radius: {cls.RADIUS_MD}px;
            padding: 6px 10px;
            color: {cls.TEXT};
        }}
        QLineEdit:focus {{
            border-color: {cls.BORDER_FOCUS};
        }}

        /* --- Slider --- */
        QSlider::groove:horizontal {{
            height: {cls.SLIDER_HEIGHT}px;
            background: {cls.SURFACE_ELEVATED};
            border-radius: {cls.SLIDER_HEIGHT // 2}px;
        }}
        QSlider::handle:horizontal {{
            width: {cls.SLIDER_HANDLE}px;
            height: {cls.SLIDER_HANDLE}px;
            margin: -{(cls.SLIDER_HANDLE - cls.SLIDER_HEIGHT) // 2}px 0;
            background: {cls.TEXT};
            border-radius: {cls.SLIDER_HANDLE // 2}px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {cls.ACCENT};
        }}
        QSlider::sub-page:horizontal {{
            background: {cls.ACCENT};
            border-radius: {cls.SLIDER_HEIGHT // 2}px;
        }}

        /* --- CheckBox --- */
        QCheckBox {{
            spacing: 8px;
            color: {cls.TEXT_SECONDARY};
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border-radius: 3px;
            border: 1px solid {cls.BORDER_STRONG};
            background: {cls.CONTROL_BG};
        }}
        QCheckBox::indicator:checked {{
            background: {cls.ACCENT};
            border-color: {cls.ACCENT};
        }}
        QCheckBox::indicator:hover {{
            border-color: {cls.TEXT_DIM};
        }}

        /* --- ToolTip --- */
        QToolTip {{
            background: {cls.SURFACE_ELEVATED};
            color: {cls.TEXT};
            border: 1px solid {cls.BORDER_STRONG};
            border-radius: {cls.RADIUS_SM}px;
            padding: 4px 8px;
            font-size: {cls.FONT_SIZE_SM}px;
        }}

        /* --- Label --- */
        QLabel {{
            background: transparent;
        }}

        /* --- Card (superficie elevada; objectName para vivir al cambio de tema) --- */
        QFrame#card {{
            background: {cls.SURFACE};
            border: 1px solid {cls.BORDER_SOLID};
            border-radius: {cls.RADIUS_LG}px;
        }}
        """


def accent_subtle_color(alpha: int = 26) -> QColor:
    """QColor del acento con alfa para pintores (Qt hex-9 es #AARRGGBB, no usar).

    26/255 ≈ 10%: el mismo tinte que ACCENT_SUBTLE en QSS."""
    from PySide6.QtGui import QColor

    color = QColor(Theme.ACCENT)
    color.setAlpha(alpha)
    return color


def numeric_font(point_size: int) -> QFont:
    """QFont con numeros tabulares (tnum) para valores dinamicos (dB, Hz, %).

    Evita el baile de layout cuando el valor cambia cada frame."""
    from PySide6.QtGui import QFont

    font = QFont(Theme.FONT_FAMILY, point_size)
    with contextlib.suppress(ValueError, AttributeError, TypeError):
        font.setFeature("tnum", 1)
    return font


# Paleta inicial
Theme.set_mode("dark")
