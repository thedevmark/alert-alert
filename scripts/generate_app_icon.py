from __future__ import annotations

import struct
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"
IMG_DIR = STATIC_DIR / "img"


def rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = hex_color.lstrip("#")
    return (
        int(value[0:2], 16),
        int(value[2:4], 16),
        int(value[4:6], 16),
        alpha,
    )


TRANSPARENT = (0, 0, 0, 0)
BG_OUTER = rgba("#0D1521")
BG_INNER = rgba("#162334")
BOARD_BODY = rgba("#EEF4FA")
BOARD_TOP = rgba("#D7E0EA")
STRIPE_DARK = rgba("#243548")
STRIPE_TEAL = rgba("#63C6D7")
BOARD_DETAIL = rgba("#CBD6E1")
SHADOW = rgba("#02050B", 58)
HINGE = rgba("#0E1622", 34)


def over(dst: tuple[int, int, int, int], src: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    sr, sg, sb, sa = src
    dr, dg, db, da = dst
    src_a = sa / 255.0
    dst_a = da / 255.0
    out_a = src_a + (dst_a * (1.0 - src_a))
    if out_a <= 0:
        return TRANSPARENT
    out_r = (sr * src_a + dr * dst_a * (1.0 - src_a)) / out_a
    out_g = (sg * src_a + dg * dst_a * (1.0 - src_a)) / out_a
    out_b = (sb * src_a + db * dst_a * (1.0 - src_a)) / out_a
    return (
        max(0, min(255, int(round(out_r)))),
        max(0, min(255, int(round(out_g)))),
        max(0, min(255, int(round(out_b)))),
        max(0, min(255, int(round(out_a * 255.0)))),
    )


def inside_rounded_rect(x: float, y: float, left: float, top: float, width: float, height: float, radius: float) -> bool:
    center_x = left + (width / 2.0)
    center_y = top + (height / 2.0)
    qx = abs(x - center_x) - (width / 2.0) + radius
    qy = abs(y - center_y) - (height / 2.0) + radius
    if qx <= 0.0 and qy <= 0.0:
        return True
    dx = max(qx, 0.0)
    dy = max(qy, 0.0)
    return (dx * dx) + (dy * dy) <= radius * radius


def point_in_polygon(x: float, y: float, points: list[tuple[float, float]]) -> bool:
    inside = False
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        if ((y1 > y) != (y2 > y)):
            slope = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9)
            if x < (x1 + slope):
                inside = not inside
    return inside


def sample_icon(x: float, y: float) -> tuple[int, int, int, int]:
    pixel = TRANSPARENT

    if inside_rounded_rect(x, y, 0.08, 0.08, 0.84, 0.84, 0.22):
        pixel = over(pixel, BG_OUTER)
    if inside_rounded_rect(x, y, 0.11, 0.11, 0.78, 0.78, 0.18):
        pixel = over(pixel, BG_INNER)

    if inside_rounded_rect(x, y, 0.21, 0.29, 0.62, 0.21, 0.06):
        pixel = over(pixel, SHADOW)
    if inside_rounded_rect(x, y, 0.27, 0.46, 0.52, 0.28, 0.07):
        pixel = over(pixel, SHADOW)

    if inside_rounded_rect(x, y, 0.25, 0.43, 0.50, 0.27, 0.06):
        pixel = over(pixel, BOARD_BODY)

    top_rect = inside_rounded_rect(x, y, 0.19, 0.26, 0.62, 0.19, 0.055)
    if top_rect:
        pixel = over(pixel, BOARD_TOP)

    stripe_polygons = [
        (STRIPE_DARK, [(0.15, 0.26), (0.27, 0.26), (0.19, 0.45), (0.07, 0.45)]),
        (STRIPE_TEAL, [(0.31, 0.26), (0.43, 0.26), (0.35, 0.45), (0.23, 0.45)]),
        (STRIPE_DARK, [(0.47, 0.26), (0.59, 0.26), (0.51, 0.45), (0.39, 0.45)]),
        (STRIPE_TEAL, [(0.63, 0.26), (0.75, 0.26), (0.67, 0.45), (0.55, 0.45)]),
    ]
    if top_rect:
        for color, polygon in stripe_polygons:
            if point_in_polygon(x, y, polygon):
                pixel = over(pixel, color)

    if inside_rounded_rect(x, y, 0.27, 0.436, 0.46, 0.012, 0.006):
        pixel = over(pixel, HINGE)

    if inside_rounded_rect(x, y, 0.33, 0.54, 0.34, 0.032, 0.016):
        pixel = over(pixel, BOARD_DETAIL)
    if inside_rounded_rect(x, y, 0.33, 0.615, 0.20, 0.028, 0.014):
        pixel = over(pixel, BOARD_DETAIL)

    return pixel


def render_icon(size: int, samples: int | None = None) -> bytes:
    if samples is None:
        samples = 1 if size >= 512 else (3 if size <= 32 else 2)
    buffer = bytearray(size * size * 4)
    index = 0
    total_samples = samples * samples
    for py in range(size):
        for px in range(size):
            red = green = blue = alpha = 0
            for sy in range(samples):
                sample_y = (py + ((sy + 0.5) / samples)) / size
                for sx in range(samples):
                    sample_x = (px + ((sx + 0.5) / samples)) / size
                    sr, sg, sb, sa = sample_icon(sample_x, sample_y)
                    red += sr
                    green += sg
                    blue += sb
                    alpha += sa
            buffer[index:index + 4] = bytes((
                red // total_samples,
                green // total_samples,
                blue // total_samples,
                alpha // total_samples,
            ))
            index += 4
    return bytes(buffer)


def png_bytes(width: int, height: int, rgba_bytes: bytes) -> bytes:
    rows = []
    stride = width * 4
    for offset in range(0, len(rgba_bytes), stride):
        rows.append(b"\x00" + rgba_bytes[offset:offset + stride])
    raw = b"".join(rows)
    compressed = zlib.compress(raw, 9)

    def chunk(name: bytes, data: bytes) -> bytes:
        payload = name + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")


def write_png(path: Path, size: int) -> bytes:
    image = render_icon(size)
    payload = png_bytes(size, size, image)
    path.write_bytes(payload)
    return payload


def write_ico(path: Path, sizes: list[int]) -> None:
    png_payloads = [(size, png_bytes(size, size, render_icon(size))) for size in sizes]
    header_size = 6 + (16 * len(png_payloads))
    offset = header_size
    entries = []
    image_blobs = []

    for size, payload in png_payloads:
        width_byte = 0 if size >= 256 else size
        height_byte = 0 if size >= 256 else size
        entries.append(struct.pack(
            "<BBBBHHII",
            width_byte,
            height_byte,
            0,
            0,
            1,
            32,
            len(payload),
            offset,
        ))
        image_blobs.append(payload)
        offset += len(payload)

    icon_header = struct.pack("<HHH", 0, 1, len(png_payloads))
    path.write_bytes(icon_header + b"".join(entries) + b"".join(image_blobs))


def main() -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    write_png(IMG_DIR / "logo.png", 1024)
    write_ico(STATIC_DIR / "favicon.ico", [16, 24, 32, 48, 64, 128, 256])
    print("Wrote static/img/logo.png and static/favicon.ico")


if __name__ == "__main__":
    main()
