from __future__ import annotations

import re
from dataclasses import dataclass

HEAD = re.compile(r"^#(?:粥|方舟|ark)(?=\s|$)\s*", re.IGNORECASE)
MODES = {
    "卫": "卫戍",
    "卫戍": "卫戍",
    "卫戍协议": "卫戍",
    "盐酸": "演算",
    "演算": "演算",
    "生息演算": "演算",
}
POINTS = {
    "资料": "basic",
    "详情": "basic",
    "干员": "operator",
    "角色": "operator",
    "技能": "skills",
    "天赋": "talents",
    "特性": "trait",
    "属性": "stats",
    "面板": "stats",
    "档案": "profile",
    "基建": "building",
    "模组": "modules",
    "材料": "material",
    "专精": "training",
    "精英化": "training",
    "养成": "training",
    "皮肤": "skins",
    "时装": "skins",
    "召唤物": "tokens",
    "敌人": "enemy",
    "敌方": "enemy",
    "关卡": "stage",
    "术语": "glossary",
    "盟约": "bond",
    "策略": "strategy",
    "棋子": "chess",
    "wiki": "wiki",
}
RESERVED = frozenset({*MODES, *POINTS, "绰号", "列表", "删除", "选择", "帮助", "异格"})


@dataclass(frozen=True)
class Command:
    action: str = "query"
    name: str = ""
    point: str = "auto"
    mode: str = ""
    alter: bool = False
    alias: str = ""
    index: int = 0


def command_body(text: str) -> str | None:
    text = re.sub(r"^(?:@\d+\s+)+", "", text.strip())
    match = HEAD.match(text)
    return text[match.end() :].strip() if match else None


def parse(text: str) -> Command:
    body = command_body(text)
    if body is None:
        raise ValueError("请使用 #粥、#方舟 或 #ark。")
    words = body.split()
    if not words or words[0].casefold() in {"帮助", "help", "--help"}:
        return Command(action="help")
    if words[0] == "绰号":
        if words[1:] == ["列表"]:
            return Command(action="alias_list")
        if len(words) == 3 and words[2] == "删除":
            return Command(action="alias_delete", alias=words[1])
        if len(words) == 3:
            return Command(action="alias_add", name=words[1], alias=words[2])
        raise ValueError("用法：#粥 绰号 干员名 绰号；#粥 绰号 绰号 删除；#粥 绰号 列表")
    if words[0] == "选择":
        if len(words) != 2 or not words[1].isdigit() or not 1 <= int(words[1]) <= 20:
            raise ValueError("请发送 #粥 选择 1（编号以本次候选为准）。")
        return Command(action="select", index=int(words[1]))
    if words[0] in {"肉鸽", "肉鸽统计", "集成战略"}:
        return Command(action="rogue")
    modes = {MODES[word] for word in words if word in MODES}
    if len(modes) > 1:
        raise ValueError("一次只能查询一种玩法。")
    mode = next(iter(modes), "")
    words = [word for word in words if word not in MODES]
    # A mode can surround a normal entity-first query, e.g. 卫 银灰 棋子.
    alter = "异格" in words
    words = [word for word in words if word != "异格"]
    point = "auto"
    if len(words) > 1 and words[-1].casefold() in POINTS:
        point = POINTS[words.pop().casefold()]
    if alter and (
        point in {"enemy", "stage", "bond", "strategy", "glossary", "wiki"} or mode == "演算"
    ):
        raise ValueError("异格仅用于查询干员。")
    if mode == "演算":
        return Command(name=" ".join(words), point=point, mode=mode, alter=alter)
    if not words:
        return Command(action="help")
    return Command(name=" ".join(words), point=point, mode=mode, alter=alter)
