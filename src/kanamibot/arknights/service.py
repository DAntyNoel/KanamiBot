from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from .client import MCP, BackendError
from .commands import Command
from .records import Records, normalized
from .wiki import Wiki

OPERATOR_POINTS = {
    "operator",
    "skills",
    "talents",
    "trait",
    "stats",
    "profile",
    "building",
    "modules",
    "training",
    "skins",
    "tokens",
    "chess",
}
KINDS = {
    "enemy": {"敌人"},
    "stage": {"关卡"},
    "bond": {"卫戍协议盟约"},
    "strategy": {"卫戍协议策略"},
    "material": {"材料", "干员"},
}
TOOLS = {
    "干员": ("get_operator_basic_data", "operator_id"),
    "材料": ("get_material", "material_id"),
    "敌人": ("get_enemy_data", "enemy_id"),
    "关卡": ("get_stage_data", "stage_id"),
    "卫戍协议盟约": ("get_autochess_bond_detail", "bond_id"),
    "卫戍协议策略": ("get_autochess_strategy_detail", "strategy_id"),
}
OPERATOR_TOOLS = {
    "skills": "get_operator_skill",
    "modules": "get_operator_modules",
    "material": "get_operator_material",
    "training": "get_operator_material",
    "skins": "get_operator_skins",
    "tokens": "get_token_detail",
}
FIELDS = {
    "talents": "天赋",
    "trait": "档案",
    "stats": "属性",
    "profile": "档案",
    "building": "基建技能",
    "chess": "卫戍协议",
}


def display(value: Any, indent: int = 0) -> str:
    """Keep game-facing field names and strip only formatting tags."""
    if isinstance(value, dict):
        return "\n".join(
            f"{'  ' * indent}{key}：{display(item, indent + 1)}"
            for key, item in value.items()
            if item is not None and key not in {"ID", "id", "图标"}
        )
    if isinstance(value, list):
        return "\n".join(display(item, indent) for item in value)
    return re.sub(r"<[^>]*>", "", str(value)).replace("\\n", "\n")


@dataclass
class Result:
    text: str = ""
    card_url: str = ""
    pages: list[str] = field(default_factory=list)
    choices: list[dict] = field(default_factory=list)
    fallback: str = ""


