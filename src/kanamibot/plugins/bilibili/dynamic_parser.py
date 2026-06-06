from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.log import logger

from .settings import USE_FORWARD

ParsedDynamic = dict[str, Any]
DynamicMessage = Message | list[Message]

ACTIONS = {
    "DYNAMIC_TYPE_FORWARD": "转发了一条动态",
    "DYNAMIC_TYPE_AV": "投稿了视频",
    "DYNAMIC_TYPE_WORD": "发布了文字动态",
    "DYNAMIC_TYPE_DRAW": "发布了图文动态",
    "DYNAMIC_TYPE_ARTICLE": "发布了专栏",
    "DYNAMIC_TYPE_LIVE_RCMD": "正在直播",
}


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dynamic_url(dynamic_id: str | int) -> str:
    return f"https://t.bilibili.com/{dynamic_id}"


def _strip_protocol(url: str) -> str:
    return url.removeprefix("//")


def parse_raw_dynamic(item: dict[str, Any]) -> ParsedDynamic | None:
    try:
        modules = _safe_dict(item.get("modules"))
        author = _safe_dict(modules.get("module_author"))
        dynamic = _safe_dict(modules.get("module_dynamic"))
        stat = _safe_dict(modules.get("module_stat"))
        dynamic_id = item.get("id_str") or item.get("id")

        if not dynamic_id:
            logger.warning("[Bilibili] Dynamic item missing id: %s", item)
            return None

        parsed: ParsedDynamic = {
            "type": item.get("type") or "",
            "id": int(dynamic_id),
            "url": _safe_dict(item.get("basic")).get("jump_url") or _dynamic_url(dynamic_id),
            "pub_ts": int(author.get("pub_ts") or 0),
            "mid": int(author.get("mid") or 0),
            "name": str(author.get("name") or ""),
            "pub_time": str(author.get("pub_time") or ""),
            "orig": item.get("orig"),
            "desc": dynamic.get("desc"),
            "major": dynamic.get("major"),
            "like": None,
            "forward": None,
            "comment": None,
        }

        if stat:
            parsed.update(
                {
                    "like": _safe_dict(stat.get("like")).get("count"),
                    "forward": _safe_dict(stat.get("forward")).get("count"),
                    "comment": _safe_dict(stat.get("comment")).get("count"),
                }
            )

        return parsed
    except Exception as exc:
        logger.warning("[Bilibili] Failed to parse raw dynamic: %s", exc)
        return None


def parse_rich_text_nodes(nodes: list[Any]) -> Message:
    msg = Message()
    for node in nodes:
        node = _safe_dict(node)
        node_type = node.get("type")
        if node_type == "RICH_TEXT_NODE_TYPE_EMOJI":
            icon_url = _safe_dict(node.get("emoji")).get("icon_url")
            if icon_url:
                msg += MessageSegment.image(file=icon_url)
        elif node_type == "RICH_TEXT_NODE_TYPE_WEB":
            msg += MessageSegment.text(f"“{node.get('text', '')}”")
        else:
            msg += MessageSegment.text(str(node.get("text") or ""))
    return msg


def _add_header(msg: Message, dynamic_data: ParsedDynamic, *, manual: bool) -> None:
    action = ACTIONS[dynamic_data["type"]]
    if manual:
        timestamp = float(dynamic_data.get("pub_ts") or 0)
        datetime_text = (
            dt.datetime.fromtimestamp(timestamp).strftime("%y年%m月%d日 %H时%M分%S")
            if timestamp
            else "未知时间"
        )
        msg += MessageSegment.text(
            f"{dynamic_data['name']} {dynamic_data.get('pub_time')}\n"
            f"{datetime_text} {action}: \n"
        )
        return

    msg += MessageSegment.text(f"{dynamic_data['name']} {action}: \n")


def _add_summary(msg: Message, opus: dict[str, Any]) -> None:
    title = opus.get("title")
    if title:
        msg += MessageSegment.text(f"\n《{title}》\n")

    summary = _safe_dict(opus.get("summary"))
    nodes = _safe_list(summary.get("rich_text_nodes"))
    if nodes:
        msg += parse_rich_text_nodes(nodes)


def _render_forward(msg: Message, dynamic_data: ParsedDynamic, *, manual: bool) -> DynamicMessage:
    desc = _safe_dict(dynamic_data.get("desc"))
    origin = None
    raw_origin = dynamic_data.get("orig")
    if isinstance(raw_origin, dict) and raw_origin.get("type") != "DYNAMIC_TYPE_NONE":
        origin = parse_raw_dynamic(raw_origin)

    if origin:
        msg += MessageSegment.text(f"( 源动态{_strip_protocol(origin['url'])} )\n\n")
    else:
        msg += MessageSegment.text("( 源动态已失效 )\n\n")

    nodes = _safe_list(desc.get("rich_text_nodes"))
    if nodes:
        msg += parse_rich_text_nodes(nodes)
    return msg


