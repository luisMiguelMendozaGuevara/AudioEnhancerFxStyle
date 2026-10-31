"""Importador de perfiles AutoEQ: leer la curva y llevarla a nuestras bandas.

Decisión de diseño que resuelve el tema legal (ver `docs/diseno-v1.8.md` §C):
**no se empaqueta ningún perfil**. La app solo lee archivos que aporte el
usuario. AutoEQ es MIT, pero las mediciones crudas que procesa vienen de
terceros (oratory1990, crinacle, Rtings…) y su licencia puede no ser MIT; si no
redistribuimos nada, el problema desaparece sin perder la función.

Se aceptan los dos formatos que publica AutoEQ:

- ``ParametricEQ.txt``: una línea por filtro, tipo ``Filter 1: ON PK Fc 105 Hz
  Gain 5.5 dB Q 0.70`` (también ``LS``/``HS``, que se aproximan).
- ``GraphicEQ.txt``: ``Fc1 gain1; Fc2 gain2; …`` con hasta 127 puntos.

Lo que **no** se hace, a propósito: no se inventan filtros que el archivo no
tiene ni se extrapola fuera de la banda. Si el archivo trae 31 bandas y la app
tiene 9, se interpola y **se avisa**: la curva resultante no es idéntica.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

#: Ganancia máxima admitida por banda (la app no llega tan lejos en el EQ).
GANANCIA_MAX_DB = 12.0
#: Frecuencia mínima y máxima que tiene sentido pedir.
FREC_MIN_HZ = 20.0
FREC_MAX_HZ = 20000.0

#: ``Filter 3: ON PK Fc 2500 Hz Gain -3.0 dB Q 1.41``
_RE_PARAMETRICO = re.compile(
    r"Filter\s*\d+\s*:\s*(?P<estado>ON|OFF)\s+(?P<tipo>PK|LS|HS|LP|HP|BP)\s+"
    r"Fc\s+(?P<frec>[\d.]+)\s*Hz\s+Gain\s+(?P<ganancia>[-+]?[\d.]+)\s*dB"
    r"(?:\s+Q\s+(?P<q>[\d.]+))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Filtro:
    """Un filtro del archivo: frecuencia, ganancia y tipo."""

    frecuencia: float
    ganancia_db: float
    tipo: str = "PK"
    q: float = 1.0

    @property
    def activo(self) -> bool:
        """Solo cuentan los filtros con ganancia: uno a 0 dB no hace nada."""
        return abs(self.ganancia_db) > 1e-9


@dataclass
class Perfil:
    """Curva importada, con su procedencia para poder decir de dónde salió."""

    nombre: str
    puntos: list[tuple[float, float]] = field(default_factory=list)
    fuente: str = ""
    formato: str = ""

    @property
    def banda(self) -> tuple[float, float]:
        """Rango de frecuencias que cubre el archivo."""
        if not self.puntos:
            return (0.0, 0.0)
        frecuencias = [f for f, _ in self.puntos]
        return (min(frecuencias), max(frecuencias))

    def ganancia_en(self, frecuencia: float) -> float:
        """Ganancia interpolada (log-frecuencia) en una frecuencia concreta.

        Fuera del rango del archivo se devuelve el valor del extremo: extrapolar
        hacia arriba inventaría un realce de agudos que el perfil no pide.
        """
        if not self.puntos:
            return 0.0
        frecuencias = np.array([f for f, _ in self.puntos], dtype=float)
        ganancias = np.array([g for _, g in self.puntos], dtype=float)
        orden = np.argsort(frecuencias)
        frecuencias, ganancias = frecuencias[orden], ganancias[orden]
        valor = float(np.interp(np.log10(max(frecuencia, FREC_MIN_HZ)), np.log10(frecuencias), ganancias))
        return float(np.clip(valor, -GANANCIA_MAX_DB, GANANCIA_MAX_DB))


def parsear_parametrico(texto: str, nombre: str = "AutoEQ") -> Perfil:
    """Lee un ``ParametricEQ.txt`` y lo deja como curva de puntos.

    Los filtros ``LP``/``HP``/``BP`` no son de ecualización (son cortes) y se
    ignoran: aplicarlos como banda sería inventar.
    """
    filtros: list[Filtro] = []
    for linea in texto.splitlines():
        coincidencia = _RE_PARAMETRICO.search(linea)
        if not coincidencia:
            continue
        if coincidencia.group("estado").upper() != "ON":
            continue
        tipo = coincidencia.group("tipo").upper()
        if tipo not in ("PK", "LS", "HS"):
            continue
        frecuencia = float(coincidencia.group("frec"))
        if not FREC_MIN_HZ <= frecuencia <= FREC_MAX_HZ:
            continue
        q = float(coincidencia.group("q") or 1.0)
        filtros.append(
            Filtro(
                frecuencia=frecuencia,
                ganancia_db=float(coincidencia.group("ganancia")),
                tipo=tipo,
                q=q,
            )
        )
    return Perfil(
        nombre=nombre,
        puntos=[(f.frecuencia, f.ganancia_db) for f in filtros if f.activo],
        formato="parametrico",
    )


def parsear_grafico(texto: str, nombre: str = "AutoEQ") -> Perfil:
    """Lee un ``GraphicEQ.txt`` (``f1 g1; f2 g2; …``) y lo deja como curva."""
    puntos: list[tuple[float, float]] = []
    for trozo in re.split(r"[;\n]", texto):
        partes = trozo.strip().split()
        if len(partes) < 2:
            continue
        try:
            frecuencia, ganancia = float(partes[0]), float(partes[1])
        except ValueError:
            continue
        if not FREC_MIN_HZ <= frecuencia <= FREC_MAX_HZ:
            continue
        puntos.append((frecuencia, ganancia))
    return Perfil(nombre=nombre, puntos=puntos, formato="grafico")


def detectar_formato(texto: str) -> str:
    """Devuelve ``'parametrico'`` o ``'grafico'`` según el contenido del archivo."""
    if _RE_PARAMETRICO.search(texto):
        return "parametrico"
    if re.search(r"\d+(?:\.\d+)?\s+[-+]?\d+(?:\.\d+)", texto):
        return "grafico"
    return ""


def leer_archivo(ruta: str | Path) -> Perfil:
    """Lee un perfil de AutoEQ del disco y anota su procedencia.

    Lanza ``ValueError`` con un mensaje legible si el formato no se reconoce:
    un archivo que no se entiende no debe dejar el EQ a medio aplicar.
    """
    camino = Path(ruta)
    try:
        texto = camino.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"No se pudo leer el archivo: {exc}") from exc
    formato = detectar_formato(texto)
    if formato == "parametrico":
        perfil = parsear_parametrico(texto, nombre=camino.stem)
    elif formato == "grafico":
        perfil = parsear_grafico(texto, nombre=camino.stem)
    else:
        raise ValueError("El archivo no parece un perfil de AutoEQ (se esperaba ParametricEQ.txt o GraphicEQ.txt)")
    if not perfil.puntos:
        raise ValueError("El perfil no contiene filtros activos que aplicar")
    perfil.fuente = str(camino)
    return perfil


def curva_en_bandas(perfil: Perfil, bandas: list[float] | tuple[float, ...]) -> list[float]:
    """Ganancias para nuestras bandas, interpolando la curva del perfil.

    Se devuelve la ganancia **absoluta** que pide el perfil en cada banda, no
    una diferencia. El recorte a ±12 dB lo aplica ``Perfil.ganancia_en``.
    """
    if not bandas:
        raise ValueError("No hay bandas donde aplicar el perfil")
    return [perfil.ganancia_en(float(f)) for f in bandas]


@dataclass
class ResultadoImportacion:
    """Lo que hay que enseñarle al usuario antes de tocar su EQ."""

    perfil: Perfil
    ganancias: list[float]
    avisos: list[str] = field(default_factory=list)

    @property
    def ganancia_maxima(self) -> float:
        return max((abs(g) for g in self.ganancias), default=0.0)

    def resumen(self) -> str:
        """Texto corto para el diálogo de confirmación."""
        lineas = [
            f"{self.perfil.nombre} ({self.perfil.formato}, {len(self.perfil.puntos)} puntos)",
            f"Ganancia máxima aplicada: {self.ganancia_maxima:.1f} dB",
        ]
        lineas.extend(self.avisos)
        return "\n".join(lineas)


def preparar_importacion(perfil: Perfil, bandas: list[float] | tuple[float, ...]) -> ResultadoImportacion:
    """Calcula las ganancias para nuestras bandas y **avisa** de lo que se pierde."""
    ganancias = curva_en_bandas(perfil, bandas)
    avisos: list[str] = []
    if len(perfil.puntos) > len(bandas):
        avisos.append(
            f"El perfil tiene {len(perfil.puntos)} puntos y el EQ {len(bandas)} bandas: "
            "la curva resultante es una aproximación."
        )
    minimo, maximo = perfil.banda
    if minimo and bandas[0] < minimo:
        avisos.append(f"El perfil empieza en {minimo:.0f} Hz; por debajo se usa el valor del extremo.")
    if maximo and bandas[-1] > maximo:
        avisos.append(f"El perfil acaba en {maximo:.0f} Hz; por encima se usa el valor del extremo.")
    if any(abs(g) >= GANANCIA_MAX_DB - 1e-9 for g in ganancias):
        avisos.append(f"Alguna banda se recortó a ±{GANANCIA_MAX_DB:.0f} dB.")
    return ResultadoImportacion(perfil=perfil, ganancias=ganancias, avisos=avisos)
