from __future__ import annotations

import asyncio
from collections import Counter
from typing import Annotated, Any

from nonebot import get_bot, on_command, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.log import logger
from nonebot.message import event_preprocessor
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from kanamibot.core.chat_history import (
    get_today_group_ids,
    get_today_logs,
    record_group_message,
)
from kanamibot.core.group_manager import ModuleRule, group_config

from .config import get_config_status_text, get_group_config, update_group_switch
from .wordcloud import gen_wc

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

MODULE_NAME = "daily_report"
GROUP_RULE = ModuleRule(MODULE_NAME)
MIN_AUTO_REPORT_MESSAGES = 40
CommandArgs = Annotated[Message, CommandArg()]

__plugin_meta__ = PluginMetadata(
    name="DailyReport",
    description="群聊日报、龙王/复读统计和词云。",
    usage=(
        "今日总结 / 日报：手动生成本群日报\n"
        "日报设置 查看\n"
        "日报设置 开启 词云 / 日报设置 关闭 龙王"
    ),
)

_log_tasks: set[asyncio.Task[None]] = set()


def _track_log_task(task: asyncio.Task[None]) -> None:
    _log_tasks.add(task)
    task.add_done_callback(_finish_log_task)


def _finish_log_task(task: asyncio.Task[None]) -> None:
    _log_tasks.discard(task)
    try:
        task.result()
    except Exception as exc:
        logger.warning("[daily_report] failed to record group message: %s", exc)


@event_preprocessor
async def record_group_chat_history(bot: Bot, event: GroupMessageEvent) -> None:
    if str(event.user_id) == str(bot.self_id):
        return

    content = event.get_plaintext().strip()
    if not content:
        return

    task = asyncio.create_task(
        asyncio.to_thread(record_group_message, event.group_id, event.user_id, content)
    )
    _track_log_task(task)


def _count_repeaters(logs: list[tuple[str, str]]) -> Counter[str]:
    repeater_scores: Counter[str] = Counter()
    last_content: str | None = None
    for user_id, content in logs:
        if content and last_content is not None and content == last_content:
            repeater_scores[user_id] += 1
        last_content = content
    return repeater_scores


async def generate_report(
    group_id: int,
    *,
    include_wordcloud: bool = True,
) -> dict[str, Any] | None:
    logs = await asyncio.to_thread(get_today_logs, group_id)
    if not logs:
        return None

    user_ids = [row[0] for row in logs]
    contents = [row[1] for row in logs]
    message_counts = Counter(user_ids)
    repeater_scores = _count_repeaters(logs)

    top_talker = message_counts.most_common(1)[0] if message_counts else (None, 0)
    top_repeater = repeater_scores.most_common(1)[0] if repeater_scores else (None, 0)
    wordcloud = await asyncio.to_thread(gen_wc, contents) if include_wordcloud else b""

    return {
        "top_talker": top_talker,
        "top_repeater": top_repeater,
        "wordcloud": wordcloud,
        "total_msgs": len(logs),
    }


def _report_module_enabled(group_id: int) -> bool:
    return group_config.is_module_enabled(str(group_id), MODULE_NAME)


