from __future__ import annotations

import re
from dataclasses import dataclass

PREFIX = re.compile(r"^/?(?:er|永恒|永轮)(?:\s+|$)", re.IGNORECASE)
ALIASES = {
    "查": "查询",
    "rank": "段位",
    "stats": "统计",
    "recent": "近期",
    "matches": "战绩",
    "角色": "实验体",
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
    content = PREFIX.sub("", message.strip(), count=1).strip()
    if not content:
        return ParsedCommand("帮助", ())
    parts = tuple(part for part in re.split(r"[\s,，]+", content) if part)
    action = ALIASES.get(parts[0].casefold(), parts[0])
    return ParsedCommand(action, parts[1:])


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
