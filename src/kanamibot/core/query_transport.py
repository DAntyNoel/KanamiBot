from __future__ import annotations

import asyncio
import inspect
import json
from functools import wraps

from nonebot import get_driver
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment, PrivateMessageEvent
from nonebot.adapters.qq import MessageSegment as QQSegment
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent
from nonebot.rule import Rule

from . import gallery_transport as gallery
from .group_manager import DATA_PATH, __all_modules__, group_config


def official_only() -> bool:
    return bool(getattr(get_driver().config, "qq_query_only", False))


def is_qq(event: Event) -> bool:
    return isinstance(event, (C2CMessageCreateEvent, GroupMessageCreateEvent))


def user_id(event: Event) -> int:
    return gallery._identity("user", event.get_user_id()) if is_qq(event) else event.user_id


def group_id(event: Event) -> int | None:
    if isinstance(event, GroupMessageCreateEvent):
        return gallery.group_id(event)
    return getattr(event, "group_id", None)


def module_rule(name: str) -> Rule:
    __all_modules__.add(name)

    async def enabled(bot: Bot, event: Event) -> bool:
        if not isinstance(event, (MessageEvent, C2CMessageCreateEvent, GroupMessageCreateEvent)):
            return False
        if isinstance(event, (PrivateMessageEvent, C2CMessageCreateEvent)):
            return True
        if is_qq(event):
            if gallery.is_superuser(event):
                return True
            config = json.loads(DATA_PATH.read_text(encoding="utf-8")) if DATA_PATH.exists() else {}
            data = config.get(str(group_id(event)), {})
            return (user_id(event) not in data.get("blacklist", [])
                    and data.get("modules", {}).get(name, True))
        if str(user_id(event)) in get_driver().config.superusers:
            return True
        group = str(group_id(event))
        return (not group_config.is_user_banned(group, user_id(event))
                and group_config.is_module_enabled(group, name))

    return Rule(enabled)


def image(event: Event | None, data: bytes, *, reply: bool = False):
    if is_qq(event):
        return QQSegment.file_image(data)
    segment = MessageSegment.image(data)
    return MessageSegment.reply(event.message_id) + segment if reply else segment


def reply_deadline(handler):
    @wraps(handler)
    async def wrapped(bot, event, matcher):
        if not is_qq(event):
            return await handler(bot=bot, event=event, matcher=matcher)
        try:
            async with asyncio.timeout(240):
                return await handler(bot=bot, event=event, matcher=matcher)
        except TimeoutError:
            await matcher.finish("查询超过4分钟，已取消以免超出官方回复时限，请稍后重试。")
    wrapped.__signature__ = inspect.signature(handler, eval_str=True)
    return wrapped
