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


def _ellipse(draw: ImageDraw.ImageDraw, cx, cy, rx, ry, fill):
    """Pillow ellipse wrapper that scales 1024-space inputs."""
    draw.ellipse(
        (
            _scale(cx - rx), _scale(cy - ry),
            _scale(cx + rx), _scale(cy + ry),
        ),
        fill=fill + (255,),
    )


def _quadratic_bezier(p0, p1, p2, n: int = 32):
    """Sample a quadratic Bezier into ``n + 1`` (x, y) points in 1024-space."""
    points = []
    for i in range(n + 1):
        t = i / n
        u = 1.0 - t
        x = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
        y = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
        points.append((x, y))
    return points


def _bang_stem_polygon(side: str):
    """Polygon points for one Inconsolata-tapered bang stem.

    SVG path (left stem):
        M 296 180 Q 348 172 400 180 L 384 440 Q 348 434 312 440 Z
    Right stem mirrors at x = 512.
    """
    if side == "left":
        top = _quadratic_bezier((296, 180), (348, 172), (400, 180))
        bottom = _quadratic_bezier((384, 440), (348, 434), (312, 440))
    elif side == "right":
        top = _quadratic_bezier((624, 180), (676, 172), (728, 180))
        bottom = _quadratic_bezier((712, 440), (676, 434), (640, 440))
    else:
        raise ValueError(f"side must be 'left' or 'right', got {side!r}")
    # Polygon: top L→R, then bottom L→R-of-path (which is R→L spatially).
    return [(_scale(x), _scale(y)) for x, y in top + bottom]


def render_master() -> Image.Image:
    """Render the full icon at supersampled resolution."""
    image = Image.new("RGBA", (RENDER_SIZE, RENDER_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    _rounded_rect(draw, (0, 0, 1024, 1024), 224, OUTER)
    _rounded_rect(draw, (40, 40, 984, 984), 200, INNER)

    draw.polygon(_bang_stem_polygon("left"), fill=AMBER + (255,))
    draw.polygon(_bang_stem_polygon("right"), fill=IVORY + (255,))

    _ellipse(draw, 348, 522, 60, 58, AMBER)
    _ellipse(draw, 676, 522, 60, 58, IVORY)

    return image


def main() -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    master = render_master()
    # PNG/ICO writes are wired in later tasks; this no-op keeps the CLI runnable.
    print(f"Rendered master at {master.size}.")


if __name__ == "__main__":
    main()
