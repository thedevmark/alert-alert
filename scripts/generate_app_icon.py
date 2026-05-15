"""Render the Alert! Alert! app icon (PNG + multi-res ICO) from the SVG geometry.

Single source of truth: ``static/img/app-icon.svg``. This script reproduces
that geometry pixel-perfectly using Pillow primitives, supersampled 4× and
downsampled with LANCZOS for clean anti-aliasing.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"
IMG_DIR = STATIC_DIR / "img"

TARGET_SIZE = 1024
SUPERSAMPLE = 4
RENDER_SIZE = TARGET_SIZE * SUPERSAMPLE

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]

# Colors (RGB tuples, alpha appended where needed)
OUTER = (13, 21, 33)       # #0D1521
INNER = (22, 35, 52)       # #162334
AMBER = (255, 181, 71)     # #FFB547
IVORY = (238, 244, 250)    # #EEF4FA


def _scale(value: float) -> float:
    """Scale a 1024-space coordinate into render-space."""
    return value * SUPERSAMPLE


def _rounded_rect(draw: ImageDraw.ImageDraw, box, radius, fill):
    """Pillow rounded_rectangle wrapper that scales 1024-space inputs."""
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(
        (_scale(x0), _scale(y0), _scale(x1), _scale(y1)),
        radius=_scale(radius),
        fill=fill + (255,),
    )


def render_master() -> Image.Image:
    """Render the full icon at supersampled resolution."""
    image = Image.new("RGBA", (RENDER_SIZE, RENDER_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Canvas: outer rounded square + inner panel
    _rounded_rect(draw, (0, 0, 1024, 1024), 224, OUTER)
    _rounded_rect(draw, (40, 40, 984, 984), 200, INNER)

    return image


def main() -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    master = render_master()
    # PNG/ICO writes are wired in later tasks; this no-op keeps the CLI runnable.
    print(f"Rendered master at {master.size}.")


if __name__ == "__main__":
    main()
