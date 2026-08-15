# -*- coding: utf-8 -*-
"""Genera los iconos de la aplicación a partir de una imagen PNG."""
import argparse
import os
from PIL import Image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SOURCE = os.path.join(BASE_DIR, "assets", "source_icon.png")
OUTPUT_DIR = os.path.join(BASE_DIR, "assets")


def main():
    parser = argparse.ArgumentParser(description="Genera app.ico y los iconos del tray")
    parser.add_argument(
        "source",
        nargs="?",
        default=DEFAULT_SOURCE,
        help="PNG de origen (por defecto: assets/source_icon.png)",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.source):
        raise SystemExit(
            "No se encontró la imagen de origen: %s\n"
            "Copia un PNG a assets/source_icon.png o indica su ruta como argumento."
            % args.source
        )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    img = Image.open(args.source).convert("RGBA")

    # Recorte centrado para normalizar imágenes no cuadradas.
    w, h = img.size
    side = min(w, h)
    if abs(w - h) > 2:
        img = img.crop(((w - side) // 2, (h - side) // 2,
                        (w + side) // 2, (h + side) // 2))

    sizes = [16, 24, 32, 48, 64, 128, 256]
    img.save(os.path.join(OUTPUT_DIR, "app.ico"), sizes=[(s, s) for s in sizes])
    img.resize((64, 64), Image.Resampling.LANCZOS).save(
        os.path.join(OUTPUT_DIR, "tray.png")
    )
    img.resize((256, 256), Image.Resampling.LANCZOS).save(
        os.path.join(OUTPUT_DIR, "app_icon.png")
    )
    print("Iconos generados en:", OUTPUT_DIR)


if __name__ == "__main__":
    main()
