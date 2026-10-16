from __future__ import annotations

import asyncio
from types import SimpleNamespace

import nonebot

nonebot.init()

from kanamibot.plugins.er_dak import _card_for  # noqa: E402
from kanamibot.plugins.er_dak.parser import ParsedCommand  # noqa: E402


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
