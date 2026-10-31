"""Defer upstream resource I/O without delaying shortcut initialization."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from nonebot import get_driver, logger
from nonebot_plugin_skland import hook

from kanamibot.core.timing import timed


class BackgroundResources:
    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def data_pending(self) -> bool:
        task = self.tasks.get("数据")
        return task is not None and not task.done()

    def schedule(self, name: str, call: Callable[[], Awaitable[Any]]) -> None:
        if (task := self.tasks.get(name)) is not None and not task.done():
            return

        @timed()
        async def run() -> None:
            try:
                result = await call()
                if getattr(result, "failed", False) or getattr(result, "failed_count", 0):
                    logger.warning(f"森空岛{name}后台更新部分失败，请查看资源更新日志")
                else:
                    logger.info(f"森空岛{name}后台更新完成")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(f"森空岛{name}后台更新失败")

        self.tasks[name] = asyncio.create_task(run(), name=f"skland-resources-{name}")
        logger.info(f"森空岛{name}资源已安排后台更新")

    async def shutdown(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()


resources = BackgroundResources()


def install_background_resources() -> None:
    # Only replace the references used by the startup hook. Manual and scheduled
    # sync keep their original awaited update functions and upstream update lock.
    update_data = hook.update_data_resources
    download_images = hook.download_img_resource

    async def defer_data(**kwargs: Any) -> None:
        resources.schedule("数据", lambda: update_data(**kwargs))

    async def defer_images(**kwargs: Any) -> None:
        resources.schedule("图片", lambda: download_images(**kwargs))

    hook.update_data_resources = defer_data
    hook.download_img_resource = defer_images
    get_driver().on_shutdown(resources.shutdown)
