from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from bilibili_api.user import name2uid
from nonebot import on_command, on_regex
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.log import logger
from nonebot.permission import SUPERUSER

from kanamibot.core import LevelAdmin, check_permission, get_first_superuser
from kanamibot.core.group_manager import ModuleRule
from kanamibot.core.utils.message_builder import build_forward_message

from .credential import get_credential, qrlogin_check, qrlogin_get_qrcode
from .dynamic import get_dynamic_by_uids, query_dynamic
from .dynamic_parser import parse_dynamic, parse_raw_dynamic
from .live import query_live_statuses
from .settings import LOGIN_COOLDOWN_SECONDS, MANUAL_MAX_PAGES
from .store import (
    active_subscriptions,
    add_group_subscription,
    find_subscription,
    get_subscriptions,
    remove_group_subscription,
)

LOGIN_LOCK = asyncio.Lock()
LAST_LOGIN_ATTEMPT = 0.0

ADD_SUB_PATTERN = r"^(关注|add sub)\s+(\S+)\s*$"
DEL_SUB_PATTERN = r"^(取关|del sub)\s+(\S+)\s*$"
SUB_LIST_PATTERN = r"^(关注列表|sub list)\s*$"
VIEW_DYNAMIC_PATTERN = r"^(查看动态|dynamic|view dynamic)\s+(\S+)\s*(\d*)$"


async def perform_login(bot: Bot) -> None:
    superuser_id = await get_first_superuser()
    if not superuser_id:
        logger.warning("[Bilibili] SUPERUSERS is empty; login QR code cannot be sent.")
        return

    try:
        image_obj, qr_obj = await qrlogin_get_qrcode()
    except Exception as exc:
        logger.warning("[Bilibili] Failed to get login QR code: %s", exc)
        return

    try:
        await bot.send_private_msg(
            user_id=superuser_id,
            message=Message(
                MessageSegment.text(
                    "检测到 Bilibili 未登录或凭证失效，请扫描二维码登录（5分钟内有效）：\n"
                )
                + MessageSegment.image(image_obj.content)
            ),
        )
    except Exception as exc:
        logger.warning("[Bilibili] Failed to send login QR code to %s: %s", superuser_id, exc)
        return

    try:
        success, info = await asyncio.wait_for(qrlogin_check(qr_obj), timeout=300)
    except TimeoutError:
        await bot.send_private_msg(user_id=superuser_id, message="登录操作超时（已超过5分钟）。")
        return
    except Exception as exc:
        await bot.send_private_msg(user_id=superuser_id, message=f"登录过程发生异常：{exc}")
        return

    if success:
        await bot.send_private_msg(user_id=superuser_id, message="登录成功！Cookies 已保存。")
    else:
        await bot.send_private_msg(user_id=superuser_id, message=f"登录失败：{info}")


async def trigger_login_if_needed(bot: Bot) -> bool:
    global LAST_LOGIN_ATTEMPT

    current_time = time.time()
    if LOGIN_LOCK.locked() or current_time - LAST_LOGIN_ATTEMPT <= LOGIN_COOLDOWN_SECONDS:
        return False

    LAST_LOGIN_ATTEMPT = current_time
    asyncio.create_task(run_login_task(bot))
    return True


async def run_login_task(bot: Bot) -> None:
    async with LOGIN_LOCK:
        await perform_login(bot)


async def resolve_uid(target: str) -> int | None:
    cached = find_subscription(target)
    if cached:
        return int(cached[0])

    if target.isdigit():
        return int(target)

    try:
        result = await name2uid(target)
    except Exception as exc:
        logger.warning("[Bilibili] Failed to resolve uid by name %s: %s", target, exc)
        return None

    uid_list = result.get("uid_list") if isinstance(result, dict) else None
    if not uid_list:
        return None
    return int(uid_list[0]["uid"])


async def resolve_display_name(uid: int, fallback: str) -> str:
    if cached := get_subscriptions().get(str(uid)):
        return cached["name"]

    try:
        live_statuses = await query_live_statuses([uid])
    except Exception:
        live_statuses = {}
    if uid in live_statuses and live_statuses[uid].name:
        return live_statuses[uid].name

    return fallback if not fallback.isdigit() else str(uid)


async def send_dynamic_message(bot: Bot, group_id: int, msg: Any) -> None:
    if isinstance(msg, Message):
        await bot.send_group_msg(group_id=group_id, message=msg)
        return

    await bot.call_api(
        "send_group_forward_msg",
        group_id=group_id,
        messages=build_forward_message(msg),
    )


cmd_login = on_command("bili_login", permission=SUPERUSER, priority=1, block=True)


@cmd_login.handle()
async def handle_login(bot: Bot) -> None:
    if LOGIN_LOCK.locked():
        await cmd_login.finish("正在进行登录流程，请查看私聊或稍后再试。")

    await cmd_login.send("开始获取登录二维码...")
    async with LOGIN_LOCK:
        await perform_login(bot)


add_sub = on_regex(ADD_SUB_PATTERN, rule=ModuleRule("bilibili"), priority=5, block=True)


