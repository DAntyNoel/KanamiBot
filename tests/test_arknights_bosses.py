from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from bs4 import BeautifulSoup

from kanamibot.arknights.bosses import (
    ACTIVITY,
    BOSS_ORDER,
    activity_excerpt,
    clean_excerpt,
    enemy_excerpt,
    roster,
)
from kanamibot.arknights.client import BackendError
from kanamibot.arknights.commands import Command, command_body, parse
from kanamibot.arknights.records import Records
from kanamibot.arknights.service import Queries
from kanamibot.arknights.wiki import Wiki

FIXTURES = Path(__file__).with_name("fixtures") / "arknights"
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII="
)


def run(coro):
    return asyncio.run(coro)


def fixture(name):
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("body", ["炎 盟约", "真理", "黑白相易 策略", "boss", "帮助", ""])
def test_guard_shortcut_is_equivalent(body):
    assert parse(f"#卫 {body}") == parse(f"#粥 卫 {body}")
    assert command_body("#卫生") is None
    assert command_body("#卫boss") is None
    assert parse("@123 #卫 炎 盟约").mode == "卫戍"


@pytest.mark.parametrize("head", ["#卫", "#粥 卫", "#方舟 卫戍", "#ARK 卫戍协议"])
def test_boss_grammar(head):
    assert parse(f"{head} BOSS") == Command(action="boss", mode="卫戍")
    assert parse(f"{head} boss 1") == Command(action="boss", mode="卫戍", index=1)
    for word in ("隐秘", "隐秘核心", "（隐秘）", "(隐秘)"):
        assert parse(f"{head} boss 1 {word}") == Command(
            action="boss", mode="卫戍", index=1, hidden=True
        )


@pytest.mark.parametrize("body", ["boss -1", "boss 0", "boss a", "boss 101", "boss 1 攻略"])
def test_boss_invalid_arguments(body):
    with pytest.raises(ValueError):
        parse(f"#卫 {body}")


def test_actual_prts_roster_and_variant_values():
    html = fixture("boss_roster")
    bosses = roster(html)
    assert tuple(b.name for b in bosses) == BOSS_ORDER
    assert [i for i, b in enumerate(bosses, 1) if b.hidden] == [1, 2, 3, 7, 8, 9]
    normal = clean_excerpt(activity_excerpt(html, bosses[0], False)).get_text()
    hidden = clean_excerpt(activity_excerpt(html, bosses[0], True)).get_text()
    assert "3,940,000" in normal and "6,840,000" not in normal
    assert "6,840,000" in hidden and "3,940,000" not in hidden
    assert "283,680" in normal and "283,680" not in hidden
    assert "规程等级20" in hidden and "规程等级1\n" not in hidden
    assert "假想敌：铳" not in normal
    for boss in bosses:
        assert boss.name in clean_excerpt(activity_excerpt(html, boss, False)).get_text()


def test_source_default_corrections_and_selected_level_preserved():
    raw = fixture("boss_zhou")
    overview, levels = enemy_excerpt(raw, False)
    text = clean_excerpt(overview).get_text()
    assert "不会额外发射<刺胄之弹>" in text
    assert "隐秘核心" not in text and "旧日的剑与锤" not in text
    assert "因描述与游戏实际表现不符合" not in text
    assert "切换原文版本" not in text
    hidden_overview, hidden_levels = enemy_excerpt(raw, True)
    assert "旧日的剑与锤" in clean_excerpt(hidden_overview).get_text()
    assert "1200000" in clean_excerpt(hidden_levels).get_text()
    assert "1200000" not in clean_excerpt(levels).get_text()
    assert "最大生命值" in clean_excerpt(levels).get_text()


def test_missing_hidden_detail_does_not_substitute_normal_stats():
    overview, levels = enemy_excerpt(fixture("boss_xie"), True)
    assert levels == ""
    assert "衰颓与复兴" in clean_excerpt(overview).get_text()
    assert "洪流攻势" in clean_excerpt(overview).get_text()
    assert enemy_excerpt(fixture("boss_xie"), False)[1]


