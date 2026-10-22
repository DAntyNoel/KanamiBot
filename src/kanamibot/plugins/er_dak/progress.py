"""Chinese progress messages for ERCraft's public lookup queue."""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable, Mapping


def format_summary_progress(progress: Mapping) -> str:
    nickname = progress.get("nickname") or "玩家"
    phase = "正在解析玩家身份" if progress.get("phase") == "identity" else "正在准备评价报告"
    parts = [f"ERCraft：{nickname}，{phase}。"]
    queue = progress.get("queue")
    if isinstance(queue, Mapping):
        if queue.get("state") == "running":
            parts.append("网站正在处理。")
        elif queue.get("position") is not None:
            text = f"排队第 {queue['position']} 位"
            if queue.get("playersAhead") is not None:
                text += f"，前面还有 {queue['playersAhead']} 人"
            parts.append(text + "。")
        lower, upper = queue.get("estimatedWaitMinMs"), queue.get("estimatedWaitMaxMs")
        if queue.get("state") != "running" and isinstance(upper, (int, float)):
            if upper <= 0:
                parts.append("即将开始处理。")
            elif isinstance(lower, (int, float)):
                start, end = max(1, math.ceil(lower / 60000)), max(1, math.ceil(upper / 60000))
                duration = str(end) if start == end else f"{start}～{end}"
                parts.append(f"预计 {duration} 分钟后开始。")
        remaining = queue.get("remainingGames")
        if remaining is not None:
            parts.append(f"还有 {remaining} 场记录待收录。")
    parts.append("会持续等待并在完成后发送图片，无需重复查询。")
    return "\n".join(parts)


class SummaryProgressReporter:
    def __init__(self, send: Callable[[str], Awaitable[object]]) -> None:
        self.send = send
        self.last_message: str | None = None
        self.last_phase: str | None = None
        self.last_sent = 0.0

    async def __call__(self, progress: Mapping) -> None:
        message = format_summary_progress(progress)
        now = time.monotonic()
        if message == self.last_message:
            return
        if (self.last_message is not None and progress.get("phase") == self.last_phase
                and now - self.last_sent < 15):
            return
        await self.send(message)
        self.last_message, self.last_phase = message, progress.get("phase")
        self.last_sent = now
