"""Cache current Skland records and limit the number of records sent to the renderer."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from time import time
from weakref import WeakValueDictionary

from nonebot import logger
from nonebot.matcher import current_matcher
from nonebot_plugin_alconna import ALCONNA_RESULT, Alconna, Args, Option, command_manager
from nonebot_plugin_skland.commands import garrison as upstream
from nonebot_plugin_skland.config import DATA_DIR
from nonebot_plugin_skland.model import SkUser
from nonebot_plugin_skland.render import render_garrison_history
from nonebot_plugin_skland.schemas.arknights.garrison import (
    GarrisonData,
    GarrisonResources,
    GarrisonView,
)
from nonebot_plugin_skland.services.garrison import fetch_garrison
from nonebot_plugin_skland.utils.files import atomic_write
from pydantic import BaseModel

CACHE_TTL = 60
CACHE_DIR = DATA_DIR / "garrison_cache"
_cache_locks: WeakValueDictionary[Path, asyncio.Lock] = WeakValueDictionary()


class Snapshot(BaseModel):
    data: GarrisonData
    resources: GarrisonResources
    activity_id: str


def _read_snapshot(path: Path) -> Snapshot | None:
    try:
        if 0 <= time() - path.stat().st_mtime < CACHE_TTL:
            snapshot = Snapshot.model_validate_json(path.read_bytes())
            snapshot.resources.current_resource(snapshot.activity_id)
            return snapshot
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as error:
        logger.warning(f"无法读取卫戍战绩缓存（{type(error).__name__}）")
    return None


def _write_snapshot(path: Path, snapshot: Snapshot) -> None:
    try:
        with atomic_write(path) as file:
            file.write(snapshot.model_dump_json().encode())
    except OSError as error:
        logger.warning(f"无法写入卫戍战绩缓存（{type(error).__name__}）")


async def fetch_cached_garrison(
    user: SkUser, uid: str
) -> tuple[GarrisonData, GarrisonResources, str]:
    identity = (user.owner_id, user.id, user.skland_user_id, uid)
    path = CACHE_DIR / f"{sha256(repr(identity).encode()).hexdigest()}.json"
    lock = _cache_locks.setdefault(path, asyncio.Lock())
    async with lock:
        snapshot = await asyncio.to_thread(_read_snapshot, path)
        if snapshot is None:
            data, resources, activity_id = await fetch_garrison(user, uid)
            resources.current_resource(activity_id)
            snapshot = Snapshot(data=data, resources=resources, activity_id=activity_id)
            await asyncio.to_thread(_write_snapshot, path, snapshot)
    return snapshot.data, snapshot.resources, snapshot.activity_id


async def render_limited_garrison(view: GarrisonView, *, show_all: bool = False) -> list[bytes]:
    matcher = current_matcher.get(None)
    if matcher is not None and (command_result := matcher.state.get(ALCONNA_RESULT)) is not None:
        count = command_result.result.query("garrison.count.record_count")
        if count is not None:
            view = view.model_copy(update={"records": view.records[:count]})
            show_all = True
    return await render_garrison_history(view, show_all=show_all)


def install_garrison(command: Alconna) -> None:
    garrison = next(option for option in command.options if option.dest == "garrison")
    with command_manager.update(command):
        garrison.add(Option("-n|--count", Args["record_count", int], help_text="限制返回战绩条数"))
    upstream.fetch_garrison = fetch_cached_garrison
    upstream.render_garrison_history = render_limited_garrison
