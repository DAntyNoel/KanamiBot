from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from nonebot import get_driver, get_plugin_config, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.log import logger
from nonebot.message import event_preprocessor

from .config import Settings
from .service import ClassificationService, ImageJob

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

settings = get_plugin_config(Settings)
service = ClassificationService(settings)
driver = get_driver()
driver.on_startup(service.start)
driver.on_shutdown(service.stop)


@event_preprocessor
async def collect_group_images(bot: Bot, event: GroupMessageEvent) -> None:
    if not service.accepting:
        return
    # Preprocessors see every group, even when a command matcher later blocks the event.
    message = event.original_message
    if not any(segment.type in {"image", "forward", "node"} for segment in message):
        return
    await service.submit(
        ImageJob(
            bot=bot,
            message=message.copy(),
            source={
                "bot_id": str(bot.self_id),
                "group_id": event.group_id,
                "user_id": event.user_id,
                "message_id": event.message_id,
                "message_time": event.time,
                "received_at": datetime.now(UTC).isoformat(),
            },
        )
    )


@scheduler.scheduled_job(
    "cron",
    hour=12,
    minute=0,
    second=0,
    timezone=ZoneInfo("Asia/Shanghai"),
    id="image_classifier_cleanup",
    misfire_grace_time=3600,
)
async def cleanup_expired_results() -> None:
    try:
        count = await asyncio.to_thread(service.store.cleanup)
        logger.info("[image_classifier] removed {} expired image records", count)
    except Exception:
        logger.exception("[image_classifier] retention cleanup failed")
