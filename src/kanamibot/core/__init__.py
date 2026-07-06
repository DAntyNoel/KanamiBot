from __future__ import annotations

from nonebot import get_driver


async def get_first_superuser() -> int | None:
    """获取配置中的第一个超管 ID"""
    superusers = get_driver().config.superusers
    if not superusers:
        return None

    try:
        return int(list(superusers)[0])
    except (TypeError, ValueError):
        return None
