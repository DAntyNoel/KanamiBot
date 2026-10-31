"""Acknowledge accepted game queries without sending a progress message."""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor
from nonebot.plugin import PluginMetadata

from kanamibot.core.message_reactions import react_ok
from kanamibot.core.timing import timed

__plugin_meta__ = PluginMetadata(
    name="查询指令回执",
    description="卡丘和永恒轮回指令开始执行时，在原消息上贴 OK 表情。",
    usage="自动生效，无独立命令。",
    type="library",
)

ACK_PLUGINS = frozenset({"strinova", "er_dak"})
ACK_TIMEOUT_SECONDS = 2.0


@run_preprocessor
@timed()
async def acknowledge_query(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    if matcher.plugin_name not in ACK_PLUGINS:
        return

    await react_ok(bot, event, timeout=ACK_TIMEOUT_SECONDS)
