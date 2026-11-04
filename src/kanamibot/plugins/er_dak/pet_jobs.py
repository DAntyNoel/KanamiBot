"""OneBot lifecycle and command adapter for persistent group tracking."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from nonebot import get_bots, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.log import logger

from kanamibot.core.check_perm import LevelAdmin
from kanamibot.core.group_manager import group_config
from kanamibot.core.paths import DATA_DIR

from .group_pet import PetTracker
from .match_detail import MatchDetailRenderer, PetSource, detail_card
from .pet_store import PetStore

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

_tracker = None
_renderer = None
_poll_tasks = set()

HELP = (
    "群宠订阅可追踪玩家的新对局，并推送结算图。\n"
    "#BS 群宠 添加 <玩家名>\n#BS 群宠 列表\n#BS 群宠 取消 <玩家名>\n"
    "添加、取消仅限群管理员；每群最多 3 人，有效期 7 天，重复添加不续期。\n"
    "首次不补发历史，每次只推最新一局；昵称含空格请加双引号。"
)


def enabled(bot, group):
    return bot in get_bots() and group_config.is_module_enabled(str(group), "er_dak")


async def get_tracker():
    global _tracker, _renderer
    if _tracker is None:
        from . import _get_runtime

        runtime = await _get_runtime()
        # No await after initialization: concurrent callers cannot create separate stores/workers.
        if _tracker is None:
            _renderer = MatchDetailRenderer(runtime.client.config)

            async def render(snapshot):
                card = await detail_card(
                    runtime.client, snapshot.nickname, snapshot.season, snapshot.match_id
                )
                return await _renderer.render(card)

            async def send(bot, group, text, image):
                message = MessageSegment.text(text)
                if image is not None:
                    message += MessageSegment.image(image)
                await get_bots()[bot].send_group_msg(group_id=group, message=message)

            source = PetSource(runtime.client)
            _tracker = PetTracker(
                PetStore(DATA_DIR / "er_dak" / "group-pets.sqlite3"),
                source,
                render,
                send,
                enabled,
                subscription_source=source.for_subscription,
            )
    return _tracker


async def command(bot: Bot, event, args):
    if not isinstance(event, GroupMessageEvent):
        return "群宠订阅仅支持在群聊中管理。"
    if not args or args == ("帮助",):
        return HELP
    action = args[0]
    if action not in {"添加", "订阅", "取消", "列表"}:
        return HELP
    if action != "列表" and not await LevelAdmin(bot, event):
        return "请让群主或群管理员来添加、取消订阅。"
    if (action == "列表" and len(args) != 1) or (action != "列表" and len(args) != 2):
        return HELP
    tracker = await get_tracker()
    bot_id, group = str(bot.self_id), event.group_id
    if action == "列表":
        rows = tracker.store.rows(bot_id, group)
        visible = [r for r in rows if r["state"] in {"active", "missing"}]
        if not visible:
            return "本群还没有群宠订阅。\n" + HELP
        now = time.time()
        lines = ["本群群宠订阅："]
        for row in visible:
            remaining = max(0, int(row["expires"] - now))
            status = (
                f"剩余 {remaining // 86400} 天 {remaining % 86400 // 3600} 小时"
                if remaining
                else "已到期，等待发送提醒"
            )
            if row["state"] == "missing":
                status = "已停止：上游确认玩家不存在，可取消后重新添加"
            due = datetime.fromtimestamp(row["next_check"], timezone(timedelta(hours=8)))
            last = (
                datetime.fromtimestamp(row["last_time"], timezone(timedelta(hours=8))).strftime(
                    "%m-%d %H:%M"
                )
                if row["last_time"]
                else "暂无记录"
            )
            lines.append(
                f"{row['nickname']} · {status}\n"
                f"最近对局 {last} · 下次检查 {due:%m-%d %H:%M}\n"
                f"累计推送 {row['pushes']} 次" + (f" · {row['error']}" if row["error"] else "")
            )
        return "\n\n".join(lines)
    if action == "取消":
        removed = tracker.store.remove(bot_id, group, args[1])
        return f"已取消「{args[1]}」的订阅。" if removed else "本群没有这位玩家的有效订阅。"
    added = await tracker.subscribe(bot_id, group, event.user_id, args[1])
    return (
        f"已添加「{args[1]}」的群宠订阅，有效期 7 天，首次不补发历史对局。"
        if added
        else "这位玩家已在本群订阅中，不会重复订阅或延长有效期。"
    )


@scheduler.scheduled_job(
    "interval",
    seconds=30,
    id="er_dak_group_pets",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=30,
)
async def poll():
    # Do not open network clients or create a database just because the plugin loaded.
    if _tracker is None and not (DATA_DIR / "er_dak" / "group-pets.sqlite3").exists():
        return
    if not get_bots():
        return
    task = asyncio.current_task()
    _poll_tasks.add(task)
    try:
        tracker = await get_tracker()
        for bot_id in list(get_bots()):
            await tracker.tick(bot_id)
    except Exception:
        logger.exception("[er_dak] group pet polling failed")
    finally:
        _poll_tasks.discard(task)


async def shutdown():
    # Stop polling before the shared HTTP client is closed.
    if scheduler.get_job("er_dak_group_pets"):
        scheduler.remove_job("er_dak_group_pets")
    tasks = list(_poll_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    if _renderer is not None:
        await _renderer.close()
