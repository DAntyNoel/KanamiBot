"""Shared, best-effort message reactions for OneBot plugins."""

from __future__ import annotations

import asyncio

from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.log import logger

OK_EMOJI_ID = "124"


async def react_ok(bot: Bot, event: MessageEvent, *, timeout: float = 2.0) -> None:
    try:
        async with asyncio.timeout(timeout):
            await bot.call_api(
                "set_msg_emoji_like",
                message_id=event.message_id,
                emoji_id=OK_EMOJI_ID,
                set=True,
                _timeout=timeout,
            )
    except Exception as exc:
        # A failed reaction must not turn a successful operation into an error.
        logger.warning(
            "[message_reactions] failed for message {}: {}", event.message_id, type(exc).__name__
        )
