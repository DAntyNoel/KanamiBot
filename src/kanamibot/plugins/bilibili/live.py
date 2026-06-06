from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from nonebot.adapters.onebot.v11 import Message, MessageSegment

LIVE_STATUS_ENDPOINT = "https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


class LiveQueryError(RuntimeError):
    def __init__(self, message: str, *, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


@dataclass(slots=True)
class LiveStatus:
    uid: int
    name: str
    live_status: int
    title: str
    cover: str
    url: str


def _normalize_live_status(raw_status: Any) -> int:
    try:
        status = int(raw_status)
    except (TypeError, ValueError):
        return 0
    return 1 if status == 1 else 0


def _parse_live_status(uid: int, item: dict[str, Any]) -> LiveStatus:
    room_id = int(item.get("room_id") or 0)
    return LiveStatus(
        uid=int(item.get("uid") or uid),
        name=str(item.get("uname") or item.get("name") or uid),
        live_status=_normalize_live_status(item.get("live_status")),
        title=str(item.get("title") or ""),
        cover=str(item.get("cover_from_user") or item.get("keyframe") or item.get("cover") or ""),
        url=f"https://live.bilibili.com/{room_id}" if room_id else "",
    )


async def query_live_statuses(uids: list[int]) -> dict[int, LiveStatus]:
    if not uids:
        return {}

    form_data = {"uids[]": [str(uid) for uid in uids]}
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://live.bilibili.com/",
    }

    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
        response = await client.post(LIVE_STATUS_ENDPOINT, data=form_data)

    if response.status_code in {412, 429}:
        raise LiveQueryError(
            f"live status API rate limited: HTTP {response.status_code}",
            rate_limited=True,
        )
    if response.status_code >= 500:
        raise LiveQueryError(f"live status API server error: HTTP {response.status_code}")
    response.raise_for_status()

    payload = response.json()
    if payload.get("code") != 0:
        raise LiveQueryError(f"live status API returned code {payload.get('code')}: {payload}")

    data = payload.get("data")
    if not isinstance(data, dict):
        return {}

    result: dict[int, LiveStatus] = {}
    for raw_uid, item in data.items():
        if not isinstance(item, dict):
            continue
        try:
            uid = int(raw_uid)
        except ValueError:
            uid = int(item.get("uid") or 0)
        if uid:
            result[uid] = _parse_live_status(uid, item)

    return result


def parse_live(status: LiveStatus, last_status: int) -> Message | None:
    current_status = status.live_status
    if int(last_status) == current_status:
        return None

    if current_status == 1:
        msg = MessageSegment.text(f"{status.name} 正在直播\n{status.title}\n")
        if status.cover:
            msg += MessageSegment.image(file=status.cover)
        if status.url:
            msg += MessageSegment.text(f"\n==> {status.url} <==")
        return Message(msg)

    if int(last_status) == 1 and current_status == 0:
        return Message(MessageSegment.text(f"{status.name} 下播了"))

    return None
