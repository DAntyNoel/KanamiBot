"""Bounded contact sheets and persistent content IDs for private administrator review."""

from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw, ImageOps

from .config import Settings
from .storage import ResultStore


def preview_page(store: ResultStore, settings: Settings, page: int = 1) -> tuple[bytes, str]:
    if page < 1:
        raise ValueError("页码必须大于0")
    candidates = []
    seen = set()
    for path, record in store.records():
        classification = record["classification"]
        if (
            classification["dimension"] not in settings.preview_dimensions
            or classification["safety"] not in settings.preview_safety
            or classification["category_a"] not in settings.preview_categories_a
            or (classification["category_b"] or "unknown") not in settings.preview_categories_b
            or record["sha256"] in seen
        ):
            continue
        seen.add(record["sha256"])
        candidates.append((path, record))
    pages = max(1, math.ceil(len(candidates) / settings.preview_page_size))
    if page > pages:
        raise ValueError(f"共{pages}页")
    selected = candidates[
        (page - 1) * settings.preview_page_size : page * settings.preview_page_size
    ]
    canvas = Image.new("RGB", (4 * 240, max(1, math.ceil(len(selected) / 4)) * 210), "white")
    draw = ImageDraw.Draw(canvas)
    ids = []
    for index, (path, record) in enumerate(selected):
        x, y = index % 4 * 240, index // 4 * 210
        token = record["sha256"][:16]
        try:
            with Image.open(io.BytesIO(store.image_bytes(path, record))) as image:
                if image.width * image.height > settings.max_pixels:
                    raise ValueError("Image exceeds pixel limit")
                thumbnail = ImageOps.exif_transpose(image).convert("RGB")
                thumbnail.thumbnail((232, 180))
                canvas.paste(thumbnail, (x + 4, y + 2))
            draw.text((x + 4, y + 186), token, fill="black")
            ids.append(f"{token} {classification_label(record['classification'])}")
        except (OSError, ValueError):
            draw.text((x + 4, y + 186), "source unavailable", fill="black")
    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=80)
    text = (
        f"图片筛选：{len(candidates)}张，第{page}/{pages}页（最近48小时，按内容去重）。\n"
        "动图预览仅显示首帧，保存时保留原文件。\n"
        "#图片预览 页码\n#图片保存 编号 [目录名]\n"
        f"可选目录：{', '.join(settings.saved_dirs)}\n" + "\n".join(ids)
    )
    return output.getvalue(), text


def classification_label(result: dict) -> str:
    a = {"A1": "二次元", "A2": "三次元", "A3": "无法判断"}
    b = {"B1": "NSFW", "B2": "涩图", "B3": "SFW", None: "未完整检测"}
    return (
        f"{result['category_a']} {a[result['category_a']]} / "
        f"{result['category_b'] or '?'} {b[result['category_b']]}"
    )
