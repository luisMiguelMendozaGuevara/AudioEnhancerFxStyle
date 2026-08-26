"""Localización: diccionarios de traducción y detección de idioma.

Codigo es->en: el nombre de la clave es el texto en español y el valor la
traducción al inglés. Para idiomas distintos del español se usa la versión en
inglés (respaldo).
"""

import ctypes
import locale
import logging
import sys

logger = logging.getLogger("audio_enhancer.i18n")

# Localización: inglés es el idioma de respaldo.
TRANSLATIONS = {
    "Audio Enhancer": "Audio Enhancer",
    "Procesamiento del audio del sistema vía WASAPI loopback": "System audio processing via WASAPI loopback",
    "Dispositivos y ruteo": "Devices and routing",
    "Captura (loopback):": "Capture (loopback):",
    "Salida (física):": "Output (physical):",
    "Actualizar dispositivos": "Refresh devices",
    "Efectos": "Effects",
    "Configuración:": "Preset:",
    "A: Efectos ON": "A: Effects ON",
    "B: Directo (OFF)": "B: Direct (OFF)",
    "Nuevo preset:": "New preset:",
    "nombre del preset": "preset name",
    "Guardar": "Save",
    "Borrar": "Delete",
    "Volumen": "Volume",
    "Bass Boost (dB)": "Bass Boost (dB)",
    "Treble Boost (dB)": "Treble Boost (dB)",
    "Limitador suave": "Soft limiter",
    "Compresor RMS": "RMS compressor",
    "Nivel:": "Level:",
    "Ecualizador (9 bandas)": "Equalizer (9 bands)",
    "Analizador de espectro": "Spectrum analyzer",
    "Iniciar audio del sistema": "Start system audio",
    "Detener audio del sistema": "Stop system audio",
    "Instalar loopback propio (VB-CABLE)": "Install local loopback (VB-CABLE)",
    "Restablecer": "Reset",
    "Iniciar con Windows": "Start with Windows",
    "Listo. Configura el ruteo y pulsa Iniciar.": "Ready. Configure routing and press Start.",
    "Procesamiento detenido": "Processing stopped",
    "Mostrar / Ocultar": "Show / Hide",
    "Iniciar / Detener": "Start / Stop",
    "Salir": "Exit",
    "Dispositivos actualizados.": "Devices refreshed.",
    "Detectando dispositivos...": "Detecting devices...",
    "Selecciona una fuente de captura y una salida física.": "Select a capture source and a physical output.",
    "Revisa el ruteo: no captures y reproduzcas el mismo dispositivo.": "Check the routing: don't capture and play "
    "through the same device.",
    "A/B: audio directo sin efectos (B)": "A/B: direct audio without effects (B)",
    "A/B: audio procesado con efectos (A)": "A/B: processed audio with effects (A)",
    "Limitador suave: %s": "Soft limiter: %s",
    "Compresor RMS: %s": "RMS compressor: %s",
    "Configuración aplicada: %s": "Preset applied: %s",
    "Escribe un nombre para el preset personalizado.": "Type a name for the custom preset.",
    "Preset personalizado guardado: %s": "Custom preset saved: %s",
    "Selecciona un preset personalizado para borrarlo.": "Select a custom preset to delete.",
    "Preset personalizado borrado: %s": "Custom preset deleted: %s",
    "Controles restablecidos a plano": "Controls reset to flat",
    "Procesando en segundo plano (icono en bandeja).": "Processing in background (tray icon).",
    "Se abrió la carpeta con el instalador de VB-CABLE.": "Opened the folder with the VB-CABLE installer.",
    "Carpeta del instalador VB-CABLE no encontrada.": "VB-CABLE installer folder not found.",
    "Activo (ring buffer): %s → %s": "Active (ring buffer): %s -> %s",
    "No se pudo iniciar el loopback: %s": "Could not start the loopback: %s",
    "Activando salida física...": "Starting physical output...",
    "Inicio con Windows: activado": "Start with Windows: enabled",
    "Inicio con Windows: fallo al configurar": "Start with Windows: failed to configure",
    "⚠  ECO: capturas y reproduces el mismo dispositivo (A → A). "
    "Selecciona como captura el cable virtual donde suenan las apps "
    "(p. ej. CABLE Input de VB-Audio) y como salida la física.": "⚠  ECHO: you capture and play through the "
    "same device (A -> A). Pick the virtual cable where the apps play as capture "
    "(e.g. VB-Audio CABLE Input) and a physical device as output.",
    "⚠  La salida es virtual (cable). Reproduce en la salida FÍSICA "
    "(parlantes reales) para no realimentar el cable.": "⚠  The output is virtual (cable). Use a PHYSICAL output "
    "(real speakers) to avoid feeding the cable back.",
    "⚠  Estás capturando el loopback de FxSound (otra app). Si no percibes "
    "efecto o hay conflicto, instala VB-CABLE y captura 'CABLE Input'.": "⚠  You are capturing the FxSound loopback "
    "(another app). If you hear no effect or there is a conflict, install "
    "VB-CABLE and capture 'CABLE Input'.",
    "✔  Ruteo correcto: capturas tu cable virtual y solo la salida física "
    "reproduce el audio procesado. Cierra FxSound para no duplicar el efecto.": "✔  Correct routing: you capture your "
    "virtual cable and only the physical output plays processed audio. "
    "Close FxSound to avoid a doubled effect.",
    "Info: capturas un parlante físico. Asegúrate de que sea el dispositivo "
    "donde suenan las apps y que la salida sea otro distinto.": "Info: you are capturing a physical speaker. "
    "Make sure it is the device where your apps play and that the output is a different one.",
    "Preparando interfaz…": "Preparing interface…",
    "No se pudieron detectar dispositivos: %s": "Could not detect devices: %s",
    "Dispositivos listos.": "Devices ready.",
    # ---------- Nueva UI (sidebar, paginas, widgets) ----------
    "Inicio": "Home",
    "Ecualizador": "Equalizer",
    "Audio": "Audio",
    "Presets": "Presets",
    "Config": "Settings",
    "Iniciar audio": "Start audio",
    "Detener audio": "Stop audio",
    "Preset": "Preset",
    "Detenido": "Stopped",
    "ACTIVO": "ACTIVE",
    "ESPECTRO": "SPECTRUM",
    "ENTRADA": "INPUT",
    "SALIDA": "OUTPUT",
    "ENTRADA (loopback)": "INPUT (loopback)",
    "SALIDA (física)": "OUTPUT (physical)",
    "RUTEO": "ROUTING",
    "DETALLES": "DETAILS",
    "Sample Rate": "Frecuencia de muestreo",
    "Buffer": "Buffer",
    "Latency": "Latencia",
    "Status": "Estado",
    "Restablecer todo": "Reset all",
    "Selecciona una fuente y una salida.": "Select a source and an output.",
    "ECO: capturas y reproduces el mismo dispositivo.": "ECHO: you capture and play the same device.",
    "La salida es virtual. Usa salida fisica.": "The output is virtual. Use a physical output.",
    "Ruteo correcto: cable virtual -> salida fisica.": "Correct routing: virtual cable -> physical output.",
    "Info: capturas un parlante fisico.": "Info: capturing a physical speaker.",
    "GUARDAR PRESET": "SAVE PRESET",
    "Importar": "Import",
    "Exportar": "Export",
    "INCLUIDOS": "INCLUDED",
    "PERSONALIZADOS": "CUSTOM",
    "Eliminar": "Delete",
    "Preset eliminado: %s": "Preset deleted: %s",
    "Escribe un nombre para el preset.": "Write a name for the preset.",
    "IDIOMA": "LANGUAGE",
    "APARIENCIA": "APPEARANCE",
    "COMPORTAMIENTO": "BEHAVIOR",
    "Oscuro": "Dark",
    "Blanco": "Light",
    "Minimizar a bandeja al cerrar": "Minimize to tray on close",
    "Auto-iniciar audio al abrir": "Auto-start audio on launch",
    "Notificaciones": "Notifications",
    "Idioma guardado. Se aplicara ahora.": "Language saved. Applied now.",
    "Revisa el ruteo.": "Check the routing.",
    "No se pudo iniciar: %s": "Could not start: %s",
    "Activando salida...": "Activating output...",
    "Activo (ring buffer): %s -> %s": "Active (ring buffer): %s -> %s",
    "Procesando en segundo plano.": "Processing in the background.",
    "Procesando": "Processing",
    "Inicio con Windows: fallo": "Start with Windows: failed",
    "Refuerzo de graves (dB)": "Bass boost (dB)",
    "Refuerzo de agudos (dB)": "Treble boost (dB)",
}

