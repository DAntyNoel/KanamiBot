"""Apply group policy before parsing, including inherited waiter permissions."""

from __future__ import annotations

from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.matcher import Matcher
from nonebot.permission import Permission

from kanamibot.core import ModuleRule

MODULE_NAME = "skland"
GROUP_RULE = ModuleRule(MODULE_NAME)


def protect_matcher(matcher: type[Matcher]) -> None:
    original_permission = matcher.permission

    async def allowed(bot: Bot, event: Event) -> bool:
        if not isinstance(event, MessageEvent):
            return False
        if not await GROUP_RULE(bot, event, {}):
            return False
        return await original_permission(bot, event)

    # Rules run concurrently. Permissions run before Alconna can send help or errors;
    # waiter's keep_session=True also inherits this permission for confirmation replies.
    matcher.permission = Permission(allowed)
