"""Translate local command heads without changing the upstream command tree."""

from __future__ import annotations

import re

from nonebot.adapters import Bot, Event
from nonebot_plugin_alconna import Alconna, Extension, Text, UniMessage

COMMAND_HEAD = re.compile(r"^#(?:SK|sk|森空岛)(?=\s|$)\s*")


class SklandCommandExtension(Extension):
    id = "kanamibot.skland.commands"
    priority = 0

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
        elif action == "扫码登录":
            body = "扫码绑定" + body[len(action) :]
        elif any(action in option.aliases for option in command.options):
            body = "skland " + body

        # Reuse upstream Chinese shortcuts and their role/signing semantics. Only
        # this matcher's copy changes; mentions and the original event stay intact.
        prefix = next((value for value in command.prefixes if isinstance(value, str)), "")
        return UniMessage(prefix + body) + receive[1:]