# Guía de instalación de VB-CABLE (messagebox). Se define como constante y se
# registra en TRANSLATIONS para que la clave usada desde la UI no pueda divergir
# del literal de este módulo.
CABLE_GUIDE = (
    "Para un loopback propio (sin el APO de FxSound) hace falta el driver "
    "virtual VB-CABLE.\n\n"
    "1) Se abrió la carpeta con el instalador descargado.\n"
    "2) Ejecuta VBCABLE_Setup_x64.exe COMO ADMINISTRADOR "
    "(clic derecho > Ejecutar como administrador).\n"
    "3) Pulsa 'Install Driver' y espera el mensaje de éxito.\n"
    "4) Reinicia Windows.\n"
    "5) En Sonido > Salida, pon 'CABLE Input (VB-Audio Virtual Cable)' "
    "como dispositivo predeterminado.\n\n"
    "Después, esta app capturará 'CABLE Input' y solo la salida física sonará. "
    "No necesita FxSound."
)

CABLE_GUIDE_EN = (
    "For your own loopback (without FxSound's APO) you need the VB-CABLE "
    "virtual driver.\n\n"
    "1) The folder with the downloaded installer has been opened.\n"
    "2) Run VBCABLE_Setup_x64.exe AS ADMINISTRATOR "
    "(right click > Run as administrator).\n"
    "3) Press 'Install Driver' and wait for the success message.\n"
    "4) Restart Windows.\n"
    "5) In Sound > Output, set 'CABLE Input (VB-Audio Virtual Cable)' as the "
    "default device.\n\n"
    "After that, this app will capture 'CABLE Input' and only the physical "
    "output will play. FxSound is not needed."
)

