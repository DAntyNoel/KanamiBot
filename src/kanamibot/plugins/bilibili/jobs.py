from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from nonebot import get_bot
from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.log import logger
from nonebot_plugin_apscheduler import scheduler

from kanamibot.core.utils.message_builder import build_forward_message

from .credential import get_credential
from .dynamic import get_dynamic_by_uids
from .dynamic_parser import parse_dynamic
from .live import LiveQueryError, parse_live, query_live_statuses
from .settings import (
    DYNAMIC_INTERVAL_MINUTES,
    LIVE_BATCH_SIZE,
    LIVE_INTERVAL_MINUTES,
    REQUEST_DELAY_SECONDS,
)
from .store import active_subscriptions, cleanup_unsubscribed, set_subscription

FIRST_DYNAMIC_CHECK = True
FIRST_LIVE_CHECK = True


@dataclass
class BackoffState:
    failures: int = 0
    next_available_at: float = 0.0

    def can_run(self) -> bool:
        return time.time() >= self.next_available_at

    def succeed(self) -> None:
        self.failures = 0
        self.next_available_at = 0.0

    def fail(self, *, base_minutes: float) -> float:
        self.failures += 1
        delay_minutes = min(60.0, base_minutes * (2 ** (self.failures - 1)))
        self.next_available_at = time.time() + delay_minutes * 60
        return delay_minutes


LIVE_BACKOFF = BackoffState()


def _chunked(values: list[int], size: int) -> list[list[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


async def _send_group_dynamic(bot: Bot, group_id: int, msg) -> None:
    if isinstance(msg, Message):
        await bot.send_group_msg(group_id=group_id, message=msg)
        return

    await bot.call_api(
        "send_group_forward_msg",
        group_id=group_id,
        messages=build_forward_message(msg),
    )


async def _send_group_message(bot: Bot, group_ids: list[int], message: Message) -> None:
    for group_id in group_ids:
        await bot.send_group_msg(group_id=group_id, message=message)
        if REQUEST_DELAY_SECONDS:
            await asyncio.sleep(REQUEST_DELAY_SECONDS)


@scheduler.scheduled_job(
    "interval",
    minutes=DYNAMIC_INTERVAL_MINUTES,
    id="bili_dynamic_push",
    max_instances=1,
    coalesce=True,
)
async def check_bili_update(*, suppress_initial: bool | None = None) -> None:
    global FIRST_DYNAMIC_CHECK
    is_initial_check = FIRST_DYNAMIC_CHECK if suppress_initial is None else suppress_initial

    credential = await get_credential()
    if not credential:
        return

    data = active_subscriptions(reload=True)
    if not data:
        return

    try:
        bot: Bot = get_bot()
    except ValueError:
        return

    for str_uid, info in data.items():
        uid = int(str_uid)
        groups = info["groups"]
        if not groups:
            continue

        try:
            dynamics = await get_dynamic_by_uids(
                credential,
                [uid],
                update_baselines=[info["dynamic"]],
            )
        except Exception as exc:
            logger.warning("[Bilibili] Dynamic check error for uid {}: {}", uid, exc)
            continue

        if not dynamics:
            continue

        current_info = info.copy()
        current_info["dynamic"] = int(dynamics[0]["id"])
        current_info["name"] = dynamics[0].get("name") or current_info["name"]
        set_subscription(str_uid, current_info)

        for dynamic_data in reversed(dynamics):
            if is_initial_check:
                logger.info(
                    "[Bilibili] Initial dynamic for {}: {}",
                    uid,
                    dynamic_data["id"],
                )
                continue

            msg = parse_dynamic(dynamic_data)
            if not msg:
                continue

            for group_id in groups:
                await _send_group_dynamic(bot, group_id, msg)
                if REQUEST_DELAY_SECONDS:
                    await asyncio.sleep(REQUEST_DELAY_SECONDS)

    FIRST_DYNAMIC_CHECK = False
    cleanup_unsubscribed()


@scheduler.scheduled_job(
    "interval",
    minutes=LIVE_INTERVAL_MINUTES,
    id="bili_live_push",
    jitter=45,
    max_instances=1,
    coalesce=True,
)
async def check_live_update() -> None:
    global FIRST_LIVE_CHECK

    if not LIVE_BACKOFF.can_run():
        return

    data = active_subscriptions(reload=True)
    if not data:
        return

    try:
        bot: Bot = get_bot()
    except ValueError:
        return

    uids = [int(uid) for uid in data]
    try:
        for batch in _chunked(uids, LIVE_BATCH_SIZE):
            statuses = await query_live_statuses(batch)
            for uid in batch:
                str_uid = str(uid)
                info = data.get(str_uid)
                if not info:
                    continue

                status = statuses.get(uid)
                if not status:
                    continue

                current_info = info.copy()
                current_info["name"] = status.name or current_info["name"]
                current_info["live_status"] = status.live_status
                set_subscription(str_uid, current_info)

                if FIRST_LIVE_CHECK:
                    continue

                msg = parse_live(status, int(info.get("live_status") or 0))
                if msg:
                    await _send_group_message(bot, info["groups"], msg)

            if REQUEST_DELAY_SECONDS:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
    except LiveQueryError as exc:
        delay = LIVE_BACKOFF.fail(base_minutes=LIVE_INTERVAL_MINUTES)
        level = logger.warning if exc.rate_limited else logger.info
        level("[Bilibili] Live polling backed off for {:.1f} minutes: {}", delay, exc)
        return
    except Exception as exc:
        delay = LIVE_BACKOFF.fail(base_minutes=LIVE_INTERVAL_MINUTES)
        logger.warning(
            "[Bilibili] Live polling failed; backed off for {:.1f} minutes: {}",
            delay,
            exc,
        )
        return

    LIVE_BACKOFF.succeed()
    FIRST_LIVE_CHECK = False
    cleanup_unsubscribed()