@add_sub.handle()
async def handle_add_sub(bot: Bot, event: GroupMessageEvent) -> None:
    if not await check_permission(bot, event, LevelAdmin):
        await add_sub.finish("只有管理员可以操作关注喵~")

    match = re.search(ADD_SUB_PATTERN, event.get_plaintext().strip())
    if not match:
        return

    target = match.group(2)
    uid = await resolve_uid(target)
    if uid is None:
        await add_sub.finish("无法找到该UP主，请检查UID或名称喵")

    credential = await get_credential()
    if not credential:
        await trigger_login_if_needed(bot)
        await add_sub.finish("Bot 未登录 Bilibili，无法关注")

    name = await resolve_display_name(uid, target)
    last_dynamic_id = 0
    try:
        dynamics = await get_dynamic_by_uids(credential, [uid])
        if dynamics:
            name = dynamics[0]["name"] or name
            last_dynamic_id = int(dynamics[0]["id"])
    except Exception as exc:
        logger.warning("[Bilibili] UID validation failed for %s: %s", uid, exc)
        await add_sub.finish("UID 有效性检查失败")

    added = add_group_subscription(
        uid,
        name=name,
        group_id=event.group_id,
        dynamic_id=last_dynamic_id,
    )
    if not added:
        await add_sub.finish(f"{name}({uid}) 已经关注了喵！")

    await add_sub.finish(f"添加关注 {name}({uid}) 成功喵！")


del_sub = on_regex(DEL_SUB_PATTERN, rule=ModuleRule("bilibili"), priority=5, block=True)


@del_sub.handle()
async def handle_del_sub(bot: Bot, event: GroupMessageEvent) -> None:
    if not await check_permission(bot, event, LevelAdmin):
        await del_sub.finish("无权操作")

    match = re.search(DEL_SUB_PATTERN, event.get_plaintext().strip())
    if not match:
        return

    target = match.group(2)
    subscription = find_subscription(target)
    if not subscription:
        await del_sub.finish("本群没有关注这个UP主哦")

    uid, info = subscription
    removed = remove_group_subscription(uid, event.group_id)
    if not removed:
        await del_sub.finish("本群没有关注这个UP主哦")

    await del_sub.finish(f"取关 {info['name']} 成功喵")


sub_list = on_regex(SUB_LIST_PATTERN, rule=ModuleRule("bilibili"), priority=5, block=True)


@sub_list.handle()
async def handle_sub_list(event: GroupMessageEvent) -> None:
    items = [
        f"{info['name']}({uid})"
        for uid, info in active_subscriptions().items()
        if int(event.group_id) in info["groups"]
    ]

    if not items:
        await sub_list.finish("本群还未关注任何B站up哦~")

    await sub_list.finish("本群关注列表：\n" + "\n".join(items))


manual_check_matcher = on_command("更新动态", rule=ModuleRule("bilibili"), priority=5, block=True)


@manual_check_matcher.handle()
async def handle_manual_check() -> None:
    from .jobs import check_bili_update

    logger.info("[Bilibili] Manual dynamic update requested.")
    await check_bili_update()
    await manual_check_matcher.finish("动态更新检查完成。")


view_dynamic = on_regex(VIEW_DYNAMIC_PATTERN, rule=ModuleRule("bilibili"), priority=5, block=True)


@view_dynamic.handle()
async def handle_view_dynamic(bot: Bot, event: GroupMessageEvent) -> None:
    if not await check_permission(bot, event, LevelAdmin):
        await view_dynamic.finish("只有管理员可以使用此功能喵~")

    match = re.search(VIEW_DYNAMIC_PATTERN, event.get_plaintext().strip())
    if not match:
        return

    target = match.group(2)
    dynamic_index = int(match.group(3) or "1")
    if dynamic_index <= 0:
        await view_dynamic.finish("动态序号需要大于 0")

    uid = await resolve_uid(target)
    if uid is None:
        await view_dynamic.finish("未找到该UP主，请输入订阅列表中的名称或直接输入UID")

    name = await resolve_display_name(uid, target)
    credential = await get_credential()
    if not credential:
        await view_dynamic.finish("Bot 未登录，无法查询动态")

    receipt = await view_dynamic.send(f"正在查询 {name}({uid}) 的第 {dynamic_index} 条动态...")
    message_id = receipt.get("message_id") if isinstance(receipt, dict) else None

    try:
        raw_dynamic = await query_dynamic(
            uid,
            credential,
            dynamic_index,
            max_pages=MANUAL_MAX_PAGES,
        )
        if not raw_dynamic:
            await view_dynamic.send("未找到对应的动态（可能翻页过深或没有更多动态了）")
            return

        parsed = parse_raw_dynamic(raw_dynamic)
        if not parsed:
            await view_dynamic.send("动态解析失败或不支持的类型")
            return

        msg = parse_dynamic(parsed, manual=True)
        if not msg:
            await view_dynamic.send("动态解析失败或不支持的类型")
            return

        await send_dynamic_message(bot, event.group_id, msg)
    except Exception as exc:
        logger.exception("[Bilibili] Failed to query dynamic.")
        await view_dynamic.send(f"查询出错: {exc}")
    finally:
        if message_id is not None:
            try:
                await bot.delete_msg(message_id=message_id)
            except Exception:
                logger.debug("[Bilibili] Failed to recall dynamic query prompt.", exc_info=True)
