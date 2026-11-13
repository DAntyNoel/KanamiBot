"""Bounded contact sheets and persistent content IDs for private administrator review."""

from __future__ import annotations

import io
import math
from collections import Counter, defaultdict
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


def image_details(record: dict) -> str:
    """Source metadata is shown only when explicitly requesting an original."""
    source = record.get("source") or {}

    def sender(value):
        name = value.get("sender_name")
        user = value.get("user_id")
        return f"{name}（QQ {user}）" if name and user else str(name or user or "未知")

    def sent_at(value):
        timestamp = value.get("message_time")
        try:
            return datetime.fromtimestamp(float(timestamp), SHANGHAI).strftime(
                "%Y-%m-%d %H:%M:%S（北京时间）"
            )
        except (TypeError, ValueError, OverflowError, OSError):
            return "未知"

    group = source.get("group_id")
    group_name = source.get("group_name")
    group_label = f"{group_name}（群号 {group}）" if group_name else str(group)
    lines = [
        f"来源：群聊 {group_label}"
        if group is not None else "来源：私聊" if source else "来源：未知",
        f"发送者：{sender(source)}",
        f"消息时间：{sent_at(source)}",
    ]
    if source.get("forwarded"):
        original = source.get("forwarded_source") or {}
        lines += [
            "合并转发内图片（以上为转发消息来源，原始群聊未知）",
            f"转发节点发送者：{sender(original)}",
            f"转发节点时间：{sent_at(original)}",
        ]
    result = record.get("classification")
    if result:
        lines.append(f"分类：{classification_label(result)}")
    return "\n".join(lines)


def collection_overview(store: ResultStore, *, now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    today = current.astimezone(SHANGHAI).date()
    unique = {}
    sources = 0
    for _, record in store.records(now=current):
        if datetime.fromisoformat(record["saved_at"]) <= current:
            sources += 1
            unique.setdefault(record["sha256"], record)
    cached = Counter()
    daily = Counter()
    for record in unique.values():
        result = record["classification"]
        category = result["category_a"] + (result["category_b"] or "B?")
        cached[category] += 1
        if datetime.fromisoformat(record["saved_at"]).astimezone(SHANGHAI).date() == today:
            daily[category] += 1
    lines = [
        f"图片收集总览（北京时间 {today}，按内容去重）",
        f"今日：{sum(daily.values())}张；48小时缓存：{len(unique)}张；来源记录：{sources}条。",
    ]
    for a in range(1, 4):
        for b in range(1, 4):
            key = f"A{a}B{b}"
            lines.append(f"{key}：今日 {daily[key]} / 缓存 {cached[key]}")
        unknown = f"A{a}B?"
        if cached[unknown]:
            lines.append(f"{unknown}（未完整检测）：今日 {daily[unknown]} / 缓存 {cached[unknown]}")
    lines.append("统计全部已收集分类，不受预览筛选影响；查看不影响23点自动审查。")
    return "\n".join(lines)


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


def nightly_preview_batches(
    store: ResultStore, settings: Settings, *, user: str, now: datetime | None = None
) -> Iterator[tuple[tuple[str, str], list[tuple[bytes, str, list[str]]]]]:
    """Group sheets by classification, with at most 20 nodes per forward message."""
    today, total, pages = _daily_plan(store, settings, now or datetime.now(UTC), user, True)
    for category in dict.fromkeys(category for category, _ in pages):
        selected_pages = [
            (index, selected) for index, (key, selected) in enumerate(pages, 1) if key == category
        ]
        for start in range(0, len(selected_pages), 20):
            yield (
                category,
                [
                    _render_page(
                        store, settings, today, total, category, selected, index, len(pages)
                    )
                    for index, selected in selected_pages[start : start + 20]
                ],
            )


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
