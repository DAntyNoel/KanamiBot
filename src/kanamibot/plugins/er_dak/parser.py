from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

COMMAND_PATTERN = (
    r"\A#(?P<head>永恒轮回战绩|BS战绩|永恒轮回角色|BS角色|BS)"
    r"[ \u3000]+(?P<content>[^\r\n]*)\Z"
)
PREFIX = re.compile(COMMAND_PATTERN, re.IGNORECASE)
DAK_ACTIONS = {
    "查询玩家",
    "查玩家",
    "查询角色装备",
    "查询角色",
    "角色数据统计",
    "角色数据",
    "段位统计",
    "永恒多少分",
    "查询装备",
}
SUBCOMMANDS = DAK_ACTIONS | {
    "帮助",
    "来源",
    "绑定",
    "解绑",
    "查询",
    "段位",
    "统计",
    "近期",
    "实验体",
    "皮肤",
    "队友",
    "最佳局",
    "英雄池",
    "出装习惯",
    "雷达",
    "战绩",
    "多查",
    "对比",
    "排行榜",
    "角色强度",
    "物品",
    "路线",
}
ALIASES = {
    "查": "查询",
    "rank": "段位",
    "stats": "统计",
    "recent": "近期",
    "radar": "雷达",
    "评分": "雷达",
    "matches": "战绩",
    "角色": "查询角色",
    "英雄": "查询角色",
    "玩家": "查询玩家",
    "英雄池": "英雄池",
    "build": "出装习惯",
    "leaderboard": "排行榜",
    "character": "角色强度",
    "item": "物品",
    "route": "路线",
    "help": "帮助",
    "source": "来源",
}


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    action: str
    args: tuple[str, ...]


def parse_command(message: str) -> ParsedCommand:
    match = PREFIX.fullmatch(message)
    if match is None:
        raise ValueError("命令头后必须有空格，例如 #BS战绩 玩家名 或 #BS角色 席琳。")
    head = match.group("head").casefold()
    content = match.group("content").strip()
    lexer = shlex.shlex(content, posix=True)
    lexer.whitespace += ",，\u3000"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        parts = tuple(lexer)
    except ValueError as exc:
        raise ValueError("命令引号不匹配，请检查昵称或查询名称。") from exc
    if head in {"永恒轮回战绩", "bs战绩"}:
        return ParsedCommand("查询玩家", parts)
    if head in {"永恒轮回角色", "bs角色"}:
        return ParsedCommand("查询角色", parts)
    if not parts:
        return ParsedCommand("帮助", ())
    action = ALIASES.get(parts[0].casefold(), parts[0])
    if action not in SUBCOMMANDS:
        return ParsedCommand("自动查询", parts)
    return ParsedCommand(action, parts[1:])


def match_character(query: str, characters: Iterable[Mapping[str, Any]]) -> str | None:
    """Recognize exact names/IDs/pinyin without fuzzy-matching a player into a hero."""
    entries = list(characters)
    normalized = query.strip().casefold()
    for character in entries:
        if normalized in {str(character.get(key, "")).casefold() for key in ("name", "key", "id")}:
            return str(character.get("name") or character.get("key") or character["id"])

    from pypinyin import Style, lazy_pinyin

    phonetic = normalized.replace(" ", "")
    matches = [
        character
        for character in entries
        if phonetic
        in {
            "".join(lazy_pinyin(str(character.get("name", "")))).casefold(),
            "".join(
                lazy_pinyin(str(character.get("name", "")), style=Style.FIRST_LETTER)
            ).casefold(),
        }
    ]
    if len(matches) > 1:
        choices = " / ".join(str(character.get("name")) for character in matches[:15])
        raise ValueError(f"拼音匹配多个角色，请使用 #BS角色 完整名称：{choices}")
    if matches:
        return str(matches[0].get("name") or matches[0].get("key") or matches[0]["id"])
    return None


def parse_match_args(args: tuple[str, ...]) -> tuple[str | None, int]:
    nickname: str | None = None
    count = 5
    if not args:
        return nickname, count
    if len(args) == 1 and args[0].isdigit():
        count = int(args[0])
    else:
        nickname = args[0]
        if len(args) >= 2:
            if not args[1].isdigit():
                raise ValueError("战绩数量必须是 1–10 的整数")
            count = int(args[1])
    if not 1 <= count <= 10:
        raise ValueError("战绩数量必须在 1–10 之间")
    return nickname, count
