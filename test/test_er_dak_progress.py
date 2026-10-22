from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from kanamibot.plugins.er_dak.progress import SummaryProgressReporter, format_summary_progress


def test_queue_message_preserves_position_people_ahead_and_estimate():
    message = format_summary_progress({
        "nickname": "小冰", "phase": "identity", "queue": {
            "state": "queued", "position": 3, "playersAhead": 2,
            "estimatedWaitMinMs": 60000, "estimatedWaitMaxMs": 120000,
        },
    })
    assert "小冰" in message and "解析玩家身份" in message
    assert "第 3 位" in message and "还有 2 人" in message and "1～2 分钟" in message


def test_progress_suppresses_unchanged_messages_and_throttles_queue_changes(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("kanamibot.plugins.er_dak.progress.time.monotonic", lambda: now[0])
    send = AsyncMock()
    reporter = SummaryProgressReporter(send)
    progress = {"phase": "identity", "queue": {"state": "queued", "position": 3}}

    async def exercise():
        await reporter(progress)
        await reporter(progress)
        progress["queue"]["position"] = 2
        await reporter(progress)
        assert send.await_count == 1
        now[0] += 16
        await reporter(progress)
        assert send.await_count == 2
        progress["phase"] = "report"
        await reporter(progress)
        assert send.await_count == 3

    asyncio.run(exercise())
