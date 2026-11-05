"""Parse the private superuser image request syntax."""

from __future__ import annotations

import re

HELP = "图ab 查看A1B1缩略图；图ab 3 发送第3张；图aabb 3,5,12,25 批量发送。"


def group_command(category: tuple[str, str]) -> str | None:
    a, b = category
    if a in {"A1", "A2", "A3"} and b in {"B1", "B2", "B3"}:
        return "图" + "a" * int(a[1]) + "b" * int(b[1])
    return None


def parse_request(text: str) -> tuple[tuple[str, str] | None, list[int]]:
    match = re.fullmatch(r"图(?:(a{1,3}b{1,3}|a[123]b[123]))?(?:\s+(.*))?", text.strip(), re.I)
    if match is None:
        raise ValueError(HELP)
    raw, selection = match.groups()
    category = None
    if raw:
        raw = raw.lower()
        if raw[1].isdigit():
            category = ("A" + raw[1], "B" + raw[3])
        else:
            category = (f"A{raw.count('a')}", f"B{raw.count('b')}")
    if not selection:
        return category, []
    if category is None or not re.fullmatch(r"[0-9]+(?:\s*[,，]\s*[0-9]+)*", selection):
        raise ValueError(HELP)
    numbers = list(dict.fromkeys(int(value.strip()) for value in re.split("[,，]", selection)))
    if not numbers or min(numbers) < 1:
        raise ValueError("图片序号从1开始。")
    if len(numbers) > 100:
        raise ValueError("每次最多发送100张，请分次指定。")
    return category, numbers
