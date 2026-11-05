from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from nonebot import get_bots, get_driver, get_plugin_config, on_regex, require
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.message import event_preprocessor
from nonebot.permission import SUPERUSER

from .commands import parse_request
from .config import Settings
from .review import daily_preview_pages
from .service import ClassificationService, ImageJob

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

settings = get_plugin_config(Settings)
service = ClassificationService(settings)
driver = get_driver()
driver.on_startup(service.start)
driver.on_shutdown(service.stop)


@event_preprocessor
async def collect_group_images(bot: Bot, event: MessageEvent) -> None:
    if not service.accepting or str(event.user_id) == str(bot.self_id):
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
                "group_id": event.group_id if isinstance(event, GroupMessageEvent) else None,
                "message_type": event.message_type,
                "user_id": event.user_id,
                "message_id": event.message_id,
                "message_time": event.time,
                "received_at": datetime.now(UTC).isoformat(),
            },
        )
    )


@scheduler.scheduled_job(
    "cron",
    minute="*",
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


images = on_regex(r"^图(?=$|[aAbB\s])", permission=SUPERUSER, priority=5, block=True)


@images.handle()
async def request_images(event: PrivateMessageEvent) -> None:
    try:
        category, numbers = parse_request(event.get_plaintext())
    except ValueError as exc:
        await images.finish(str(exc))
    if not numbers:
        pages = daily_preview_pages(
            service.store,
            settings,
            user=str(event.user_id),
            category=category,
            exclude_today=False,
        )
        while (page := await asyncio.to_thread(next, pages, None)) is not None:
            data, text, digests = page
            await images.send(_review_message(data, text))
            await asyncio.to_thread(
                service.store.mark_reviewed, str(event.user_id), digests, datetime.now(UTC)
            )
        await images.finish()
    try:
        selections = await asyncio.to_thread(
            service.store.numbered_images,
            str(event.user_id),
            category,
            numbers,
        )
    except ValueError as exc:
        await images.finish(str(exc))
    failed = []
    for number, entry in selections:
        if entry is None:
            failed.append(str(number))
            continue
        path, record = entry
        try:
            data = await asyncio.to_thread(service.store.image_bytes, path, record)
        except (OSError, ValueError):
            failed.append(str(number))
            continue
        try:
            await images.send(
                MessageSegment.text(f"{category[0]}{category[1]} 第{number}张\n")
                + MessageSegment.image(data)
            )
        except Exception:
            logger.warning("[image_classifier] requested original delivery failed")
            failed.append(str(number))
            continue
        await asyncio.to_thread(
            service.store.mark_reviewed, str(event.user_id), [record["sha256"]], datetime.now(UTC)
        )
    if failed:
        await images.finish(
            f"这些序号不存在、已过期、源图失效或发送失败：{','.join(failed)}。"
            "可发送对应分组重看缩略图。"
        )
    await images.finish()


def _review_message(data: bytes, text: str) -> Message:
    message = Message(text)
    if data:
        message.insert(0, MessageSegment.image(data))
    return message


@scheduler.scheduled_job(
    "cron",
    hour=23,
    minute=0,
    second=0,
    timezone=ZoneInfo("Asia/Shanghai"),
    id="image_classifier_review",
    misfire_grace_time=3600,
    max_instances=1,
)
async def nightly_review() -> None:
    if not settings.enabled:
        return
    bots = [bot for bot in get_bots().values() if isinstance(bot, Bot)]
    if not bots:
        return
    users = [int(user) for user in driver.config.superusers if user.isdecimal()]
    if not users:
        return
    for user in users:
        pages = daily_preview_pages(service.store, settings, user=str(user))
        while (page := await asyncio.to_thread(next, pages, None)) is not None:
            data, text, digests = page
            # A failed page/recipient must not prevent the remaining review sheets.
            for bot in bots:
                try:
                    await bot.send_private_msg(user_id=user, message=_review_message(data, text))
                except Exception:
                    logger.warning("[image_classifier] nightly preview delivery failed")
                    continue
                try:
                    await asyncio.to_thread(
                        service.store.mark_reviewed, str(user), digests, datetime.now(UTC)
                    )
                except Exception:
                    logger.exception("[image_classifier] could not persist review delivery history")
                break
