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

SMILE_PATH = (
    (320, 730),  # start
    (512, 878),  # quadratic control
    (704, 730),  # end
)
SMILE_STROKE_WIDTH = 52  # 1024-space pixels


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


def _build_horizontal_gradient(width, height, left, right, start_x=0, end_x=None):
    """Linear left→right gradient image at render resolution.

    The gradient interpolates between ``start_x`` and ``end_x`` (in pixels).
    Outside that range the color clamps to the end stops. This mirrors SVG's
    default ``objectBoundingBox`` gradient behaviour — the gradient spans the
    smile path's bounding box, not the whole canvas.
    """
    if end_x is None:
        end_x = width - 1
    gradient = Image.new("RGBA", (width, height))
    pixels = gradient.load()
    lr, lg, lb = left
    rr, rg, rb = right
    span = max(end_x - start_x, 1)
    for x in range(width):
        t = (x - start_x) / span
        t = max(0.0, min(1.0, t))
        r = round(lr * (1.0 - t) + rr * t)
        g = round(lg * (1.0 - t) + rg * t)
        b = round(lb * (1.0 - t) + rb * t)
        for y in range(height):
            pixels[x, y] = (r, g, b, 255)
    return gradient


def _smile_mask() -> Image.Image:
    """Single-channel mask of the smile stroke at render resolution."""
    mask = Image.new("L", (RENDER_SIZE, RENDER_SIZE), 0)
    draw = ImageDraw.Draw(mask)

    p0, p1, p2 = SMILE_PATH
    points = [
        (_scale(x), _scale(y))
        for x, y in _quadratic_bezier(p0, p1, p2, n=128)
    ]
    draw.line(points, fill=255, width=int(_scale(SMILE_STROKE_WIDTH)), joint="curve")

    # Round caps: filled circles at each endpoint, radius = half the stroke width.
    cap_radius = _scale(SMILE_STROKE_WIDTH / 2)
    for cx, cy in (points[0], points[-1]):
        draw.ellipse(
            (cx - cap_radius, cy - cap_radius, cx + cap_radius, cy + cap_radius),
            fill=255,
        )
    return mask


def _composite_smile(image: Image.Image) -> None:
    """Paint the gradient-stroked smile onto ``image`` in place."""
    smile_left = _scale(SMILE_PATH[0][0])   # 320 in 1024-space
    smile_right = _scale(SMILE_PATH[2][0])  # 704 in 1024-space
    gradient = _build_horizontal_gradient(
        RENDER_SIZE, RENDER_SIZE, AMBER, IVORY,
        start_x=smile_left, end_x=smile_right,
    )
    mask = _smile_mask()
    image.paste(gradient, (0, 0), mask)


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

    _composite_smile(image)

    return image


def main() -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    master = render_master()
    # PNG/ICO writes are wired in later tasks; this no-op keeps the CLI runnable.
    print(f"Rendered master at {master.size}.")


if __name__ == "__main__":
    main()