def _render_video(
    msg: Message,
    dynamic_data: ParsedDynamic,
    *,
    manual: bool,
) -> DynamicMessage | None:
    archive = _safe_dict(_safe_dict(dynamic_data.get("major")).get("archive"))
    if not archive:
        return None

    msg += MessageSegment.text(f"\n{archive.get('title', '')}\n")
    cover = archive.get("cover")
    if cover:
        msg += MessageSegment.image(file=cover)

    if manual:
        stat = _safe_dict(archive.get("stat"))
        msg += MessageSegment.text(
            f"\n时长{archive.get('duration_text', '未知')} "
            f"播放{stat.get('play', 0)} 弹幕{stat.get('danmaku', 0)}\n"
        )

    desc = archive.get("desc")
    if isinstance(desc, str) and desc:
        msg += MessageSegment.text(f"\n{desc}")
    return msg


def _render_word(
    msg: Message,
    dynamic_data: ParsedDynamic,
    *,
    manual: bool,
) -> DynamicMessage | None:
    opus = _safe_dict(_safe_dict(dynamic_data.get("major")).get("opus"))
    if not opus:
        return None
    _add_summary(msg, opus)
    return msg


def _render_draw_or_article(
    msg: Message,
    dynamic_data: ParsedDynamic,
    *,
    manual: bool,
) -> DynamicMessage | None:
    opus = _safe_dict(_safe_dict(dynamic_data.get("major")).get("opus"))
    if not opus:
        return None

    _add_summary(msg, opus)
    pictures = [
        pic.get("url")
        for pic in _safe_list(opus.get("pics"))
        if isinstance(pic, dict) and pic.get("url")
    ]

    if USE_FORWARD and pictures:
        msg += MessageSegment.text(f"\n==> {_strip_protocol(dynamic_data['url'])} <==")
        return [Message(msg), *[Message(MessageSegment.image(file=url)) for url in pictures]]

    for url in pictures:
        msg += MessageSegment.image(file=url)
    return msg


def _render_live_rcmd(
    msg: Message,
    dynamic_data: ParsedDynamic,
    *,
    manual: bool,
) -> DynamicMessage | None:
    live_rcmd = _safe_dict(_safe_dict(dynamic_data.get("major")).get("live_rcmd"))
    content = live_rcmd.get("content")
    if not isinstance(content, str):
        return None

    try:
        live_data = json.loads(content)
    except json.JSONDecodeError:
        return None

    live_play_info = _safe_dict(_safe_dict(live_data).get("live_play_info"))
    msg += MessageSegment.text(f"{live_play_info.get('title', '')}\n")
    cover = live_play_info.get("cover")
    if cover:
        msg += MessageSegment.image(file=cover)
    return msg


RENDERERS: dict[str, Callable[[Message, ParsedDynamic], DynamicMessage | None]] = {
    "DYNAMIC_TYPE_FORWARD": lambda msg, data: _render_forward(msg, data, manual=False),
    "DYNAMIC_TYPE_AV": lambda msg, data: _render_video(msg, data, manual=False),
    "DYNAMIC_TYPE_WORD": lambda msg, data: _render_word(msg, data, manual=False),
    "DYNAMIC_TYPE_DRAW": lambda msg, data: _render_draw_or_article(msg, data, manual=False),
    "DYNAMIC_TYPE_ARTICLE": lambda msg, data: _render_draw_or_article(msg, data, manual=False),
    "DYNAMIC_TYPE_LIVE_RCMD": lambda msg, data: _render_live_rcmd(msg, data, manual=False),
}


def parse_dynamic(dynamic_data: ParsedDynamic, *, manual: bool = False) -> DynamicMessage | None:
    dtype = dynamic_data.get("type")
    if dtype not in ACTIONS:
        logger.debug("[Bilibili] Unsupported dynamic type: %s", dtype)
        return None

    if not dynamic_data.get("url"):
        dynamic_data["url"] = _dynamic_url(dynamic_data["id"])

    msg = Message()
    _add_header(msg, dynamic_data, manual=manual)

    if dtype == "DYNAMIC_TYPE_FORWARD":
        rendered = _render_forward(msg, dynamic_data, manual=manual)
    elif dtype == "DYNAMIC_TYPE_AV":
        rendered = _render_video(msg, dynamic_data, manual=manual)
    elif dtype == "DYNAMIC_TYPE_WORD":
        rendered = _render_word(msg, dynamic_data, manual=manual)
    elif dtype in {"DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_ARTICLE"}:
        rendered = _render_draw_or_article(msg, dynamic_data, manual=manual)
    elif dtype == "DYNAMIC_TYPE_LIVE_RCMD":
        rendered = _render_live_rcmd(msg, dynamic_data, manual=manual)
    else:
        rendered = None

    if rendered is None:
        return None

    if isinstance(rendered, list):
        return rendered

    rendered += MessageSegment.text(f"\n==> {_strip_protocol(dynamic_data['url'])} <==")
    return rendered
