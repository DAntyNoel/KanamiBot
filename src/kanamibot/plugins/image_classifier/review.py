"""Bounded contact sheets and persistent content IDs for private administrator review."""

from __future__ import annotations

import io
import math
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .commands import group_command
from .config import ROOT, Settings
from .storage import ResultStore

SHANGHAI = ZoneInfo("Asia/Shanghai")
THUMB_SIZE = (200, 200)
# Match the gallery font without importing core's gallery registries and caches.
FONT_PATH = ROOT.parents[1] / "files" / "fonts" / "MiSans-Regular.ttf"


def _daily_plan(
    store: ResultStore,
    settings: Settings,
    now: datetime,
    user: str | None = None,
    exclude_today: bool = False,
    category: tuple[str, str] | None = None,
):
    today = now.astimezone(SHANGHAI).date()
    cutoff = datetime.combine(today, time.min, SHANGHAI)
    if exclude_today:
        cutoff += timedelta(days=1)
    reviewed = store.reviewed_before(user, cutoff) if user is not None else set()
    groups = defaultdict(list)
    seen = set()
    for path, record in store.records(now=now):
        saved = datetime.fromisoformat(record["saved_at"])
        if saved > now or saved.astimezone(SHANGHAI).date() != today:
            continue
        classification = record["classification"]
        if category and (classification["category_a"], classification["category_b"]) != category:
            continue
        if (
            classification["dimension"] not in settings.preview_dimensions
            or classification["safety"] not in settings.preview_safety
            or classification["category_a"] not in settings.preview_categories_a
            or (classification["category_b"] or "unknown") not in settings.preview_categories_b
            or record["sha256"] in seen
            or record["sha256"] in reviewed
        ):
            continue
        seen.add(record["sha256"])
        groups[classification["category_a"], classification["category_b"] or "unknown"].append(
            (path, record)
        )
    pages = []
    if user is not None:
        store.number_review(user, str(today), groups)
    for category, records in sorted(groups.items()):
        if user is None:
            for index, (_, record) in enumerate(records, 1):
                record["review_number"] = index
        for start in range(0, len(records), settings.preview_page_size):
            pages.append((category, records[start : start + settings.preview_page_size]))
    return today, len(seen), pages


def preview_page(
    store: ResultStore,
    settings: Settings,
    page: int = 1,
    *,
    now: datetime | None = None,
    user: str | None = None,
    category: tuple[str, str] | None = None,
    exclude_today: bool = False,
) -> tuple[bytes, str]:
    if page < 1:
        raise ValueError("页码必须大于0")
    today, total, pages = _daily_plan(
        store,
        settings,
        now or datetime.now(UTC),
        user,
        exclude_today,
        category,
    )
    if page > max(1, len(pages)):
        raise ValueError(f"共{max(1, len(pages))}页")
    if not pages:
        return b"", f"{today} 今日图片筛选：0张。"
    category, selected = pages[page - 1]
    return _render_page(store, settings, today, total, category, selected, page, len(pages))[:2]


def daily_preview_pages(
    store: ResultStore,
    settings: Settings,
    *,
    now: datetime | None = None,
    user: str | None = None,
    category: tuple[str, str] | None = None,
    exclude_today: bool = True,
) -> Iterator[tuple[bytes, str, list[str]]]:
    """Snapshot candidates once; render one bounded page at a time off the event loop."""
    today, total, pages = _daily_plan(
        store,
        settings,
        now or datetime.now(UTC),
        user,
        exclude_today,
        category,
    )
    if not pages:
        yield b"", f"{today} 今日图片筛选：0张。", []
    for page, (category, selected) in enumerate(pages, 1):
        yield _render_page(store, settings, today, total, category, selected, page, len(pages))


def _render_page(store, settings, today, total, category, selected, page, pages):
    # Follow image.create_image_gallery's centered thumbnail grid and numbered captions.
    columns = min(5, math.ceil(math.sqrt(len(selected))))
    rows = math.ceil(len(selected) / columns)
    padding, cell_w, cell_h, header = 15, 200, 240, 56
    canvas = Image.new(
        "RGB",
        (columns * (cell_w + padding) + padding, header + rows * (cell_h + padding) + padding),
        "#f0f0f0",
    )
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype(str(FONT_PATH), size=14)
    except OSError:
        font = ImageFont.load_default(size=14)
    group_label = "/".join(category)
    draw.text((padding, 6), f"{today}  {page}/{pages}", font=font, fill="black")
    draw.text((padding, 28), group_label, font=font, fill="black")
    ids = []
    digests = []
    for cell, (path, record) in enumerate(selected):
        index = record["review_number"]
        x = padding + cell % columns * (cell_w + padding)
        y = header + padding + cell // columns * (cell_h + padding)
        token = record["sha256"][:16]
        available = True
        try:
            with Image.open(io.BytesIO(store.image_bytes(path, record))) as image:
                if image.width * image.height > settings.max_pixels:
                    raise ValueError("Image exceeds pixel limit")
                thumbnail = ImageOps.exif_transpose(image).convert("RGBA")
                thumbnail.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
                canvas.paste(
                    thumbnail,
                    (x + (cell_w - thumbnail.width) // 2, y + (200 - thumbnail.height) // 2),
                    thumbnail,
                )
                digests.append(record["sha256"])
        except (OSError, ValueError):
            available = False
            draw.rectangle((x, y, x + 200, y + 200), fill="#cccccc")
            draw.text((x + 10, y + 90), "source unavailable", font=font, fill="black")
        draw.text((x, y + 203), f"[{index}] {group_label}", font=font, fill="black")
        draw.text((x, y + 222), token, font=font, fill="black")
        label = classification_label(record["classification"])
        ids.append(f"[{index}] {token} {label}" + ("" if available else "（源图已失效）"))
    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=80)
    text = (
        f"{today} 今日图片筛选：{total}张，第{page}/{pages}页，本页 {group_label}。\n"
        "北京时间当日保存记录，按内容去重；仅发送缩略图拼图，动图仅显示首帧。\n"
        + (
            f"发送原图：{group_command(category)} 序号，例如 {group_command(category)} 3，5\n"
            if group_command(category)
            else "本组未完整检测，暂不支持按分组发送原图。\n"
        )
        + "序号在本组跨页连续；发送“图”重看缩略图。\n"
        + "\n".join(ids)
    )
    return output.getvalue(), text, digests


def classification_label(result: dict) -> str:
    a = {"A1": "二次元", "A2": "三次元", "A3": "无法判断"}
    b = {"B1": "NSFW", "B2": "涩图", "B3": "SFW", None: "未完整检测"}
    return (
        f"{result['category_a']} {a[result['category_a']]} / "
        f"{result['category_b'] or '?'} {b[result['category_b']]}"
    )
