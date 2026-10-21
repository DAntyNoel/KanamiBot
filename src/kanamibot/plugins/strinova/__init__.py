from __future__ import annotations

import asyncio
import os
import re

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

from kanamibot.core.group_manager import ModuleRule
from kanamibot.strinova.client import Settings
from kanamibot.strinova.query import QueryError, parse_query
from kanamibot.strinova.render import render_png
from kanamibot.strinova.service import QUERY_TIMEOUT_SECONDS, check_available, create_report

USAGE = (
    "#卡丘战绩 {昵称或ID}（默认玩家概览）\n"
    "#卡丘战绩 {昵称或ID} 对局战绩\n"
    "#卡丘战绩 {昵称或ID} 2（最近战绩第 2 页）\n"
    "#卡丘战绩 {昵称或ID} 排位 2（筛选排位爆破并翻页）\n"
    "#卡丘战绩 {昵称或ID} 对局战绩 2\n"
    "#卡丘战绩 {昵称或ID} 排位\n"
    "模式可填完整名称，或排位 / 枪王 / 生化等简称\n"
    "界面可选：玩家概览 / 引航者资料 / 当前状态 / 生涯数据 / 历史赛季 / 单局详情 / 玩家名片\n"
    "也支持：#卡丘 / #卡丘资料 / #卡丘状态 / #卡丘生涯 {昵称或ID}\n"
    "#卡丘赛季列表（查询可用赛季）\n"
    "#卡丘赛季 {昵称或ID}（默认最近已结束主赛季）\n"
    "#卡丘赛季 {昵称或ID} --season-id 17\n"
    "#卡丘名片 {昵称或ID}\n"
    "#卡丘录像 {房间ID}\n"
    "#卡丘对局 {昵称或ID} --room-id {房间ID}\n"
    "可选：--player-id ID / --nick 昵称 / --mode 排位爆破 / --page 2 / --limit 8 / "
    "--game-mode 0 / --room-mode-type 0 / --play-mode 0 / --season-type 1"
)
ENABLED = os.getenv("KANAMIBOT_STRINOVA_ENABLED", "true").lower() in ("1", "true", "yes", "on")
__plugin_meta__ = PluginMetadata(
    name="卡拉彼丘战绩",
    description="通过 Strinova API 查询玩家资料、生涯、赛季与对局，并生成图片。"
    + ("" if ENABLED else "当前未启用，需设置 KANAMIBOT_STRINOVA_ENABLED=true。"),
    usage=USAGE,
)
COMMAND = re.compile(
    r"^\s*#(?P<command>卡丘(?:资料|状态|生涯|战绩|赛季列表|赛季|对局|名片|录像)?|KQ)"
    r"(?:\s+(?P<args>.*))?\s*$",
    re.IGNORECASE | re.DOTALL,
)
COMMAND_VIEWS = {
    "卡丘资料": "profile",
    "卡丘状态": "status",
    "卡丘生涯": "career",
    "卡丘赛季": "history",
    "卡丘对局": "match",
    "卡丘名片": "card",
    "卡丘赛季列表": "seasons",
    "卡丘录像": "replay",
}
query_gate = asyncio.Semaphore(2)


async def handle_strinova(event: MessageEvent, matcher: Matcher, bot: Bot) -> None:
    match = COMMAND.fullmatch(event.message.extract_plain_text())
    if match is None:
        return
    try:
        settings = Settings.from_env()
        await check_available(settings)
    except QueryError as exc:
        await matcher.finish(str(exc))
    args = (match.group("args") or "").strip()
    view = COMMAND_VIEWS.get(match.group("command"), "overview")
    if (not args and view != "seasons") or args in ("帮助", "help", "--help"):
        await matcher.finish(USAGE)
    if view == "replay" and args.isascii() and args.isdecimal():
        args = f"--room-id {args}"
    try:
        query = parse_query(args, view=view)
    except QueryError as exc:
        await matcher.finish(f"{exc}\n\n{USAGE}")
    if query_gate.locked():
        await matcher.finish("战绩查询正在忙，请稍后再试。")
    async with query_gate:
        target = query.target or ("赛季目录" if view == "seasons" else query.room_id)
        receipt = await matcher.send(f"正在查询「{target}」的卡丘战绩……")
        message_id = receipt.get("message_id") if isinstance(receipt, dict) else None
        try:
            async with asyncio.timeout(QUERY_TIMEOUT_SECONDS):
                report = await create_report(query, settings)
                image = await render_png(report, settings)
        except QueryError as exc:
            logger.warning("[strinova] query failed: {}", str(exc))
            await matcher.finish(str(exc))
        except TimeoutError:
            await matcher.finish("战绩查询已超过 2 分钟，已取消本次查询，请稍后重试。")
        except Exception:
            logger.exception("[strinova] unexpected query or rendering failure")
            await matcher.finish("战绩查询或图片生成失败，请稍后重试。")
        finally:
            if message_id is not None:
                try:
                    async with asyncio.timeout(5):
                        await bot.delete_msg(message_id=message_id)
                except Exception as exc:
                    # A failed QQ recall must not replace the result or leak the query slot.
                    logger.warning(
                        "[strinova] failed to recall query prompt: {}", type(exc).__name__
                    )
    result = MessageSegment.image(image)
    replay_url = report.sections.get("replay", {}).get("download_url")
    if replay_url:
        result = result + MessageSegment.text(f"\n录像下载地址：{replay_url}")
    await matcher.finish(result)


if ENABLED:
    strinova_matcher = on_regex(
        COMMAND.pattern, flags=COMMAND.flags, priority=8, block=True, rule=ModuleRule("strinova")
    )
    strinova_matcher.handle()(handle_strinova)
