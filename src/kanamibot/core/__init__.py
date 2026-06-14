from __future__ import annotations

from nonebot import get_driver

from .chat_history import *  # noqa: F403
from .check_perm import *  # noqa: F403
from .config_storage import ConfigManager as ConfigManager
from .group_manager import *  # noqa: F403
from .image_wrappers import *  # noqa: F403
from .media_storage import AdvancedMediaStorageSystem as AdvancedMediaStorageSystem
from .utils import *  # noqa: F403


async def get_first_superuser() -> int | None:
    """获取配置中的第一个超管 ID"""
    superusers = get_driver().config.superusers
    if not superusers:
        return None

    try:
        return int(list(superusers)[0])
    except (TypeError, ValueError):
        return None
