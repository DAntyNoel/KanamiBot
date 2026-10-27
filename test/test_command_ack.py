from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.message import check_and_run_matcher
from nonebot.permission import Permission
from nonebot.rule import Rule

nonebot.init(_env_file=None)

from kanamibot.plugins import command_ack  # noqa: E402


def event() -> GroupMessageEvent:
    return GroupMessageEvent(
        time=0,
        self_id=10000,
        post_type="message",
        sub_type="normal",
        message_type="group",
        group_id=20000,
        user_id=30000,
        message_id=42,
        message=MessageSegment.reply(99) + Message("#BS战绩 玩家"),
        raw_message="#BS战绩 玩家",
        font=0,
        sender={"user_id": 30000, "role": "member"},
    )


def exercise(plugin_name: str | None, *, allowed=True, matched=True, failure=None, stalled=False):
    calls = []

    async def call_api(api, **data):
        calls.append((api, data))
        if stalled:
            await asyncio.Future()
        if failure is not None:
            raise failure

    async def handler():
        calls.append(("handler", {}))

    async def permission():
        return allowed

    async def rule():
        return matched

    base = type("QueryMatcher", (Matcher,), {"plugin_name": plugin_name})
    matcher = base.new(
        "message",
        handlers=[handler],
        permission=Permission(permission),
        rule=Rule(rule),
        block=False,
    )
    adapter = SimpleNamespace(
        get_name=lambda: "OneBot V11",
        config=nonebot.get_driver().config,
        _call_api=AsyncMock(),
    )
    bot = Bot(adapter, "10000")

    async def run():
        with patch.object(bot, "call_api", side_effect=call_api):
            await asyncio.wait_for(check_and_run_matcher(matcher, bot, event(), {}), timeout=1)

    try:
        asyncio.run(run())
    finally:
        matcher.destroy()
    return calls


@pytest.mark.parametrize("plugin_name", ["strinova", "er_dak"])
def test_query_ack_precedes_handler_and_targets_original_command(plugin_name):
    calls = exercise(plugin_name)
    assert calls == [
        (
            "set_msg_emoji_like",
            {"message_id": 42, "emoji_id": "124", "set": True, "_timeout": 2.0},
        ),
        ("handler", {}),
    ]


@pytest.mark.parametrize("plugin_name", ["image", "bilibili", "codex_gpt", None])
def test_other_plugins_receive_no_ack(plugin_name):
    assert exercise(plugin_name) == [("handler", {})]


@pytest.mark.parametrize(("allowed", "matched"), [(False, True), (True, False)])
def test_unaccepted_queries_receive_no_ack(allowed, matched):
    assert exercise("strinova", allowed=allowed, matched=matched) == []


@pytest.mark.parametrize("failure", [RuntimeError("not supported"), TimeoutError()])
def test_ack_failure_does_not_cancel_business_handler(failure):
    assert exercise("er_dak", failure=failure)[-1] == ("handler", {})


def test_stalled_ack_is_bounded_and_query_continues():
    with patch.object(command_ack, "ACK_TIMEOUT_SECONDS", 0.01):
        assert exercise("strinova", stalled=True)[-1] == ("handler", {})
