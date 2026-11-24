from __future__ import annotations

from argparse import Namespace
from typing import Annotated, NoReturn

from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import ParserExit
from nonebot.matcher import Matcher
from nonebot.params import (
    CommandArg,
    CommandStart,
    CommandWhitespace,
    Depends,
    RawCommand,
    ShellCommandArgs,
)
from nonebot.rule import Rule

CommandArgs = Annotated[Message | None, CommandArg()]


async def _is_bare_chinese_alias(
    start: Annotated[str | None, CommandStart()],
    raw_command: Annotated[str | None, RawCommand()],
) -> bool:
    return start == "" and raw_command is not None and not raw_command.isascii()


BareChineseAlias = Annotated[bool, Depends(_is_bare_chinese_alias)]


async def _has_command_boundary(
    bare_alias: BareChineseAlias,
    args: CommandArgs,
    whitespace: Annotated[str | None, CommandWhitespace()],
) -> bool:
    return not bare_alias or bool(whitespace) or not (args and args.extract_plain_text())


ALIAS_BOUNDARY = Rule(_has_command_boundary)


async def _get_shell_args(
    matcher: Matcher, args: Annotated[Namespace | ParserExit, ShellCommandArgs()]
) -> Namespace:
    if isinstance(args, ParserExit):
        matcher.block = False
        await matcher.finish()
    return args


ShellArgs = Annotated[Namespace, Depends(_get_shell_args)]


async def finish_command_failure(matcher: Matcher, message: str, *, quiet: bool) -> NoReturn:
    if quiet:
        matcher.block = False
    await matcher.finish(None if quiet else message)
