from __future__ import annotations

import html
import re
import time
from typing import Any

CQ_RE = re.compile(r"\[CQ:(?P<type>[a-zA-Z0-9_]+)(?P<data>,[^\]]*)?\]")
CQ_KV_RE = re.compile(r"([a-zA-Z_][\w-]*)=([^,\]]*)")


def now_ts() -> int:
    return int(time.time())


def onebot_response(
    echo: Any,
    data: Any = None,
    *,
    ok: bool = True,
    message: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "ok" if ok else "failed",
        "retcode": 0 if ok else 1400,
        "data": data,
        "echo": echo,
    }
    if message:
        payload["message"] = message
        payload["wording"] = message
    return payload


def parse_cq_params(raw_data: str | None) -> dict[str, str]:
    if not raw_data:
        return {}
    text = raw_data[1:] if raw_data.startswith(",") else raw_data
    return {key: html.unescape(value) for key, value in CQ_KV_RE.findall(text)}


def parse_message_text(text: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    cursor = 0
    for match in CQ_RE.finditer(text):
        if match.start() > cursor:
            segments.append({"type": "text", "data": {"text": text[cursor : match.start()]}})

        segment_type = match.group("type")
        params = parse_cq_params(match.group("data"))
        if segment_type == "reply":
            segments.append({"type": "reply", "data": {"id": params.get("id", "")}})
        elif segment_type == "at":
            segments.append({"type": "at", "data": {"qq": params.get("qq", "")}})
        elif segment_type == "image":
            data = {}
            if file_value := params.get("file"):
                data["file"] = file_value
            if url_value := params.get("url"):
                data["url"] = url_value
            segments.append({"type": "image", "data": data})
        else:
            segments.append({"type": segment_type, "data": params})
        cursor = match.end()

    if cursor < len(text):
        segments.append({"type": "text", "data": {"text": text[cursor:]}})
    if not segments:
        segments.append({"type": "text", "data": {"text": ""}})
    return segments


def normalize_message(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, str):
        return parse_message_text(value)
    if isinstance(value, dict) and "type" in value:
        data = value.get("data")
        return [{"type": str(value.get("type")), "data": data if isinstance(data, dict) else {}}]
    if isinstance(value, list | tuple):
        segments: list[dict[str, Any]] = []
        for item in value:
            segments.extend(normalize_message(item))
        return segments
    return [{"type": "text", "data": {"text": str(value)}}]


def message_plain_text(segments: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for segment in segments:
        segment_type = segment.get("type")
        data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
        if segment_type == "text":
            parts.append(str(data.get("text", "")))
        elif segment_type == "at":
            parts.append(f"[@{data.get('qq', '')}]")
        elif segment_type in {"reply", "quote"}:
            parts.append(f"[reply:{data.get('id') or data.get('message_id') or ''}]")
        elif segment_type == "image":
            parts.append(f"[image:{data.get('url') or data.get('file') or ''}]")
        elif segment_type == "node":
            parts.append("[node]")
        else:
            parts.append(f"[{segment_type}]")
    return "".join(parts)


def sender_payload(user_id: int, nickname: str, role: str = "member") -> dict[str, Any]:
    return {
        "user_id": user_id,
        "nickname": nickname,
        "card": nickname,
        "sex": "unknown",
        "age": 0,
        "area": "",
        "level": "",
        "role": role,
        "title": "",
    }


def group_message_event(
    *,
    self_id: int,
    message_id: int,
    group_id: int,
    user_id: int,
    nickname: str,
    role: str,
    message: list[dict[str, Any]],
    raw_message: str,
) -> dict[str, Any]:
    return {
        "time": now_ts(),
        "self_id": self_id,
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": message_id,
        "group_id": group_id,
        "user_id": user_id,
        "message": message,
        "raw_message": raw_message,
        "font": 0,
        "sender": sender_payload(user_id, nickname, role),
    }


def private_message_event(
    *,
    self_id: int,
    message_id: int,
    user_id: int,
    nickname: str,
    message: list[dict[str, Any]],
    raw_message: str,
) -> dict[str, Any]:
    return {
        "time": now_ts(),
        "self_id": self_id,
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": message_id,
        "user_id": user_id,
        "message": message,
        "raw_message": raw_message,
        "font": 0,
        "sender": {
            "user_id": user_id,
            "nickname": nickname,
            "sex": "unknown",
            "age": 0,
        },
    }


def lifecycle_event(self_id: int) -> dict[str, Any]:
    return {
        "time": now_ts(),
        "self_id": self_id,
        "post_type": "meta_event",
        "meta_event_type": "lifecycle",
        "sub_type": "connect",
    }


def heartbeat_event(self_id: int) -> dict[str, Any]:
    return {
        "time": now_ts(),
        "self_id": self_id,
        "post_type": "meta_event",
        "meta_event_type": "heartbeat",
        "status": {
            "online": True,
            "good": True,
        },
        "interval": 30000,
    }
