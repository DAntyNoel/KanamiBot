from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from nonebot import on_command as _nonebot_on_command
from nonebot.adapters.onebot.v11 import (
    GROUP_OWNER,
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.internal.matcher import Matcher
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from kanamibot.core.group_manager import ADMIN_PERMISSION, ModuleRule
from kanamibot.core.paths import FILES_DIR

MODULE_NAME = "group_manager"
GROUP_RULE = ModuleRule(MODULE_NAME)
OWNER_PERMISSION = SUPERUSER | GROUP_OWNER

MAX_SPECIAL_TITLE_LENGTH = 6
MAX_GROUP_CARD_LENGTH = 60
MAX_GROUP_NAME_LENGTH = 60
DEFAULT_MUTE_SECONDS = 10 * 60
MAX_MUTE_SECONDS = 30 * 24 * 60 * 60

ENABLED_GROUP_MANAGER_COMMANDS = frozenset(
    {
        "设置头衔",
        "头衔",
        "删头衔",
        "清空头衔",
        "清头衔",
        "删除头衔",
        "禁言",
        "mute",
        "解禁",
        "解除禁言",
        "unmute",
        "修改群名片",
        "改昵称",
        "改名片",
        "清名片",
        "删除名片",
        "清空名片",
    }
)

VIDEO_ROOT = FILES_DIR / "group_manager" / "videos"
UPLOAD_ROOT = FILES_DIR / "group_manager" / "uploads"

CommandArgs = Annotated[Message, CommandArg()]


class _DisabledCommand:
    def handle(self, *args: object, **kwargs: object):
        def decorator(func):
            return func

        return decorator

    async def finish(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("disabled group-manager command was called")


_DISABLED_COMMAND = _DisabledCommand()


def _command_name(command: str | tuple[str, ...]) -> str:
    return " ".join(command) if isinstance(command, tuple) else command


def on_command(
    command: str | tuple[str, ...],
    *args: object,
    aliases: set[str | tuple[str, ...]] | None = None,
    **kwargs: object,
) -> type[Matcher] | _DisabledCommand:
    command_name = _command_name(command)
    if command_name not in ENABLED_GROUP_MANAGER_COMMANDS:
        return _DISABLED_COMMAND

    if aliases is not None:
        kwargs["aliases"] = {
            alias
            for alias in aliases
            if _command_name(alias) in ENABLED_GROUP_MANAGER_COMMANDS
        }

    return _nonebot_on_command(command, *args, **kwargs)

__plugin_meta__ = PluginMetadata(
    name="GroupManager",
    description="群管理与 NapCat 群互动命令。",
    usage=(
        "头衔|设置头衔 [@用户|QQ] <内容>\n"
        "删头衔|清头衔|清空头衔|删除头衔 [@用户|QQ]\n"
        "禁言|mute @用户 [10m] / 解禁|解除禁言|unmute @用户\n"
        "改名片|修改群名片|改昵称 @用户 <名片>\n"
        "清名片|删除名片|清空名片 @用户"
    ),
)


HELP_TEXT = """群管命令：
头衔 [@用户|QQ] <内容>：设置专属头衔，不填目标则设置自己
删头衔 [@用户|QQ]：清空专属头衔
禁言 @用户 [10m] / 解禁 @用户：禁言或解除禁言，时长支持 s/m/h/d、秒/分/小时/天
改名片 @用户 <名片> / 清名片 @用户：设置或清空群名片
"""

DURATION_RE = re.compile(
    r"^(?P<value>\d+)\s*(?P<unit>s|sec|secs|second|seconds|秒|m|min|mins|minute|minutes|分|分钟|"
    r"h|hr|hrs|hour|hours|时|小时|d|day|days|天)?$",
    re.IGNORECASE,
)
ON_WORDS = {"on", "1", "true", "yes", "enable", "开启", "打开", "开", "启用"}
OFF_WORDS = {"off", "0", "false", "no", "disable", "关闭", "关", "停用", "解除"}
REJECT_WORDS = {"拒绝", "拉黑", "黑", "reject", "ban", "true", "1"}


def _plain_text(args: Message) -> str:
    return args.extract_plain_text().strip()


def _to_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _unique_ints(values: list[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _at_targets(args: Message) -> list[int]:
    targets: list[int] = []
    for segment in args:
        if segment.type != "at":
            continue
        user_id = _to_int(segment.data.get("qq"))
        if user_id is not None:
            targets.append(user_id)
    return _unique_ints(targets)


def _parse_targets_with_tail(args: Message) -> tuple[list[int], str]:
    targets = _at_targets(args)
    plain = _plain_text(args)
    if targets:
        return targets, plain

    parts = plain.split()
    while parts:
        user_id = _to_int(parts[0])
        if user_id is None:
            break
        targets.append(user_id)
        parts.pop(0)
    return _unique_ints(targets), " ".join(parts)


def _parse_one_target_with_tail(
    args: Message,
    event: GroupMessageEvent,
    *,
    default_to_sender: bool = False,
) -> tuple[int | None, str]:
    targets = _at_targets(args)
    plain = _plain_text(args)
    if targets:
        return targets[0], plain

    if not plain:
        return (event.user_id, "") if default_to_sender else (None, "")

    parts = plain.split(maxsplit=1)
    user_id = _to_int(parts[0])
    if user_id is not None:
        return user_id, parts[1].strip() if len(parts) > 1 else ""

    return (event.user_id, plain) if default_to_sender else (None, plain)


def _parse_duration_seconds(raw: str, default: int = DEFAULT_MUTE_SECONDS) -> int | None:
    text = raw.strip().lower()
    if not text:
        return default
    if text in {"永久", "永远", "forever", "max", "最大"}:
        return MAX_MUTE_SECONDS

    match = DURATION_RE.match(text)
    if not match:
        return None

    value = int(match.group("value"))
    unit = match.group("unit") or "s"
    unit = unit.lower()
    if unit in {"s", "sec", "secs", "second", "seconds", "秒"}:
        seconds = value
    elif unit in {"m", "min", "mins", "minute", "minutes", "分", "分钟"}:
        seconds = value * 60
    elif unit in {"h", "hr", "hrs", "hour", "hours", "时", "小时"}:
        seconds = value * 60 * 60
    elif unit in {"d", "day", "days", "天"}:
        seconds = value * 24 * 60 * 60
    else:
        return None

    return min(seconds, MAX_MUTE_SECONDS)


def _format_duration(seconds: int) -> str:
    if seconds == 0:
        return "0秒"

    parts: list[str] = []
    days, remainder = divmod(seconds, 24 * 60 * 60)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes, secs = divmod(remainder, 60)
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分钟")
    if secs:
        parts.append(f"{secs}秒")
    return "".join(parts)


def _parse_switch(raw: str) -> bool | None:
    text = raw.strip().lower()
    if text in ON_WORDS:
        return True
    if text in OFF_WORDS:
        return False
    return None


def _reply_message_id(event: GroupMessageEvent, args: Message) -> int | None:
    for segment in args:
        if segment.type != "reply":
            continue
        message_id = _to_int(segment.data.get("id") or segment.data.get("message_id"))
        if message_id is not None:
            return message_id

    reply = getattr(event, "reply", None)
    if reply is None:
        return None
    if isinstance(reply, dict):
        return _to_int(reply.get("message_id") or reply.get("id"))
    return _to_int(getattr(reply, "message_id", None) or getattr(reply, "id", None))


def _parse_message_id(event: GroupMessageEvent, args: Message) -> tuple[int | None, str]:
    reply_id = _reply_message_id(event, args)
    plain = _plain_text(args)
    if reply_id is not None:
        return reply_id, plain

    parts = plain.split(maxsplit=1)
    if not parts:
        return None, ""
    message_id = _to_int(parts[0])
    if message_id is None:
        return None, plain
    return message_id, parts[1].strip() if len(parts) > 1 else ""


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _resolve_allowed_file(raw_path: str, root: Path) -> Path:
    root = root.resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"本地文件只能位于 {root}")
    return candidate


async def _sender_is_superuser(bot: Bot, event: GroupMessageEvent) -> bool:
    return await SUPERUSER(bot, event)


async def _sender_is_admin(bot: Bot, event: GroupMessageEvent) -> bool:
    return await ADMIN_PERMISSION(bot, event)


async def _get_bot_role(bot: Bot, event: GroupMessageEvent) -> str | None:
    try:
        member = await bot.get_group_member_info(
            group_id=event.group_id,
            user_id=int(bot.self_id),
        )
    except Exception as exc:
        logger.warning("Failed to get bot group role in %s: %s", event.group_id, exc)
        return None
    return str(member.get("role", "member"))


async def _require_bot_role(
    matcher: type[Matcher],
    bot: Bot,
    event: GroupMessageEvent,
    *,
    owner_only: bool = False,
    action: str = "执行该操作",
) -> bool:
    role = await _get_bot_role(bot, event)
    if role is None:
        await matcher.finish(f"无法确认 Bot 权限，暂时不能{action}。")
    if owner_only and role != "owner":
        await matcher.finish(f"需要 Bot 是群主才能{action}。")
    if not owner_only and role not in {"owner", "admin"}:
        await matcher.finish(f"需要 Bot 是群主或管理员才能{action}。")
    return True


async def _finish_api_error(matcher: type[Matcher], action: str, exc: Exception) -> None:
    logger.warning("%s failed: %s", action, exc)
    await matcher.finish(f"{action}失败：{exc}")


async def _set_group_ban(
    bot: Bot,
    group_id: int,
    user_ids: list[int],
    duration: int,
) -> None:
    for user_id in user_ids:
        await bot.call_api(
            "set_group_ban",
            group_id=group_id,
            user_id=user_id,
            duration=duration,
        )


async def _set_group_admin(
    bot: Bot,
    group_id: int,
    user_ids: list[int],
    enable: bool,
) -> None:
    for user_id in user_ids:
        await bot.call_api(
            "set_group_admin",
            group_id=group_id,
            user_id=user_id,
            enable=enable,
        )


group_help = on_command(
    "群管",
    aliases={"群管帮助", "群管理帮助"},
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@group_help.handle()
async def handle_group_help() -> None:
    await group_help.finish(HELP_TEXT)


set_title = on_command(
    "头衔",
    aliases={"专属头衔", "设置头衔"},
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@set_title.handle()
async def handle_set_title(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _require_bot_role(set_title, bot, event, owner_only=True, action="设置头衔")

    target_id, title = _parse_one_target_with_tail(args, event, default_to_sender=True)
    if target_id is None:
        await set_title.finish("请指定要设置头衔的成员。")
    if not title:
        await set_title.finish("请告诉我你要设置什么头衔。")
    if len(title) > MAX_SPECIAL_TITLE_LENGTH:
        await set_title.finish(f"头衔长度不能超过 {MAX_SPECIAL_TITLE_LENGTH} 个字符。")
    if target_id == int(bot.self_id) and not await _sender_is_superuser(bot, event):
        await set_title.finish("只有超管可以给 Bot 设置头衔。")
    if target_id != event.user_id and not await _sender_is_admin(bot, event):
        await set_title.finish("只有群管理员及以上可以给别人设置头衔。")

    try:
        await bot.call_api(
            "set_group_special_title",
            group_id=event.group_id,
            user_id=target_id,
            special_title=title,
            duration=-1,
        )
    except Exception as exc:
        await _finish_api_error(set_title, "设置头衔", exc)
    await set_title.finish("设置好了。")


clear_title = on_command(
    "删头衔",
    aliases={"删除头衔", "清头衔", "清空头衔"},
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@clear_title.handle()
async def handle_clear_title(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _require_bot_role(clear_title, bot, event, owner_only=True, action="清空头衔")

    target_id, _tail = _parse_one_target_with_tail(args, event, default_to_sender=True)
    if target_id is None:
        await clear_title.finish("请指定要清空头衔的成员。")
    if target_id == int(bot.self_id) and not await _sender_is_superuser(bot, event):
        await clear_title.finish("只有超管可以清空 Bot 的头衔。")
    if target_id != event.user_id and not await _sender_is_admin(bot, event):
        await clear_title.finish("只有群管理员及以上可以清空别人的头衔。")

    try:
        await bot.call_api(
            "set_group_special_title",
            group_id=event.group_id,
            user_id=target_id,
            special_title="",
            duration=-1,
        )
    except Exception as exc:
        await _finish_api_error(clear_title, "清空头衔", exc)
    await clear_title.finish("头衔已清空。")


mute_user = on_command(
    "禁言",
    aliases={"mute"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@mute_user.handle()
async def handle_mute_user(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _require_bot_role(mute_user, bot, event, action="禁言成员")

    targets, duration_text = _parse_targets_with_tail(args)
    if not targets:
        await mute_user.finish("请指定要禁言的成员：禁言 @用户 [10m]")
    targets = [target for target in targets if target != int(bot.self_id)]
    if not targets:
        await mute_user.finish("不能禁言 Bot 自己。")

    duration = _parse_duration_seconds(duration_text)
    if duration is None:
        await mute_user.finish("禁言时长格式不正确，例：30s、10m、2h、1d。")

    try:
        await _set_group_ban(bot, event.group_id, targets, duration)
    except Exception as exc:
        await _finish_api_error(mute_user, "禁言", exc)
    target_text = ", ".join(map(str, targets))
    await mute_user.finish(f"已禁言 {target_text}，时长 {_format_duration(duration)}。")


unmute_user = on_command(
    "解禁",
    aliases={"解除禁言", "unmute"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@unmute_user.handle()
async def handle_unmute_user(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _require_bot_role(unmute_user, bot, event, action="解除禁言")

    targets, _tail = _parse_targets_with_tail(args)
    if not targets:
        await unmute_user.finish("请指定要解禁的成员：解禁 @用户")

    try:
        await _set_group_ban(bot, event.group_id, targets, 0)
    except Exception as exc:
        await _finish_api_error(unmute_user, "解禁", exc)
    await unmute_user.finish(f"已解除 {', '.join(map(str, targets))} 的禁言。")


kick_user = on_command(
    "踢",
    aliases={"踢出", "kick"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@kick_user.handle()
async def handle_kick_user(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _require_bot_role(kick_user, bot, event, action="踢出成员")

    targets, tail = _parse_targets_with_tail(args)
    if not targets:
        await kick_user.finish("请指定要踢出的成员：踢 @用户 [拒绝]")
    targets = [target for target in targets if target != int(bot.self_id)]
    if not targets:
        await kick_user.finish("不能踢出 Bot 自己。")

    reject_add_request = tail.strip().lower() in REJECT_WORDS
    try:
        for target in targets:
            await bot.call_api(
                "set_group_kick",
                group_id=event.group_id,
                user_id=target,
                reject_add_request=reject_add_request,
            )
    except Exception as exc:
        await _finish_api_error(kick_user, "踢出成员", exc)

    suffix = "，并已拒绝再次加群" if reject_add_request else ""
    await kick_user.finish(f"已踢出 {', '.join(map(str, targets))}{suffix}。")


kick_reject_user = on_command(
    "踢黑",
    aliases={"拉黑踢"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@kick_reject_user.handle()
async def handle_kick_reject_user(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _require_bot_role(kick_reject_user, bot, event, action="踢出成员")

    targets, _tail = _parse_targets_with_tail(args)
    if not targets:
        await kick_reject_user.finish("请指定要踢出并拒绝再次加群的成员：踢黑 @用户")
    targets = [target for target in targets if target != int(bot.self_id)]
    if not targets:
        await kick_reject_user.finish("不能踢出 Bot 自己。")

    try:
        for target in targets:
            await bot.call_api(
                "set_group_kick",
                group_id=event.group_id,
                user_id=target,
                reject_add_request=True,
            )
    except Exception as exc:
        await _finish_api_error(kick_reject_user, "踢黑", exc)
    await kick_reject_user.finish(f"已踢出 {', '.join(map(str, targets))}，并拒绝再次加群。")


set_card = on_command(
    "改名片",
    aliases={"修改群名片", "改昵称"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@set_card.handle()
async def handle_set_card(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _require_bot_role(set_card, bot, event, action="修改群名片")

    target_id, card = _parse_one_target_with_tail(args, event)
    if target_id is None or not card:
        await set_card.finish("请指定成员和新名片：改名片 @用户 <新名片>")
    if len(card) > MAX_GROUP_CARD_LENGTH:
        await set_card.finish(f"群名片长度不能超过 {MAX_GROUP_CARD_LENGTH} 个字符。")

    try:
        await bot.call_api(
            "set_group_card",
            group_id=event.group_id,
            user_id=target_id,
            card=card,
        )
    except Exception as exc:
        await _finish_api_error(set_card, "修改群名片", exc)
    await set_card.finish(f"已将 {target_id} 的群名片改为：{card}")


clear_card = on_command(
    "清名片",
    aliases={"清空名片", "删除名片"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@clear_card.handle()
async def handle_clear_card(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _require_bot_role(clear_card, bot, event, action="清空群名片")

    targets, _tail = _parse_targets_with_tail(args)
    if not targets:
        await clear_card.finish("请指定成员：清名片 @用户")

    try:
        for target in targets:
            await bot.call_api(
                "set_group_card",
                group_id=event.group_id,
                user_id=target,
                card="",
            )
    except Exception as exc:
        await _finish_api_error(clear_card, "清空群名片", exc)
    await clear_card.finish(f"已清空 {', '.join(map(str, targets))} 的群名片。")


set_group_name = on_command(
    "群名",
    aliases={"改群名", "修改群名"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@set_group_name.handle()
async def handle_set_group_name(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _require_bot_role(set_group_name, bot, event, action="修改群名")

    group_name = _plain_text(args)
    if not group_name:
        await set_group_name.finish("请输入新的群名称。")
    if len(group_name) > MAX_GROUP_NAME_LENGTH:
        await set_group_name.finish(f"群名称长度不能超过 {MAX_GROUP_NAME_LENGTH} 个字符。")

    try:
        await bot.call_api(
            "set_group_name",
            group_id=event.group_id,
            group_name=group_name,
        )
    except Exception as exc:
        await _finish_api_error(set_group_name, "修改群名", exc)
    await set_group_name.finish(f"群名称已修改为：{group_name}")


whole_ban = on_command(
    "全员禁言",
    aliases={"全体禁言"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@whole_ban.handle()
async def handle_whole_ban(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _require_bot_role(whole_ban, bot, event, action="设置全员禁言")

    enable = _parse_switch(_plain_text(args))
    if enable is None:
        await whole_ban.finish("请指定 on/off 或 开启/关闭。")

    try:
        await bot.call_api("set_group_whole_ban", group_id=event.group_id, enable=enable)
    except Exception as exc:
        await _finish_api_error(whole_ban, "设置全员禁言", exc)
    await whole_ban.finish(f"全员禁言已{'开启' if enable else '关闭'}。")


whole_unban = on_command(
    "全员解禁",
    aliases={"全体解禁"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@whole_unban.handle()
async def handle_whole_unban(bot: Bot, event: GroupMessageEvent) -> None:
    await _require_bot_role(whole_unban, bot, event, action="关闭全员禁言")

    try:
        await bot.call_api("set_group_whole_ban", group_id=event.group_id, enable=False)
    except Exception as exc:
        await _finish_api_error(whole_unban, "关闭全员禁言", exc)
    await whole_unban.finish("全员禁言已关闭。")


async def _handle_admin_change(
    matcher: type[Matcher],
    bot: Bot,
    event: GroupMessageEvent,
    args: Message,
    enable: bool | None,
) -> None:
    await _require_bot_role(matcher, bot, event, owner_only=True, action="设置群管理员")

    targets, tail = _parse_targets_with_tail(args)
    if not targets:
        await matcher.finish("请指定成员：设管理 @用户 或 撤管理 @用户")
    targets = [target for target in targets if target != int(bot.self_id)]
    if not targets:
        await matcher.finish("不能修改 Bot 自己的管理员状态。")

    if enable is None:
        enable = _parse_switch(tail)
        if enable is None:
            await matcher.finish("请指定 on/off、设置/取消，或使用 设管理/撤管理。")

    try:
        await _set_group_admin(bot, event.group_id, targets, enable)
    except Exception as exc:
        await _finish_api_error(matcher, "设置群管理员", exc)

    action = "设为管理员" if enable else "取消管理员"
    await matcher.finish(f"已将 {', '.join(map(str, targets))} {action}。")


set_admin = on_command(
    "设管理",
    aliases={"设置管理员", "加管理"},
    permission=OWNER_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@set_admin.handle()
async def handle_set_admin(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _handle_admin_change(set_admin, bot, event, args, True)


unset_admin = on_command(
    "撤管理",
    aliases={"取消管理员", "删管理"},
    permission=OWNER_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@unset_admin.handle()
async def handle_unset_admin(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _handle_admin_change(unset_admin, bot, event, args, False)


admin_switch = on_command(
    "管理员",
    aliases={"群管理员"},
    permission=OWNER_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@admin_switch.handle()
async def handle_admin_switch(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _handle_admin_change(admin_switch, bot, event, args, None)


recall_msg = on_command(
    "撤回",
    aliases={"删除消息", "删消息"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@recall_msg.handle()
async def handle_recall_msg(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    message_id, _tail = _parse_message_id(event, args)
    if message_id is None:
        await recall_msg.finish("请回复要撤回的消息，或提供消息 ID。")

    try:
        await bot.call_api("delete_msg", message_id=message_id)
    except Exception as exc:
        await _finish_api_error(recall_msg, "撤回消息", exc)
    await recall_msg.finish("消息已撤回。")


set_essence = on_command(
    "设精华",
    aliases={"设置精华"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@set_essence.handle()
async def handle_set_essence(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _require_bot_role(set_essence, bot, event, action="设置精华消息")

    message_id, _tail = _parse_message_id(event, args)
    if message_id is None:
        await set_essence.finish("请回复要设为精华的消息，或提供消息 ID。")

    try:
        await bot.call_api("set_essence_msg", message_id=message_id)
    except Exception as exc:
        await _finish_api_error(set_essence, "设置精华消息", exc)
    await set_essence.finish("已设为精华消息。")


delete_essence = on_command(
    "取消精华",
    aliases={"删精华", "删除精华"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@delete_essence.handle()
async def handle_delete_essence(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _require_bot_role(delete_essence, bot, event, action="取消精华消息")

    message_id, _tail = _parse_message_id(event, args)
    if message_id is None:
        await delete_essence.finish("请回复要取消精华的消息，或提供消息 ID。")

    try:
        await bot.call_api("delete_essence_msg", message_id=message_id)
    except Exception as exc:
        await _finish_api_error(delete_essence, "取消精华消息", exc)
    await delete_essence.finish("已取消精华消息。")


poke_user = on_command(
    "戳",
    aliases={"拍一拍", "poke"},
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@poke_user.handle()
async def handle_poke_user(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    target_id, _tail = _parse_one_target_with_tail(args, event, default_to_sender=True)
    if target_id is None:
        await poke_user.finish("请指定要戳的成员。")

    try:
        await bot.call_api("group_poke", group_id=event.group_id, user_id=target_id)
    except Exception as first_exc:
        try:
            await bot.call_api(
                "send_poke",
                group_id=event.group_id,
                user_id=int(bot.self_id),
                target_id=target_id,
            )
        except Exception as second_exc:
            logger.warning("poke failed by group_poke=%s send_poke=%s", first_exc, second_exc)
            await poke_user.finish(f"戳一戳失败：{second_exc}")
    await poke_user.finish("戳了。")


async def _handle_emoji_like(
    matcher: type[Matcher],
    bot: Bot,
    event: GroupMessageEvent,
    args: Message,
    *,
    set_like: bool,
) -> None:
    message_id, tail = _parse_message_id(event, args)
    if message_id is None:
        await matcher.finish("请回复要贴表情的消息，或提供消息 ID。")

    parts = tail.split()
    if not parts:
        await matcher.finish("请提供 emoji_id，例如：贴表情 66。")
    emoji_id = _to_int(parts[0])
    if emoji_id is None:
        await matcher.finish("emoji_id 必须是数字。")

    try:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=message_id,
            emoji_id=emoji_id,
            set=set_like,
        )
    except Exception as exc:
        await _finish_api_error(matcher, "设置消息表情", exc)
    await matcher.finish("已处理消息表情。")


emoji_like = on_command(
    "贴表情",
    aliases={"表情回应", "点赞表情"},
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@emoji_like.handle()
async def handle_emoji_like(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _handle_emoji_like(emoji_like, bot, event, args, set_like=True)


emoji_unlike = on_command(
    "取消贴表情",
    aliases={"取消表情回应", "撤表情"},
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@emoji_unlike.handle()
async def handle_emoji_unlike(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _handle_emoji_like(emoji_unlike, bot, event, args, set_like=False)


send_text = on_command(
    "群管说",
    aliases={"发文字", "发文本"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@send_text.handle()
async def handle_send_text(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    text = _plain_text(args)
    if not text:
        await send_text.finish("请输入要发送的文字。")
    await bot.send_group_msg(group_id=event.group_id, message=MessageSegment.text(text))


send_video = on_command(
    "发视频",
    aliases={"发送视频", "群视频"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@send_video.handle()
async def handle_send_video(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    raw_source = _plain_text(args)
    if not raw_source:
        await send_video.finish(
            f"请输入视频 URL，或 {VIDEO_ROOT} 下的视频文件名。"
        )

    if _is_http_url(raw_source):
        source: str | Path = raw_source
    else:
        try:
            source = _resolve_allowed_file(raw_source, VIDEO_ROOT)
        except ValueError as exc:
            await send_video.finish(str(exc))
        if not source.exists():
            await send_video.finish(f"视频文件不存在：{source}")

    try:
        await bot.send_group_msg(
            group_id=event.group_id,
            message=MessageSegment.video(source),
        )
    except Exception as exc:
        await _finish_api_error(send_video, "发送视频", exc)


upload_group_file = on_command(
    "上传群文件",
    aliases={"传群文件", "群文件上传"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@upload_group_file.handle()
async def handle_upload_group_file(
    bot: Bot,
    event: GroupMessageEvent,
    args: CommandArgs,
) -> None:
    raw = _plain_text(args)
    if not raw:
        await upload_group_file.finish(
            f"请输入 {UPLOAD_ROOT} 下的文件名，可附带显示名：上传群文件 文件名.zip 显示名.zip"
        )
    parts = raw.split(maxsplit=1)
    raw_path = parts[0]
    display_name = parts[1].strip() if len(parts) > 1 else None

    try:
        file_path = _resolve_allowed_file(raw_path, UPLOAD_ROOT)
    except ValueError as exc:
        await upload_group_file.finish(str(exc))
    if not file_path.exists():
        await upload_group_file.finish(f"文件不存在：{file_path}")

    try:
        await bot.call_api(
            "upload_group_file",
            group_id=event.group_id,
            file=str(file_path),
            name=display_name or file_path.name,
        )
    except Exception as exc:
        await _finish_api_error(upload_group_file, "上传群文件", exc)
    await upload_group_file.finish("群文件已上传。")


send_notice = on_command(
    "群公告",
    aliases={"发公告", "发送群公告"},
    permission=ADMIN_PERMISSION,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@send_notice.handle()
async def handle_send_notice(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    await _require_bot_role(send_notice, bot, event, action="发送群公告")

    content = _plain_text(args)
    if not content:
        await send_notice.finish("请输入公告内容。")

    try:
        await bot.call_api("_send_group_notice", group_id=event.group_id, content=content)
    except Exception as exc:
        await _finish_api_error(send_notice, "发送群公告", exc)
    await send_notice.finish("群公告已发送。")


leave_group = on_command(
    "退群",
    aliases={"退出本群"},
    permission=SUPERUSER,
    rule=GROUP_RULE,
    priority=5,
    block=True,
)


@leave_group.handle()
async def handle_leave_group(bot: Bot, event: GroupMessageEvent, args: CommandArgs) -> None:
    if _plain_text(args) != "确认":
        await leave_group.finish("如需让 Bot 退出本群，请发送：退群 确认")

    try:
        await bot.call_api("set_group_leave", group_id=event.group_id, is_dismiss=False)
    except Exception as exc:
        await _finish_api_error(leave_group, "退出群聊", exc)
