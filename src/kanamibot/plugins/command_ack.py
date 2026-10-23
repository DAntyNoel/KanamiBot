"""Acknowledge accepted game queries without sending a progress message."""

from __future__ import annotations

import asyncio

from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="查询指令回执",
    description="卡丘和永恒轮回指令开始执行时，在原消息上贴 OK 表情。",
    usage="自动生效，无独立命令。",
    type="library",
)

ACK_PLUGINS = frozenset({"strinova", "er_dak"})
OK_EMOJI_ID = "124"
ACK_TIMEOUT_SECONDS = 2.0


@run_preprocessor
async def acknowledge_query(bot: Bot, event: MessageEvent, matcher: Matcher) -> None:
    if matcher.plugin_name not in ACK_PLUGINS:
        return

    try:
        async with asyncio.timeout(ACK_TIMEOUT_SECONDS):
            await bot.call_api(
                "set_msg_emoji_like",
                message_id=event.message_id,
                emoji_id=OK_EMOJI_ID,
                set=True,
                _timeout=ACK_TIMEOUT_SECONDS,
            )
    except Exception as exc:
        # A reaction failure must never prevent the query from running.
        logger.warning("[command_ack] failed for {}: {}", matcher.plugin_name, type(exc).__name__)