TRANSLATIONS[CABLE_GUIDE] = CABLE_GUIDE_EN
TRANSLATIONS["Loopback propio (VB-CABLE)"] = "Own loopback (VB-CABLE)"

# Descripciones mostradas en los tooltips de la interfaz.
EXPLAIN = {
    "volumen": "Ganancia final del audio procesado. 1.0x mantiene el nivel; "
    "valores mayores aumentan el volumen y pueden activar el limitador. "
    "Úsalo con moderación para evitar saturación y fatiga auditiva.",
    "bass": "Refuerzo tipo shelf de graves, aproximadamente por debajo de 150 Hz. "
    "Aporta peso a bombos, bajos y explosiones. Si retumba o distorsiona, "
    "reduce este control o el volumen.",
    "treble": "Refuerzo tipo shelf de agudos, aproximadamente por encima de 6 kHz. "
    "Aporta claridad a voces, platos y detalles. Demasiado puede producir "
    "sibilancias o un sonido áspero.",
    "eq": "Cada banda aumenta o reduce una zona de frecuencias alrededor de su "
    "frecuencia central. Los valores positivos (+dB) realzan y los negativos "
    "(-dB) atenúan. Q=1.4 produce una curva de anchura media; mueve poco a "
    "poco los controles para evitar cambios bruscos.",
    "limiter": "Limitador suave: evita que los picos superen aproximadamente "
    "-0.4 dBFS. En lugar de cortar la señal de golpe, reduce la ganancia "
    "progresivamente para disminuir clipping y distorsión. Puede reducir "
    "algo la dinámica si el nivel es muy alto.",
    "compressor": "Compresor RMS: calcula el nivel medio de cada bloque de audio y "
    "reduce gradualmente las partes demasiado fuertes. Suaviza la "
    "dinámica y hace más uniforme el volumen entre voces y música; "
    "no es un aumento de volumen automático y un exceso puede sonar "
    "aplastado.",
}

EXPLAIN_EN = {
    "volumen": "Final gain of the processed audio. 1.0x keeps the level; higher values "
    "increase volume and may engage the limiter.",
    "bass": "Low-shelf boost below approximately 150 Hz. Adds weight to kick drums and "
    "bass; reduce it if the sound becomes boomy.",
    "treble": "High-shelf boost above approximately 6 kHz. Adds clarity and detail; "
    "too much can sound harsh or sibilant.",
    "eq": "Each band boosts (+dB) or cuts (-dB) a frequency range around its center "
    "frequency. Q=1.4 gives a medium-width curve.",
    "limiter": "Soft limiter: gently reduces peaks near 0 dBFS to prevent clipping and "
    "distortion instead of cutting them abruptly.",
    "compressor": "RMS compressor: measures average loudness and gradually reduces overly "
    "loud sections, making volume more consistent.",
}

