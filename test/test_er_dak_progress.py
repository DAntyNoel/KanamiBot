from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import nonebot
import pytest

nonebot.init()

from kanamibot.plugins.er_dak.progress import (  # noqa: E402
    SummaryProgressReporter,
    format_summary_progress,
)


def test_queue_message_preserves_position_people_ahead_and_estimate():
    message = format_summary_progress({
        "nickname": "小冰", "phase": "identity", "queue": {
            "state": "queued", "position": 7, "playersAhead": 6,
            "estimatedWaitMinMs": 60000, "estimatedWaitMaxMs": 120000,
        },
    })
    assert "小冰" in message and "已进入查询队列" in message
    assert "第 7 位" in message and "还有 6 人" in message and "1～2 分钟" in message
    assert "正在" not in message


@pytest.mark.parametrize("phase", ["identity", "refresh", "report"])
@pytest.mark.parametrize("queue", [None, {}, {"state": "running"}, {"state": "terminal"}])
def test_nonqueued_stages_do_not_send_progress_messages(phase, queue):
    assert format_summary_progress({"nickname": "宇麟涵", "phase": phase, "queue": queue}) is None


@pytest.mark.parametrize("players_ahead", [0, 1, 4, 5])
def test_small_queues_do_not_send_progress_messages(players_ahead):
    queue = {"state": "queued", "position": players_ahead + 1, "playersAhead": players_ahead}
    assert format_summary_progress({"queue": queue}) is None


@pytest.mark.parametrize(
    ("queue", "should_notify"),
    [
        ({"state": "queued", "position": 6}, False),
        ({"state": "queued", "position": 7}, True),
        ({"state": "queued", "playersAhead": 6}, True),
        ({"state": "queued", "position": 20, "playersAhead": 5}, False),
        ({"state": "queued"}, False),
        ({"state": "queued", "position": "unknown"}, False),
        ({"state": "queued", "playersAhead": True}, False),
        ({"state": "running", "position": 20, "playersAhead": 19}, False),
    ],
)
def test_queue_threshold_uses_people_ahead_or_position_without_guessing(queue, should_notify):
    assert (format_summary_progress({"queue": queue}) is not None) is should_notify


def test_only_one_queue_message_is_sent_across_all_stages_and_position_changes():
    send = AsyncMock()
    reporter = SummaryProgressReporter(send)
    progress = {"phase": "identity", "queue": {"state": "queued", "position": 4}}

    async def exercise():
        await reporter({"phase": "refresh"})
        await reporter({"phase": "report", "queue": {"state": "terminal"}})
        await reporter(progress)
        await reporter({"queue": {"state": "queued", "position": 6}})
        send.assert_not_awaited()
        progress["queue"]["position"] = 8
        await reporter(progress)
        await reporter(progress)
        progress["queue"]["position"] = 7
        await reporter(progress)
        await reporter(progress)
        progress["phase"] = "refresh"
        await reporter(progress)
        progress["phase"] = "report"
        await reporter(progress)
        await reporter({"phase": "report"})
        send.assert_awaited_once()
        assert "第 8 位" in send.await_args.args[0]

    asyncio.run(exercise())
