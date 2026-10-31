from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, PrivateMessageEvent
from nonebot.exception import FinishedException, StopPropagation
from nonebot.message import check_and_run_matcher

nonebot.init(_env_file=None)
debug = importlib.import_module("kanamibot.plugins.debug")


def event(text: str, *, user_id: int, role: str | None):
    common = dict(
        time=0,
        self_id=10000,
        post_type="message",
        sub_type="normal" if role else "friend",
        user_id=user_id,
        message_id=10,
        message=Message(text),
        raw_message=text,
        font=0,
        sender={"user_id": user_id},
    )
    if role:
        common["sender"]["role"] = role
        return GroupMessageEvent(message_type="group", group_id=20000, **common)
    return PrivateMessageEvent(message_type="private", **common)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [None, "member", "admin", "owner"])
@pytest.mark.parametrize("superuser", [True, False])
async def test_only_superuser_can_toggle_even_for_group_owner(monkeypatch, role, superuser):
    config = SimpleNamespace(superusers={"30000"})
    adapter = SimpleNamespace(
        config=config,
        get_name=lambda: "OneBot V11",
        _call_api=AsyncMock(return_value={"message_id": 11}),
    )
    bot = Bot(adapter, "10000")
    switch = []
    monkeypatch.setattr(debug, "set_debug_mode", lambda value: switch.append(value))
    message = event("#debug 开启", user_id=30000 if superuser else 40000, role=role)
    try:
        await check_and_run_matcher(debug.debug_command, bot, message, {})
    except StopPropagation:
        pass
    assert switch == ([True] if superuser else [])
    assert adapter._call_api.await_count == (1 if superuser else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,expected",
    [
        ("#debug 开启", True),
        ("#DEBUG ON", True),
        ("#debug 关闭", False),
        ("#debug off", False),
        ("#debug 状态", None),
        ("#debug", None),
        ("#debug status", None),
        ("#debug 无效参数", None),
    ],
)
async def test_command_actions_and_no_env_writes(monkeypatch, text, expected):
    switches = []
    monkeypatch.setattr(debug, "set_debug_mode", lambda value: switches.append(value) or "INFO")
    monkeypatch.setattr(debug, "debug_enabled", lambda: False)
    matcher = SimpleNamespace(finish=AsyncMock(side_effect=FinishedException))
    with pytest.raises(FinishedException):
        await debug.handle_debug(event(text, user_id=30000, role=None), matcher)
    assert switches == ([] if expected is None else [expected])
    matcher.finish.assert_awaited_once()
    response = matcher.finish.await_args.args[0]
    if "无效" in text:
        assert "用法" in response
    elif expected is None:
        assert "当前关闭" in response
