"""Chinese progress messages for ERCraft's public lookup queue."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Mapping

QUEUE_NOTICE_THRESHOLD = 5


def format_summary_progress(progress: Mapping) -> str | None:
    queue = progress.get("queue")
    if not isinstance(queue, Mapping) or queue.get("state") in ("running", "terminal", "idle"):
        return None
    if queue.get("state") != "queued" and queue.get("position") is None:
        return None
    players_ahead = queue.get("playersAhead")
    if type(players_ahead) is not int or players_ahead < 0:
        position = queue.get("position")
        if type(position) is not int or position < 1:
            return None
        players_ahead = position - 1
    if players_ahead <= QUEUE_NOTICE_THRESHOLD:
        return None
    nickname = progress.get("nickname") or "玩家"
    parts = [f"ERCraft：{nickname}，已进入查询队列。"]
    if queue.get("position") is not None:
        text = f"排队第 {queue['position']} 位"
        if queue.get("playersAhead") is not None:
            text += f"，前面还有 {queue['playersAhead']} 人"
        parts.append(text + "。")
    lower, upper = queue.get("estimatedWaitMinMs"), queue.get("estimatedWaitMaxMs")
    if isinstance(upper, (int, float)):
        if upper <= 0:
            parts.append("即将开始处理。")
        elif isinstance(lower, (int, float)):
            start, end = max(1, math.ceil(lower / 60000)), max(1, math.ceil(upper / 60000))
            duration = str(end) if start == end else f"{start}～{end}"
            parts.append(f"预计 {duration} 分钟后开始。")
    parts.append("会持续等待并在完成后发送图片，无需重复查询。")
    return "\n".join(parts)


class SummaryProgressReporter:
    def __init__(self, send: Callable[[str], Awaitable[object]]) -> None:
        self.send = send
        self.sent = False

    async def __call__(self, progress: Mapping) -> None:
        if self.sent:
            return
        message = format_summary_progress(progress)
        if message is None:
            return
        self.sent = True
        await self.send(message)
