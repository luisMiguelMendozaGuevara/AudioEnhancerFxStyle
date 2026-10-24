# Sistema de Diseño — AudioEnhancerFxStyle (nueva UI)

## Temas
Dos paletas en `Theme` ("dark" carbón / "light" blanco). Cambiar con
`Theme.set_mode()` + reaplicar `Theme.stylesheet()` + `update()` en todos los
widgets (ver `NewMainWindow._on_theme_changed`). Selector: Settings → APPEARANCE.

## Reglas críticas de implementación
- Qt hex-9 = #AARRGGBB (alfa primero): NUNCA #RRGGBBAA en QColor; usar
  `accent_subtle_color(alpha)` para pintores.
- Painters custom (SidebarItem): pintar SIEMPRE el fondo base del tema antes
  de tintes translúcidos (alfa sobre superficie no inicializada sale negra).
- Cards con `setObjectName("card")` + regla QSS global `QFrame#card` (los
  estilos inline hornean colores al construir y no siguen el tema).

## Dirección
"Instrumento de rack": carbón mate de un solo tono + acento único fósforo teal
(herencia osciloscopio). El color es semántico, nunca decorativo.

## Tokens (audio_enhancer/ui/new/theme/colors.py)
- Superficies (1 tono, escalera de luminosidad): `#131316 → #19191d → #202025 → #26262c`
- Sidebar = mismo fondo que canvas (un solo espacio visual)
- Bordes: `rgba(255,255,255,.07)` estándar, `.14` fuerte; en QPainter usar
  `BORDER_SOLID` (Qt hex-9 es #AARRGGBB, NO usar #RRGGBBAA)
- Texto 4 niveles: `#ececee / #b8bac1 / #8a8c94 / #5f6067`
- Acento teal: `#3ddad7` (hover `#63e3e0`, pressed `#2bbfbc`); texto sobre
  acento = tinta oscura `#0d2120` (no blanco)
- Tinte translúcido para QPainter: `accent_subtle_color(alpha)` (26 ≈ 10%)
- Semánticos desaturados: success `#48b271`, warning `#e0b45f`, danger `#e0564d`

## Jerarquía
- Peso+opacidad > tamaño: 400 normal / 500 medium / 600 semibold / 700 bold
- Números dinámicos (dB, Hz): `numeric_font(n)` con feature `tnum`
- Cabecera discreta (14px/600/secondary); el foco de cada página es su
  instrumento (spectrum, curva EQ, meters)

## Componentes
- Botón por defecto = secundario (superficie + borde rgba); primario con
  `setProperty("variant", "primary")`; destructivo `variant="danger"`
- Inputs inset: `CONTROL_BG #141418` (más oscuro que la superficie)
- Slider: handle blanco `#ececee`, hover teal, sub-page teal
- Botones: padding 8x16, radius 8; cards radius 12

## Motion
- Fade de página 160ms OutCubic (retirar QGraphicsOpacityEffect al terminar)
- <300ms siempre; sin animación en acciones repetidas (start/stop audio)

## Espaciado
Base 4px: 4/8/12/16/24/32; margen de página 16px

## Curva del EQ (pages/equalizer.py)
- La curva y el relleno son la interpolación **PCHIP** de las ganancias por
  banda en log-frecuencia (`dsp.pchip_interp`, compartida con el DSP): pasa
  exactamente por los puntos, no ondula en S ni sobreoscila. Nada de Bézier
  decorativas: si el dibujo y el motor divergen, el gráfico miente.
- Se dibuja **de borde a borde** (20 Hz–20 kHz) con extensión plana en los
  extremos (`curve_samples()`); con cualquier nº de bandas el ancho se cubre
  entero y el relleno usa `accent_subtle_color(13)` cerrado a 0 dB.
- Cambiar el nº de bandas remapea las ganancias con la MISMA PCHIP
  (`Enhancer.set_eq_bands`); la ida y vuelta 9→31→9 conserva la forma en
  <0.2 dB. La UI manda bandas **antes** que ganancias (`_sync_ui_from_state`).
- El **Ancho (Q)** es un ajuste fino con efecto casi inaudible: vive en
  Efectos («Ancho (Q) del ecualizador», 0.2–10, sin toggle) y llega al motor
  por `state.eq_q_changed`; la página EQ solo tiene el selector de Bandas.
