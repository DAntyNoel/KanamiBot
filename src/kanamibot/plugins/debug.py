"""Superuser-only runtime DEBUG control, independent of group module settings."""

from __future__ import annotations

import re

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from kanamibot.core.timing import debug_enabled, set_debug_mode

__plugin_meta__ = PluginMetadata(
    name="DEBUG 调试开关",
    description="仅超管可即时切换控制台 DEBUG 日志及全插件组件计时，仅当前进程生效。",
    usage="#debug 开启 / 关闭 / 状态（仅超管）",
)

debug_command = on_regex(
    r"^\s*#debug(?:\s+.*)?\s*$",
    flags=re.IGNORECASE,
    permission=SUPERUSER,
    priority=1,
    block=True,
)


@debug_command.handle()
async def handle_debug(event: MessageEvent, matcher: Matcher) -> None:
    parts = event.get_plaintext().strip().split(maxsplit=1)
    action = parts[1].strip().lower() if len(parts) > 1 else "状态"
    if action in {"开启", "开", "on"}:
        set_debug_mode(True)
        await matcher.finish("DEBUG 已开启：控制台将输出调试日志及各组件耗时。仅当前进程生效。")
    elif action in {"关闭", "关", "off"}:
        level = set_debug_mode(False)
        await matcher.finish(f"DEBUG 已关闭，日志级别恢复为 {level}。仅当前进程生效。")
    elif action in {"状态", "status"}:
        await matcher.finish(f"DEBUG 当前{'开启' if debug_enabled() else '关闭'}。")
    else:
        await matcher.finish("用法：#debug 开启 / 关闭 / 状态（仅超管）")
