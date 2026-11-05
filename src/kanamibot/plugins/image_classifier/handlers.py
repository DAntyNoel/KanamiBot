from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from nonebot import get_bots, get_driver, get_plugin_config, on_command, require
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
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from .config import Settings
from .review import daily_preview_pages, preview_page
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


preview = on_command(
    "#图片预览", aliases={"图片预览"}, permission=SUPERUSER, priority=5, block=True
)
save = on_command("#图片保存", aliases={"图片保存"}, permission=SUPERUSER, priority=5, block=True)


@preview.handle()
async def preview_images(
    event: PrivateMessageEvent,
    args: Annotated[Message, CommandArg()],
) -> None:
    try:
        data, text = await asyncio.to_thread(
            preview_page,
            service.store,
            settings,
            int(args.extract_plain_text().strip() or "1"),
            user=str(event.user_id),
        )
    except ValueError as exc:
        await preview.finish(str(exc))
    await preview.finish(_review_message(data, text))


def _review_message(data: bytes, text: str) -> Message:
    message = Message(text)
    if data:
        message.insert(0, MessageSegment.image(data))
    return message


@save.handle()
async def save_images(
    event: PrivateMessageEvent,
    args: Annotated[Message, CommandArg()],
) -> None:
    parts = args.extract_plain_text().split()
    if not 1 <= len(parts) <= 2:
        await save.finish("用法：#图片保存 编号 [目录名]")
    alias = parts[1] if len(parts) == 2 else "default"
    if alias not in settings.saved_dirs:
        await save.finish(f"未知目录，可选：{', '.join(settings.saved_dirs)}")
    try:
        await asyncio.to_thread(service.store.archive, parts[0], settings.saved_dirs[alias])
    except (OSError, ValueError) as exc:
        logger.warning("[image_classifier] archive failed ({})", type(exc).__name__)
        await save.finish("保存失败：图片已过期、源文件已失效，或目录不可写。")
    await save.finish(f"已永久保存到 {alias}。")


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
