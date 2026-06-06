from __future__ import annotations

from nonebot.adapters.onebot.v11 import GROUP_ADMIN, GROUP_OWNER, Bot, GroupMessageEvent
from nonebot.permission import SUPERUSER

# --- 权限接口定义 ---

LevelSuper = SUPERUSER

LevelOwner = SUPERUSER | GROUP_OWNER
'''
2. 群主及以上 (群主 + 超管)
注意：通常超管也应该拥有群主权限，所以用 | 连接
'''

LevelAdmin = SUPERUSER | GROUP_OWNER | GROUP_ADMIN
'''3. 群管理及以上 (管理 + 群主 + 超管)'''

GroupAdmins = GROUP_OWNER | GROUP_ADMIN

async def check_permission(bot: Bot, event: GroupMessageEvent, level) -> bool:
    """
    Asynchronously checks if a user has the required permission level.

    Args:
        bot (Bot): The bot instance used to interact with the messaging platform.
        event (GroupMessageEvent): The event object containing details about the group message.
        level (Callable): [LevelSuper, LevelOwner, LevelAdmin]

    Returns:
        bool: True if the permission check passes, False otherwise.
    """
    return await level(bot, event)