EQ_EXPLAIN = {
    60: "Subgrave y grave profundo: golpes de bombo, sub-bajo y rumble. "
    "Realzarlo da peso; reducirlo limpia vibraciones.",
    150: "Grave alto: cuerpo de bombos, bajos y voces masculinas. Demasiado produce sonido boomy o retumbante.",
    250: "Grave medio: calidez y cuerpo. Reducirlo puede quitar barro; aumentarlo puede engrosar guitarras y voces.",
    500: "Medio bajo: cuerpo de instrumentos y voces. Atenuarlo ayuda a limpiar una mezcla congestionada.",
    1000: "Medio central: presencia general de voces, guitarras y teclados. Cambios aquí son muy perceptibles.",
    2000: "Medio alto: inteligibilidad y ataque. Realzarlo mejora definición, "
    "pero puede volver el sonido nasal o agresivo.",
    4000: "Presencia: detalle de consonantes, guitarras y percusión. Demasiado puede sonar duro o fatigante.",
    8000: "Agudo: brillo, platos y aire inicial. Útil para claridad; exceso aumenta sibilancias y ruido.",
    12000: "Aire: brillo fino y sensación de apertura. Realza detalles sutiles; reducirlo suaviza grabaciones ásperas.",
}

EQ_EXPLAIN_EN = {
    60: "Deep sub-bass: kick drums, sub-bass and rumble. Boost for weight; cut to clean up vibration.",
    150: "Upper bass: body of kick drums, bass and male voices. Too much sounds boomy.",
    250: "Low mids: warmth and body. Cutting removes mud; boosting thickens guitars and voices.",
    500: "Lower mids: body of instruments and voices. Cutting helps clean a crowded mix.",
    1000: "Center mids: presence of voices, guitars and keyboards. Very audible changes here.",
    2000: "Upper mids: intelligibility and attack. Boosting improves definition but can sound nasal.",
    4000: "Presence: detail of consonants, guitars and percussion. Too much sounds harsh.",
    8000: "Highs: brilliance, cymbals and air. Useful for clarity; too much adds sibilance.",
    12000: "Air: fine brilliance and sense of openness. Flattering subtle details.",
}

# Configuraciones predeterminadas: nombre -> (volumen, bass, treble, eq_gains)
# EQ bandas: [60, 150, 250, 500, 1000, 2000, 4000, 8000, 12000] Hz
# Curvas pensadas para el flujo de la app: bass/treble actúan como "shelf"
# (graves < 150 Hz y agudos > 6 kHz) y el EQ de 9 bandas hace el ajuste fino.
from .constants import DEFAULT_PRESET  # noqa: E402

PRESETS = {
    DEFAULT_PRESET: (1.0, 0.0, 0.0, [0, 0, 0, 0, 0, 0, 0, 0, 0]),
    "Graves (Bass)": (1.0, 5.0, 0.5, [4, 3, 1.5, 0, 0, 0, 0, 0.5, 0.5]),
    "Música (V suave)": (1.0, 2.5, 1.5, [2, 1.5, 0, -0.5, 0, 0, 0.5, 1.5, 2]),
    "Clásica / Acústica": (1.0, 1.0, 2.0, [1, 0.5, 0.5, 0, 0, 0, 0.5, 2, 2.5]),
    "Rock": (1.0, 3.0, 1.5, [3, 2, 0.5, -0.5, -0.5, 0.5, 1, 2, 3]),
    "Electrónica": (1.0, 6.0, 1.0, [6, 5, 3, 0, 0.5, 1, 1, 1.5, 1.5]),
    "Voz / Podcast": (1.0, 0.0, 0.5, [0, 0, -0.5, 1, 3, 4.5, 3, 1, 0]),
    "Cine / Series": (1.1, 4.0, 1.5, [4, 3, 1.5, -0.5, 0, 0.5, 1, 2, 2.5]),
    "Noche (vol. baja)": (0.45, 3.0, 0.5, [3, 2, 1.5, 0.5, 0, 0, 0, 1, 1.5]),
    "Agudos (Treble)": (1.0, 0.0, 4.0, [0, 0, 0, 0, 0, 0, 1, 2.5, 3.5]),
}


def detect_system_language():
    """Devuelve 'es' si la interfaz de Windows está en español y 'en' en el resto."""
    if sys.platform == "win32":
        try:
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        except Exception:
            # Sin idioma de interfaz disponible se cae al respaldo en inglés
            logger.debug("No se pudo consultar el idioma de interfaz de Windows", exc_info=True)
            return "en"
        # Primary Language ID = bits 0-9 del LANGID; español = 0x0A
        return "es" if (lang_id & 0x3FF) == 0x0A else "en"
    # Fuera de Windows: respaldo con el locale del entorno
    try:
        lang = (locale.getlocale()[0] or "").lower()
    except Exception:
        logger.debug("No se pudo detectar el locale del sistema", exc_info=True)
        lang = ""
    return "es" if "spanish" in lang or lang.startswith("es") else "en"


def translate(text, language):
    if language == "en":
        return TRANSLATIONS.get(text, text)
    return text
