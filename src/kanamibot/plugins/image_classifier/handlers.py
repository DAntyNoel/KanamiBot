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

from .commands import REQUEST_PATTERN, parse_request
from .config import Settings
from .exclusions import exclude_event, is_command_message, replied_message_id
from .review import collection_overview, daily_preview_pages, image_details, nightly_preview_batches
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
    if not service.accepting:
        return
    if exclude_event(bot, event):
        if is_command_message(event.original_message) and (reply_id := replied_message_id(event)):
            await asyncio.to_thread(
                service.store.exclude_source,
                {
                    "bot_id": str(bot.self_id),
                    "group_id": event.group_id if isinstance(event, GroupMessageEvent) else None,
                    "user_id": event.user_id,
                    "message_id": reply_id,
                },
            )
        return
    # Exclude command traffic before queueing: no download, inference or temporary record.
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
                "sender_name": event.sender.card or event.sender.nickname
                if isinstance(event, GroupMessageEvent)
                else event.sender.nickname,
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


async def _superuser_private(bot: Bot, event: MessageEvent) -> bool:
    return isinstance(event, PrivateMessageEvent) and await SUPERUSER(bot, event)


images = on_regex(REQUEST_PATTERN, permission=_superuser_private, priority=5, block=True)


@images.handle()
async def request_images(event: PrivateMessageEvent) -> None:
    text = event.get_plaintext().strip()
    if text.lower() in {"images", "所有图"}:
        await images.finish(await asyncio.to_thread(collection_overview, service.store))
    try:
        category, numbers = parse_request(text)
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
            await asyncio.to_thread(service.store.mark_previewed, str(event.user_id), digests)
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
                MessageSegment.text(
                    f"{category[0]}{category[1]} 第{number}张\n{image_details(record)}\n"
                )
                + MessageSegment.image(data)
            )
        except Exception:
            logger.warning("[image_classifier] requested original delivery failed")
            failed.append(str(number))
            continue
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
        batches = nightly_preview_batches(service.store, settings, user=str(user))
        while (batch := await asyncio.to_thread(next, batches, None)) is not None:
            category, pages = batch
            digests = [digest for _, _, items in pages for digest in items]
            # Only mark the sheets in a successfully delivered forward message.
            for bot in bots:
                nodes = [
                    MessageSegment.node_custom(
                        user_id=bot.self_id,
                        nickname=f"图片审查 {category[0]}{category[1]}",
                        content=_review_message(data, text),
                    )
                    for data, text, _ in pages
                ]
                try:
                    await bot.call_api("send_private_forward_msg", user_id=user, messages=nodes)
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
