"""Recognize bot interactions without running command handlers or fetching quoted messages."""

from __future__ import annotations

import re
import sys
import time

from nonebot import get_bots
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, PrivateMessageEvent
from nonebot.matcher import matchers
from nonebot.rule import RegexRule, TrieRule

from .commands import REQUEST_PATTERN


def is_command_message(message) -> bool:
    if not isinstance(message, list | tuple | Message):
        return False
    parts = []
    for segment in message:
        kind = (
            segment.type
            if hasattr(segment, "type")
            else segment.get("type")
            if isinstance(segment, dict)
            else None
        )
        data = (
            segment.data
            if hasattr(segment, "data")
            else segment.get("data", {})
            if isinstance(segment, dict)
            else {}
        )
        if kind == "text" and isinstance(data, dict):
            parts.append(str(data.get("text", "")))
    text = re.sub(r"^(?:@\d+\s+)+", "", "".join(parts).strip())
    if not text:
        return False
    if TrieRule.prefix.longest_prefix(text):
        return True
    if re.search(REQUEST_PATTERN, text) or re.match(r"^(?:#搜图|混淆|解混淆)(?=\s|$)", text):
        return True
    # Match registered regex command syntax only; never evaluate permissions, ModuleRule,
    # custom rules or handlers (which can have side effects).
    seen = set()
    for priority_matchers in tuple(matchers.values()):
        for matcher in tuple(priority_matchers):
            if matcher.type != "message":
                continue
            for checker in matcher.rule.checkers:
                rule = checker.call
                if isinstance(rule, RegexRule) and (rule.regex, rule.flags) not in seen:
                    seen.add((rule.regex, rule.flags))
                    matched = re.search(rule.regex, text, rule.flags)
                    if matched is not None and matched.start() == 0 and matched.end() > 0:
                        return True
    return False


def bot_ids(bot) -> set[str]:
    return {str(bot.self_id)} | {
        str(connected.self_id) for connected in get_bots().values() if isinstance(connected, Bot)
    }


def bot_authored(value: dict, ids: set[str]) -> bool:
    sender = value.get("sender", {})
    if not isinstance(sender, dict):
        sender = {}
    return str(value.get("user_id") or value.get("uin") or sender.get("user_id")) in ids


def exclude_event(bot, event: MessageEvent) -> bool:
    ids = bot_ids(bot)
    if str(event.user_id) in ids or is_command_message(event.original_message):
        return True
    if any(
        segment.type == "at" and str(segment.data.get("qq")) in ids
        for segment in event.original_message
    ):
        return True
    reply = event.reply
    if reply is not None:
        sender = reply.sender
        if str(getattr(sender, "user_id", None)) in ids:
            return True
    # The obfuscator consumes plain image follow-ups during its 60-second private dialog.
    # Inspect an already-loaded module only; importing it here would register extra matchers.
    module = sys.modules.get("kanamibot.plugins.image_obfuscator")
    if module is not None and isinstance(event, PrivateMessageEvent):
        key = f"{event.self_id}:{event.user_id}"
        pending = getattr(module, "_pending", {}).get(key)
        if pending is not None and pending.expires_at > time.monotonic():
            return True
    return False


def replied_message_id(event: MessageEvent) -> int | None:
    if event.reply is not None:
        return event.reply.message_id
    for segment in event.original_message:
        if segment.type in {"reply", "quote"}:
            try:
                return int(segment.data.get("id") or segment.data.get("message_id"))
            except (TypeError, ValueError):
                continue
    return None
