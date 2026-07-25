from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

from kanamibot.core.group_manager import ModuleRule

__plugin_meta__ = PluginMetadata(
    name="ESBR",
    description="查询《永恒轮回》玩家概览并返回图片。",
    usage="#ER {玩家名}",
)

ER_COMMAND_PATTERN = re.compile(
    r"^\s*#ER(?:\s+(?P<player_name>.*?))?\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)
ERBS_EXECUTABLE_ENV = "ERBS_EXECUTABLE"
ERBS_WORKDIR_ENV = "ERBS_WORKDIR"
DEFAULT_ERBS_EXECUTABLE = "erbs"
ERBS_TIMEOUT_SECONDS = 120.0
MAX_PLAYER_NAME_LENGTH = 64
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ERBSQueryError(RuntimeError):
    def __init__(self, user_message: str, detail: str) -> None:
        super().__init__(detail)
        self.user_message = user_message
        self.detail = detail


def extract_player_name(message: str) -> str:
    match = ER_COMMAND_PATTERN.match(message)
    if match is None:
        return ""
    return (match.group("player_name") or "").strip()


def _failure_message(return_code: int, player_name: str) -> str:
    if return_code == 3:
        return f"未找到玩家「{player_name}」，请检查玩家名后重试。"
    if return_code == 4:
        return "永恒轮回数据源暂时不可用，请稍后重试。"
    if return_code == 5:
        return "玩家概览图片生成失败，请稍后重试。"
    return "玩家概览查询失败，请稍后重试。"


def resolve_erbs_workdir(
    executable: str,
    workdir: str | os.PathLike[str] | None = None,
) -> Path | None:
    configured = workdir or os.getenv(ERBS_WORKDIR_ENV)
    if configured:
        return Path(configured).expanduser()

    resolved_executable = shutil.which(executable)
    executable_path = Path(resolved_executable or executable).expanduser()
    if not executable_path.is_file():
        return None

    for parent in executable_path.resolve().parents:
        if (parent / "assets" / "manifest.json").is_file():
            return parent
    return None


async def render_player_overview(
    player_name: str,
    *,
    executable: str | None = None,
    workdir: str | os.PathLike[str] | None = None,
    timeout: float = ERBS_TIMEOUT_SECONDS,
) -> bytes:
    active_executable = executable or os.getenv(ERBS_EXECUTABLE_ENV, DEFAULT_ERBS_EXECUTABLE)
    active_workdir = resolve_erbs_workdir(active_executable, workdir)
    try:
        process = await asyncio.create_subprocess_exec(
            active_executable,
            "overview",
            player_name,
            "--format",
            "bytes",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=active_workdir,
        )
    except (OSError, ValueError) as exc:
        raise ERBSQueryError(
            "ERBS 查询服务尚未配置，请联系管理员。",
            f"failed to start {active_executable!r}: {exc}",
        ) from exc

    try:
        image, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise ERBSQueryError(
            "玩家概览查询超时，请稍后重试。",
            f"erbs overview timed out after {timeout:g} seconds",
        ) from exc

    if process.returncode != 0:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        detail = (
            f"erbs overview exited with {process.returncode}: "
            f"{stderr_text or '<empty stderr>'}"
        )
        raise ERBSQueryError(
            _failure_message(process.returncode or 1, player_name),
            detail[:1000],
        )

    if not image.startswith(PNG_SIGNATURE):
        raise ERBSQueryError(
            "玩家概览图片生成失败，请稍后重试。",
            f"erbs overview returned invalid PNG data ({len(image)} bytes)",
        )
    return image


esbr_matcher = on_regex(
    ER_COMMAND_PATTERN.pattern,
    flags=re.IGNORECASE | re.DOTALL,
    priority=8,
    block=True,
    rule=ModuleRule("esbr"),
)


@esbr_matcher.handle()
async def handle_esbr(event: MessageEvent, matcher: Matcher) -> None:
    player_name = extract_player_name(event.message.extract_plain_text())
    if not player_name:
        await matcher.finish("用法：#ER {玩家名}")
    if len(player_name) > MAX_PLAYER_NAME_LENGTH or "\x00" in player_name:
        await matcher.finish(f"玩家名不能超过 {MAX_PLAYER_NAME_LENGTH} 个字符。")

    await matcher.send(f"正在查询玩家「{player_name}」，请稍候……")
    try:
        image = await render_player_overview(player_name)
    except ERBSQueryError as exc:
        logger.warning("[esbr] overview query failed: {}", exc.detail)
        await matcher.finish(exc.user_message)
    await matcher.finish(MessageSegment.image(image))