class Queries:
    def __init__(self, mcp: MCP, records: Records, wiki: Wiki | None = None) -> None:
        self.mcp, self.records = mcp, records
        self.wiki = wiki or Wiki()
        self.pending: dict[str, tuple[float, Command, list[dict]]] = {}

    def choices(self, key: str, command: Command, items: list[dict]) -> Result:
        now = time.monotonic()
        self.pending = {k: value for k, value in self.pending.items() if value[0] > now}
        if len(self.pending) >= 256:
            self.pending.pop(next(iter(self.pending)))
        self.pending[key] = (now + 300, command, items[:20])
        text = "找到以下候选，请发送 #粥 选择 编号（5 分钟内有效）：\n"
        text += "\n".join(
            f"{index}. {item['name']}【{item['type']}】" for index, item in enumerate(items[:20], 1)
        )
        return Result(text=text, choices=items[:20])

    async def execute(self, command: Command, key: str) -> Result:
        if command.action == "alias_list":
            return Result(pages=self.records.list_messages())
        if command.action == "alias_delete":
            existed = self.records.delete(command.alias)
            verb = "已删除绰号" if existed else "已停用绰号"
            return Result(text=f"{verb}「{command.alias}」。")
        if command.action == "alias_add":
            name = self.records.resolve(command.name)
            info = self.records.operators.get(name)
            if info is None:
                async with self.mcp.session() as session:
                    data = await session.call("search", {"query": name})
                    exact = [
                        item
                        for item in data.get("data", {}).get("items", [])
                        if item.get("type") == "干员" and item.get("name") == name
                    ]
                    if len(exact) != 1:
                        raise ValueError("未找到唯一的干员，请使用正式名称。")
                    detail = await session.call(
                        "get_operator_basic_data", {"operator_id": exact[0]["id"]}
                    )
                    info = {"faction": detail.get("data", {}).get("档案", {}).get("阵营", "未分类")}
            self.records.add(name, command.alias, operator=info)
            return Result(text=f"已添加：{name} → {command.alias}")
        if command.action == "rogue":
            return Result(text="暂未接入可靠的全体玩家肉鸽统计页，此项查询暂不提供。")
        if command.action == "select":
            pending = self.pending.get(key)
            if not pending or pending[0] < time.monotonic():
                self.pending.pop(key, None)
                return Result(text="候选已过期，请重新查询。")
            _, previous, items = pending
            if command.index > len(items):
                raise ValueError("编号超出本次候选范围。")
            item = items[command.index - 1]
            if item["type"] == "Wiki":
                result = Result(text=await self.wiki.detail(item["id"], previous.name))
            else:
                async with self.mcp.session() as session:
                    result = await self.detail(session, previous, item)
            self.pending.pop(key, None)
            return result
        # A fresh query invalidates an older selection in the same user/conversation.
        self.pending.pop(key, None)
        if command.point == "wiki" or command.mode == "演算":
            items = await self.wiki.search(command.name, reclamation=command.mode == "演算")
            if not items:
                return Result(text="PRTS Wiki 中未找到相关资料，请换一个关键词。")
            if len(items) == 1:
                return Result(text=await self.wiki.detail(items[0]["id"], command.name))
            return self.choices(key, command, items)
        if command.point == "glossary":
            async with self.mcp.session() as session:
                value = await session.call("get_glossary", {"glossary_name": command.name})
            return Result(text=display(value) or "未找到相关术语。")
        name = command.name
        if self.records.deleted(name) and normalized(name) not in self.records.names:
            return Result(text=f"未找到「{name}」：该绰号已停用，请使用正式名称。")
        # Nicknames describe operators; explicit enemy/material queries keep their own names.
        if command.point not in {"enemy", "stage", "bond", "strategy"}:
            name = self.records.resolve(name)
        targets = [name]
        if command.alter:
            targets = self.records.alters(name)
            if not targets:
                return Result(text=f"未找到「{name}」已实装的异格关系。")
        async with self.mcp.session() as session:
            items = []
            for target in targets:
                payload = await session.call("search", {"query": target})
                found = self.filter(command, payload.get("data", {}).get("items", []), target)
                items.extend(found)
            items = list({item["id"]: item for item in items}.values())
            if not items:
                return Result(text=f"未找到「{command.name}」的相关资料。")
            if len(items) == 1 and items[0].get("_exact"):
                return await self.detail(session, command, items[0])
            result = self.choices(key, command, items)
            try:
                card = await session.call(
                    "build_search_card", {"ids": [item["id"] for item in result.choices]}
                )
                result.card_url = card.get("card_image_url", "")
            except BackendError:
                pass
            return result

    def filter(self, command: Command, items: list[dict], target: str) -> list[dict]:
        if command.point in OPERATOR_POINTS or command.alter:
            kinds = {"干员"}
        else:
            kinds = KINDS.get(command.point, set(TOOLS))
        if command.mode == "卫戍":
            kinds = kinds & {"干员", "卫戍协议盟约", "卫戍协议策略"}
        items = [item for item in items if item.get("type") in kinds and item.get("id")]
        # Tombstones must also win over the backend's hourly global alias sync.
        items = [
            item
            for item in items
            if not (
                item.get("from_alias")
                and self.records.deleted(str(item["from_alias"]))
                and normalized(item.get("name", "")) != normalized(target)
            )
        ]
        exact = [
            item
            for item in items
            if normalized(target)
            in {
                normalized(str(item.get(field) or ""))
                for field in ("name", "id", "code", "from_alias")
            }
        ]
        for item in items:
            item["_exact"] = item in exact
        if exact:
            if command.point in {"auto", "material", "basic"} and command.mode != "卫戍":
                operators = [item for item in exact if item["type"] == "干员"]
                if operators:
                    return operators
            return exact
        return items

    async def detail(self, session, command: Command, item: dict) -> Result:
        kind, name = item["type"], item["name"]
        tool, argument = TOOLS[kind]
        if kind == "干员" and command.mode != "卫戍":
            tool = OPERATOR_TOOLS.get(command.point, tool)
        payload = await session.call(tool, {argument: item["id"]})
        data = payload.get("data", {})
        if kind == "干员" and (command.mode == "卫戍" or command.point == "chess"):
            chess = data.get("卫戍协议") if isinstance(data, dict) else None
            if not chess:
                return Result(text=f"{name}：当期卫戍协议没有这名干员的棋子信息。")
            return Result(
                text=f"{name} · 卫戍协议\n{display(chess)}",
                card_url=payload.get("card_image_url", ""),
            )
        if kind == "干员" and command.point == "skills":
            pages = []
            for skill in data.get("技能", []):
                # The full per-level response is available; show all real levels,
                # split into separate chat-record nodes for comfortable reading.
                title = f"{name} · 技能{skill.get('序号', '')} {skill.get('名称', '')}"
                levels = skill.get("等级", [])
                page = title
                for level in levels:
                    visible = {
                        key: value
                        for key, value in level.items()
                        if key not in {"解包等级", "专精等级", "升级归属", "持续时间类型"}
                    }
                    block = "\n\n" + display(visible)
                    if len(page) + len(block) > 2200:
                        pages.append(page)
                        page = title + "（续）"
                    page += block
                pages.append(page)
            return Result(pages=pages, text="" if pages else f"{name}没有技能。")
        if kind == "干员" and command.point in FIELDS:
            value = data.get(FIELDS[command.point], {})
            if command.point == "trait" and isinstance(value, dict):
                value = value.get("特性", "暂无特性资料。")
            return Result(text=f"{name}\n{display(value) or '暂无对应资料。'}")
        text = f"{name}【{kind}】"
        if not payload.get("card_image_url"):
            text += "\n" + display(data)
        return Result(
            text=text,
            card_url=payload.get("card_image_url", ""),
            fallback=f"{name}【{kind}】\n{display(data)}"[:12000],
        )
