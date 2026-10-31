"""Translate local command heads without changing the upstream command tree."""

from __future__ import annotations

import re

from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot.typing import T_State
from nonebot_plugin_alconna import Alconna, Arparma, Extension, Text, UniMessage
from nonebot_plugin_alconna.extension import OutputType

from kanamibot.core.timing import timed

from .help import render_help_images

COMMAND_HEAD = re.compile(r"^#(?:SK|sk|森空岛)(?=\s|$)\s*")


class SklandCommandExtension(Extension):
    id = "kanamibot.skland.commands"
    priority = 0

    async def parse_wrapper(
        self, bot: Bot, state: T_State, event: Event, res: Arparma
    ) -> None:
        from nonebot_plugin_skland import skland

        from .background import resources

        if resources.data_pending and res.matched and any(
            name in res.subcommands for name in ("gacha", "efgacha", "import", "box")
        ):
            await skland.finish("森空岛数据资源正在后台准备，请稍后重试。")

    @timed()
    async def receive_wrapper(
        self, bot: Bot, event: Event, command: Alconna, receive: UniMessage
    ) -> UniMessage:
        if not receive or not isinstance(receive[0], Text):
            return receive
        if not (head := COMMAND_HEAD.match(receive[0].text)):
            return receive

        body = receive[0].text[head.end() :]
        action = body.split(maxsplit=1)[0] if body.strip() else ""
        if not action or action in {"帮助", "help", "--help", "-h"}:
            body = "skland --help"
        elif action.casefold() in {"登录", "登陆", "扫码", "qr", "扫码登录", "扫码绑定"}:
            # Login must work even before upstream startup installs its shortcuts.
            body = "skland qrcode" + body[len(action) :]
        elif action == "方舟":
            body = "skland" + body[len(action) :]
        elif any(action in option.aliases for option in command.options):
            body = "skland " + body

        # Reuse upstream Chinese shortcuts and their role/signing semantics. Only
        # this matcher's copy changes; mentions and the original event stay intact.
        prefix = next((value for value in command.prefixes if isinstance(value, str)), "")
        return UniMessage(prefix + body) + receive[1:]

    @timed()
    async def output_converter(self, output_type: OutputType, content: str) -> UniMessage:
        if output_type in {"help", "shortcut"}:
            try:
                images = await render_help_images()
                if not images:
                    raise ValueError("empty help image result")
                message = UniMessage()
                for image in images:
                    message += UniMessage.image(raw=image)
                return message
            except Exception:
                logger.exception("森空岛帮助图片生成失败")
                return UniMessage("帮助图片暂时无法生成，请稍后重试。")
        return UniMessage("指令参数有误，请发送 #SK 帮助 查看命令和用法。")
