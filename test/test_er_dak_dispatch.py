from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
import pytest

nonebot.init()

from kanamibot.plugins.er_dak import _card_for  # noqa: E402
from kanamibot.plugins.er_dak.parser import ParsedCommand, parse_command  # noqa: E402


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def matches_card(self, nickname: str, *, count: int):
        self.calls.append(("matches", nickname, count))
        return {"kind": "matches"}

    async def multi_card(self, nicknames: list[str]):
        self.calls.append(("multi", *nicknames))
        return {"kind": "multi"}


def test_dispatch_keeps_business_logic_in_service() -> None:
    service = FakeService()
    event = SimpleNamespace(user_id=10001)

    match_result = asyncio.run(
        _card_for(service, event, ParsedCommand("战绩", ("B站丨咕咕禽OC", "5")))
    )
    multi_result = asyncio.run(
        _card_for(service, event, ParsedCommand("多查", ("Alice", "Bob", "Carol")))
    )

    assert match_result == {"kind": "matches"}
    assert multi_result == {"kind": "multi"}
    assert service.calls == [
        ("matches", "B站丨咕咕禽OC", 5),
        ("multi", "Alice", "Bob", "Carol"),
    ]


@pytest.mark.parametrize(
    ("text", "method", "args", "kwargs"),
    [
        (
            '/查询玩家 "Player Name" 普通',
            "search_player_card",
            ("Player Name",),
            {"mode": "NORMAL"},
        ),
        (
            "/er 查询角色 席琳 排位 星陨",
            "character_analysis_card",
            ("席琳",),
            {"mode": "RANK", "tier": "meteorite_plus", "equipment_only": False},
        ),
        (
            "/查询角色装备 艾玛 钴协议",
            "character_analysis_card",
            ("艾玛",),
            {"mode": "COBALT", "tier": "diamond_plus", "equipment_only": True},
        ),
        ("/角色数据 无暇", "character_stats_card", (), {"tier": "mithril_plus"}),
        ("/段位统计 国服", "tier_statistics_card", (), {"server": "asia2", "cutoffs_only": False}),
        (
            "/永恒多少分 全球",
            "tier_statistics_card",
            (),
            {"server": "global", "cutoffs_only": True},
        ),
        ("/查询装备 lieyang", "item_card", ("lieyang",), {}),
    ],
)
def test_new_queries_share_library_filters(text, method, args, kwargs) -> None:
    result = object()
    call = AsyncMock(return_value=result)
    service = SimpleNamespace(**{method: call})
    actual = asyncio.run(_card_for(service, SimpleNamespace(user_id=10001), parse_command(text)))
    assert actual is result
    call.assert_awaited_once_with(*args, **kwargs)


@pytest.mark.parametrize(
    ("action", "method"),
    [
        ("查询", "player_overview"),
        ("段位", "rank_card"),
        ("统计", "stats_card"),
        ("近期", "recent_card"),
        ("实验体", "characters_card"),
        ("皮肤", "skins_card"),
        ("队友", "teammates_card"),
        ("最佳局", "best_match_card"),
        ("英雄池", "hero_pool_card"),
        ("出装习惯", "equipment_card"),
        ("雷达", "radar_card"),
    ],
)
def test_player_commands_and_radar(action, method) -> None:
    methods = (
        "player_overview",
        "rank_card",
        "stats_card",
        "recent_card",
        "characters_card",
        "skins_card",
        "teammates_card",
        "best_match_card",
        "hero_pool_card",
        "equipment_card",
        "radar_card",
    )
    service = SimpleNamespace(**{name: AsyncMock() for name in methods})
    asyncio.run(
        _card_for(service, SimpleNamespace(user_id=10001), ParsedCommand(action, ("Player",)))
    )
    getattr(service, method).assert_awaited_once_with("Player")
