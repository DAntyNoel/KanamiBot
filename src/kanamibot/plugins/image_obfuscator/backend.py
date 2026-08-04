"""小番茄图片混淆后端。

This module intentionally has no NoneBot imports.  ``encode`` and ``decode``
are the public API used by both the QQ adapter and the CLI.
"""
from __future__ import annotations

import base64
import io
import math
from pathlib import Path
from typing import Iterable

from PIL import Image

MAX_PIXELS = 8_000_000
MAX_SIDE = 4_000
GOLDEN_RATIO_FRACTION = (math.sqrt(5.0) - 1.0) / 2.0


def _sgn(value: int) -> int:
    return -1 if value < 0 else 1 if value > 0 else 0


def _generate_2d(x: int, y: int, ax: int, ay: int, bx: int, by: int):
    """Yield a generalized Hilbert/Gilbert curve for an arbitrary rectangle."""
    width = abs(ax + ay)
    height = abs(bx + by)
    dax, day = _sgn(ax), _sgn(ay)
    dbx, dby = _sgn(bx), _sgn(by)

    if height == 1:
        for _ in range(width):
            yield x, y
            x, y = x + dax, y + day
        return
    if width == 1:
        for _ in range(height):
            yield x, y
            x, y = x + dbx, y + dby
        return

    ax2, ay2 = ax // 2, ay // 2
    bx2, by2 = bx // 2, by // 2
    width2 = abs(ax2 + ay2)
    height2 = abs(bx2 + by2)

    if 2 * width > 3 * height:
        if width2 % 2 and width > 2:
            ax2, ay2 = ax2 + dax, ay2 + day
        yield from _generate_2d(x, y, ax2, ay2, bx, by)
        yield from _generate_2d(x + ax2, y + ay2, ax - ax2, ay - ay2, bx, by)
    else:
        if height2 % 2 and height > 2:
            bx2, by2 = bx2 + dbx, by2 + dby
        yield from _generate_2d(x, y, bx2, by2, ax2, ay2)
        yield from _generate_2d(x + bx2, y + by2, ax, ay, bx - bx2, by - by2)
        yield from _generate_2d(
            x + (ax - dax) + (bx2 - dbx),
            y + (ay - day) + (by2 - dby),
            -bx2,
            -by2,
            -(ax - ax2),
            -(ay - ay2),
        )


def gilbert2d(width: int, height: int) -> list[tuple[int, int]]:
    if width <= 0 or height <= 0:
        return []
    if width >= height:
        points = _generate_2d(0, 0, width, 0, 0, height)
    else:
        points = _generate_2d(0, 0, 0, height, width, 0)
    result = list(points)
    if len(result) != width * height or len(set(result)) != len(result):
        raise ValueError("Gilbert curve did not cover the image exactly")
    return result


def _prepare_image(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGB")
    if image.width > MAX_SIDE or image.height > MAX_SIDE or image.width * image.height > MAX_PIXELS:
        scale = min(
            MAX_SIDE / image.width,
            MAX_SIDE / image.height,
            math.sqrt(MAX_PIXELS / (image.width * image.height)),
        )
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
    return image


def _transform(data: bytes, *, reverse: bool) -> bytes:
    image = _prepare_image(data)
    width, height = image.size
    curve = gilbert2d(width, height)
    pixels = list(image.getdata())
    offset = round(GOLDEN_RATIO_FRACTION * len(curve)) % len(curve)
    output = [None] * len(pixels)

    def index(point: tuple[int, int]) -> int:
        return point[1] * width + point[0]

    for position, point in enumerate(curve):
        target_position = (
            (position - offset) % len(curve)
            if reverse
            else (position + offset) % len(curve)
        )
        output[index(curve[target_position])] = pixels[index(point)]

    result = io.BytesIO()
    final = Image.new("RGB", image.size)
    final.putdata(output)
    final.save(result, format="JPEG", quality=95, optimize=True)
    return result.getvalue()


def encode(data: bytes) -> bytes:
    """混淆一张图片，返回 JPEG bytes。"""
    return _transform(data, reverse=False)


def decode(data: bytes) -> bytes:
    """解混淆一张图片，返回 JPEG bytes。"""
    return _transform(data, reverse=True)


def decode_input(value: str) -> bytes:
    """Decode a CLI base64 value, accepting data URLs and base64:// prefixes."""
    value = value.strip()
    if value.startswith("data:"):
        value = value.split(",", 1)[1]
    if value.startswith("base64://"):
        value = value.removeprefix("base64://")
    return base64.b64decode(value, validate=True)


def save_bytes(data: bytes, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def image_extension(data: bytes) -> str:
    """Return a safe extension for an archived input image."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            return "jpg" if image.format == "JPEG" else (image.format or "img").lower()
    except Exception:
        return "img"
