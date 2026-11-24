"""Translate local command heads without changing the upstream command tree."""

from __future__ import annotations

import re

from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot.exception import SkippedException
from nonebot.typing import T_State
from nonebot_plugin_alconna import Alconna, Arparma, Extension, Text, UniMessage
from nonebot_plugin_alconna.extension import TM, OutputType

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

        if res.matched and "garrison" in res.subcommands:
            count = res.query("garrison.count.record_count")
            error = None
            if count is not None:
                if count <= 0:
                    error = "查询条数必须是正整数，例如 #sk 卫 -n 10。"
                elif res.find("garrison.all") or res.find("garrison.index"):
                    error = "-n 不能与 -a 或 -i 同时使用。"
            if error:
                await UniMessage(error).send(target=event, bot=bot)
                raise SkippedException

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
        elif action.casefold() in {"绑定", "登录", "登陆", "扫码", "qr", "扫码登录", "扫码绑定"}:
            # Login must work even before upstream startup installs its shortcuts.
            body = "skland qrcode" + body[len(action) :]
        elif action == "方舟":
            body = "skland" + body[len(action) :]
        elif action in {"卫", "卫戍战绩"}:
            arguments = body[len(action) :]
            # QQ numbers have at least five digits and still act as a target.
            if count := re.match(r"\s+([+-]?[0-9]{1,4})(?=\s|$)", arguments):
                arguments = f" -n {count[1]}{arguments[count.end() :]}"
            body = "skland garrison" + arguments
        elif any(action in option.aliases for option in command.options):
            body = "skland " + body

        # Reuse upstream Chinese shortcuts and their role/signing semantics. Only
        # this matcher's copy changes; mentions and the original event stay intact.
        prefix = next((value for value in command.prefixes if isinstance(value, str)), "")
        return UniMessage(prefix + body) + receive[1:]

    async def send_wrapper(self, bot: Bot, event: Event, send: TM) -> TM:
        if isinstance(send, UniMessage) and send.extract_plain_text() == "你还没有绑定森空岛账号":
            return send + "\n请使用 #sk 登录 用森空岛扫码绑定。"
        return send

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
