from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
import pytest

nonebot.init()

import kanamibot.plugins.er_dak as er_dak  # noqa: E402
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
            '#BS战绩 "Player Name" 普通',
            "search_player_card",
            ("Player Name",),
            {"mode": "NORMAL"},
        ),
        (
            "#BS角色 席琳 排位 星陨",
            "character_analysis_card",
            ("席琳",),
            {"mode": "RANK", "tier": "meteorite_plus", "equipment_only": False},
        ),
        (
            "#BS 查询角色装备 艾玛 钴协议",
            "character_analysis_card",
            ("艾玛",),
            {"mode": "COBALT", "tier": "diamond_plus", "equipment_only": True},
        ),
        ("#BS 角色数据 无暇", "character_stats_card", (), {"tier": "mithril_plus"}),
        (
            "#BS 段位统计 国服",
            "tier_statistics_card",
            (),
            {"server": "asia2", "cutoffs_only": False},
        ),
        (
            "#BS 永恒多少分 全球",
            "tier_statistics_card",
            (),
            {"server": "global", "cutoffs_only": True},
        ),
        ("#BS 查询装备 lieyang", "item_card", ("lieyang",), {}),
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


@pytest.mark.parametrize("query", ["席琳", "Celine", "xilin", "xl", "62"])
def test_auto_queries_prefer_characters(query) -> None:
    metadata = AsyncMock(return_value={"characters": [{"id": 62, "name": "席琳", "key": "Celine"}]})
    service = SimpleNamespace(
        client=SimpleNamespace(metadata=metadata),
        character_analysis_card=AsyncMock(return_value="character"),
        search_player_card=AsyncMock(),
    )
    result = asyncio.run(
        _card_for(service, SimpleNamespace(user_id=10001), parse_command(f"#BS {query} 排位 星陨"))
    )
    assert result == "character"
    service.character_analysis_card.assert_awaited_once_with(
        "席琳",
        mode="RANK",
        tier="meteorite_plus",
        equipment_only=False,
    )
    service.search_player_card.assert_not_awaited()


def test_auto_queries_use_player_when_no_character_matches() -> None:
    service = SimpleNamespace(
        client=SimpleNamespace(
            metadata=AsyncMock(
                return_value={
                    "characters": [{"id": 62, "name": "席琳", "key": "Celine"}],
                }
            )
        ),
        character_analysis_card=AsyncMock(),
        search_player_card=AsyncMock(return_value="player"),
    )
    result = asyncio.run(
        _card_for(service, SimpleNamespace(user_id=10001), parse_command("#BS 席琳123 普通"))
    )
    assert result == "player"
    service.search_player_card.assert_awaited_once_with("席琳123", mode="NORMAL")
    service.character_analysis_card.assert_not_awaited()


def test_explicit_player_header_overrides_same_named_character() -> None:
    service = SimpleNamespace(search_player_card=AsyncMock(return_value="same-name-player"))
    result = asyncio.run(
        _card_for(service, SimpleNamespace(user_id=10001), parse_command("#BS战绩 席琳"))
    )
    assert result == "same-name-player"
    service.search_player_card.assert_awaited_once_with("席琳", mode="RANK")


@pytest.mark.parametrize(
    ("message", "nickname"),
    [
        ("#BS评价 dantynoel", "dantynoel"),
        ('#BS评价 "Player Name"', "Player Name"),
        ("#BS 评价 席琳", "席琳"),
    ],
)
def test_summary_uses_explicit_player_and_compatible_count_without_auto_routing(
    monkeypatch, message: str, nickname: str,
) -> None:
    def unexpected_binding_lookup(user_id: int) -> str:
        raise AssertionError("summary requires an explicit nickname")

    monkeypatch.setattr(er_dak, "get_binding", unexpected_binding_lookup)
    service = SimpleNamespace(player_summary=AsyncMock(return_value="summary"))

    result = asyncio.run(
        _card_for(service, SimpleNamespace(user_id=10001), parse_command(message))
    )

    assert result == "summary"
    service.player_summary.assert_awaited_once_with(nickname, count=20)


@pytest.mark.parametrize("message", ["#BS评价 ", "#BS 评价", '#BS评价 ""', "#BS评价 A B"])
def test_summary_rejects_missing_or_ambiguous_nickname_even_with_binding(
    monkeypatch, message,
) -> None:
    monkeypatch.setattr(er_dak, "get_binding", lambda _: "Bound Player")
    service = SimpleNamespace(player_summary=AsyncMock())

    with pytest.raises(er_dak.InvalidQuery, match="#BS评价 <玩家昵称>"):
        asyncio.run(_card_for(service, SimpleNamespace(user_id=10001), parse_command(message)))

    service.player_summary.assert_not_awaited()


def test_help_includes_summary_command() -> None:
    assert "#BS评价 <昵称>" in er_dak.HELP_TEXT
    assert "本赛季排位（最多最近 500 场），不足 20 场以同赛季匹配补足" in er_dak.HELP_TEXT