async def send_group_report_logic(bot: Bot, group_id: int, *, is_manual: bool = False) -> None:
    if not _report_module_enabled(group_id):
        if is_manual:
            await bot.send_group_msg(group_id=group_id, message="本群日报功能已关闭。")
        return

    cfg = get_group_config(group_id)
    if not any(cfg.values()):
        if is_manual:
            await bot.send_group_msg(group_id=group_id, message="本群日报显示项都已关闭。")
        return

    report = await generate_report(group_id, include_wordcloud=cfg.get("show_wordcloud", True))
    if not report:
        if is_manual:
            await bot.send_group_msg(group_id=group_id, message="今日暂无聊天记录，无法生成总结。")
        return

    total_msgs = int(report["total_msgs"])
    if total_msgs < MIN_AUTO_REPORT_MESSAGES:
        if not is_manual:
            return
        await bot.send_group_msg(
            group_id=group_id,
            message=f"今日消息量不足 {MIN_AUTO_REPORT_MESSAGES} 条，先图个乐。",
        )

    msg = Message()
    msg.append(MessageSegment.text("【今日群聊总结】\n"))

    if cfg.get("show_total", True):
        msg.append(MessageSegment.text(f"今日消息总量：{total_msgs}\n"))
        msg.append(MessageSegment.text("----------------\n"))

    if cfg.get("show_talker", True):
        msg.append(MessageSegment.text("今日龙王："))
        talker_id, talker_count = report["top_talker"]
        if talker_id:
            msg.append(MessageSegment.at(talker_id))
            msg.append(MessageSegment.text(f"（发言 {talker_count} 条）\n"))
        else:
            msg.append(MessageSegment.text("暂无\n"))

    if cfg.get("show_repeater", True):
        msg.append(MessageSegment.text("人类本质："))
        repeater_id, repeater_count = report["top_repeater"]
        if repeater_id and repeater_count > 0:
            msg.append(MessageSegment.at(repeater_id))
            msg.append(MessageSegment.text(f"（跟随复读 {repeater_count} 次）\n"))
        else:
            msg.append(MessageSegment.text("暂无\n"))

    if (cfg.get("show_talker", True) or cfg.get("show_repeater", True)) and cfg.get(
        "show_wordcloud", True
    ):
        msg.append(MessageSegment.text("----------------\n"))

    if cfg.get("show_wordcloud", True):
        wordcloud = report.get("wordcloud")
        if wordcloud:
            msg.append(MessageSegment.text("今日词云：\n"))
            msg.append(MessageSegment.image(wordcloud))
        else:
            msg.append(MessageSegment.text("今日词云：暂无可用热词\n"))

    await bot.send_group_msg(group_id=group_id, message=msg)


setting_cmd = on_command(
    "日报设置",
    aliases={"设置日报"},
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    priority=5,
    block=True,
    rule=GROUP_RULE,
)


@setting_cmd.handle()
async def handle_daily_report_setting(event: GroupMessageEvent, args: CommandArgs) -> None:
    argv = args.extract_plain_text().strip().split()
    group_id = event.group_id

    if not argv or argv[0] in {"查看", "状态", "status"}:
        await setting_cmd.finish(get_config_status_text(group_id))

    if len(argv) < 2:
        await setting_cmd.finish(
            "指令格式：\n"
            "日报设置 查看\n"
            "日报设置 开启 词云\n"
            "日报设置 关闭 龙王"
        )

    action = argv[0]
    target = argv[1]
    if action not in {"开启", "打开", "启用", "关闭", "停止", "禁用"}:
        await setting_cmd.finish("动作仅支持：开启、关闭。")

    success, result_msg = update_group_switch(group_id, target, action in {"开启", "打开", "启用"})
    if not success:
        await setting_cmd.finish(result_msg)
    await setting_cmd.finish(result_msg)


manual_report = on_command(
    "今日总结",
    aliases={"日报", "daily_report"},
    priority=5,
    block=True,
    rule=GROUP_RULE,
)


@manual_report.handle()
async def handle_manual_report(bot: Bot, event: GroupMessageEvent) -> None:
    await manual_report.send("正在生成今日总结，请稍候...")
    await send_group_report_logic(bot, int(event.group_id), is_manual=True)


@scheduler.scheduled_job(
    "cron",
    hour=23,
    minute=30,
    id="daily_report_summary",
)
async def run_daily_summary() -> None:
    try:
        bot = get_bot()
    except ValueError:
        logger.warning("[daily_report] scheduled summary skipped because no bot is available.")
        return

    group_ids = await asyncio.to_thread(get_today_group_ids)
    for group_id in group_ids:
        try:
            await send_group_report_logic(bot, int(group_id), is_manual=False)
        except Exception as exc:
            logger.exception(
                "[daily_report] failed to send summary for group %s: %s",
                group_id,
                exc,
            )