def test_source_html_controls_and_untrusted_markup_removed():
    cleaned = str(
        clean_excerpt(
            '<p>原文<span class="term">晕眩</span><span style="display: none">解释</span></p>'
            '<script>bad()</script><iframe src="http://localhost/"></iframe>'
            '<a href="javascript:bad()" onclick="bad()">技能</a>'
            '<span style="color:#ff9a9a; background-image:url(http://localhost)">1,234</span>'
        )
    )
    assert "原文" in cleaned and "晕眩" in cleaned and "1,234" in cleaned
    for bad in ("解释", "bad()", "iframe", "localhost", "onclick", "javascript"):
        assert bad not in cleaned
    assert "color:#ff9a9a" in cleaned


def fake_wiki():
    wiki = AsyncMock(spec=Wiki)

    async def request(**params):
        name = params["page"]
        stem = (
            "boss_roster"
            if name == ACTIVITY
            else "boss_zhou"
            if name == BOSS_ORDER[0]
            else "boss_xie"
        )
        return {"parse": {"text": {"*": fixture(stem)}, "revid": 123}}

    wiki.request.side_effect = request
    wiki.image.return_value = (PIXEL, "image/png")
    return wiki


def test_boss_service_avoids_mcp_and_caches_source_without_mutation(tmp_path):
    async def scenario():
        wiki = fake_wiki()
        mcp = AsyncMock()
        queries = Queries(mcp, Records(tmp_path / "aliases.json"), wiki)
        catalog = await queries.execute(parse("#卫 boss"), "user")
        assert len(BeautifulSoup(catalog.card_html, "html.parser").select(".boss")) == 10
        assert BOSS_ORDER[-1] in catalog.fallback
        hidden = await queries.execute(parse("#卫 boss 1 隐秘"), "user")
        assert "旧日的剑与锤" in hidden.fallback and "6,840,000" in hidden.fallback
        normal = await queries.execute(parse("#粥 卫 boss 1"), "user")
        assert "旧日的剑与锤" not in normal.fallback and "3,940,000" in normal.fallback
        assert "oldid=123" in normal.card_html and "CC BY-SA 3.0" in normal.card_html
        assert wiki.request.await_count == 2
        mcp.session.assert_not_called()
        for text, match in [("#卫 boss 11", "1～10"), ("#卫 boss 4 隐秘", "没有列出")]:
            with pytest.raises(ValueError, match=match):
                await queries.execute(parse(text), "user")

    run(scenario())


def test_wiki_portrait_origin_and_type_are_checked():
    def handle(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PIXEL)

    wiki = Wiki(transport=httpx.MockTransport(handle))
    assert run(wiki.image("https://media.prts.wiki/test.png")) == (PIXEL, "image/png")
    for url in (
        "http://media.prts.wiki/a.png",
        "https://localhost/a.png",
        "https://media.prts.wiki:80/a",
    ):
        with pytest.raises(BackendError, match="地址"):
            run(wiki.image(url))
    bad_wiki = Wiki(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="error")))
    with pytest.raises(BackendError, match="不是"):
        run(bad_wiki.image("https://media.prts.wiki/test.png"))


def test_onebot_local_card_and_full_text_fallback(tmp_path, monkeypatch):
    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter, GroupMessageEvent, Message

    if nonebot.get_plugin("arknights") is None:
        nonebot.init(_env_file=None)
        nonebot.get_driver().register_adapter(Adapter)
        nonebot.load_plugin("kanamibot.plugins.arknights")
    from kanamibot.plugins import arknights as plugin

    queries = Queries(AsyncMock(), Records(tmp_path / "aliases.json"), fake_wiki())
    monkeypatch.setattr(plugin, "queries", queries)
    monkeypatch.setattr(plugin, "react_ok", AsyncMock())
    render = AsyncMock(return_value=PIXEL)
    monkeypatch.setattr(plugin, "render_png", render)
    bot = SimpleNamespace(self_id="123", send=AsyncMock(), call_api=AsyncMock())
    event = GroupMessageEvent.model_construct(
        group_id=456, user_id=789, message_id=1, message=Message("#卫 boss")
    )
    assert run(plugin.matches(event))
    run(plugin.handle_query(bot, event))
    assert any(s.type == "image" for s in bot.send.await_args.args[1])
    assert render.await_count == 1
    render.side_effect = BackendError("渲染失败")
    run(plugin.handle_query(bot, event))
    assert BOSS_ORDER[0] in str(bot.send.await_args.args[1])
    assert BOSS_ORDER[-1] in str(bot.send.await_args.args[1])
