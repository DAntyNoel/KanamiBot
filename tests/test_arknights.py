from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from kanamibot.arknights.client import MCP, BackendError, decode_tool
from kanamibot.arknights.commands import Command, command_body, parse
from kanamibot.arknights.records import Records
from kanamibot.arknights.service import Queries
from kanamibot.arknights.wiki import Wiki


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("head", ["#粥", "#方舟", "#ark", "#ARK"])
def test_entity_first_heads(head):
    assert parse(f"{head} 德克萨斯 异格 技能") == Command(
        name="德克萨斯",
        point="skills",
        alter=True,
    )
    assert parse(f"{head} 国度 敌人") == Command(name="国度", point="enemy")
    assert command_body("#国度 敌人") is None
    assert command_body("#arknights 银灰") is None


@pytest.mark.parametrize("word", ["卫", "卫戍", "卫戍协议"])
def test_autochess_aliases(word):
    assert parse(f"#粥 {word} 炎 盟约") == Command(name="炎", point="bond", mode="卫戍")
    assert parse(f"#粥 银灰 {word}") == Command(name="银灰", mode="卫戍")


@pytest.mark.parametrize("word", ["盐酸", "演算", "生息演算"])
def test_reclamation_aliases(word):
    assert parse(f"#粥 {word} 餐食").mode == "演算"


def test_nickname_grammar():
    assert parse("#粥 绰号 缄默德克萨斯 异德") == Command(
        action="alias_add",
        name="缄默德克萨斯",
        alias="异德",
    )
    assert parse("#粥 绰号 易得 删除") == Command(action="alias_delete", alias="易得")
    assert parse("#粥 绰号 列表").action == "alias_list"


def test_nickname_persistence_tombstones_and_factions(tmp_path):
    path = tmp_path / "aliases.json"
    records = Records(path)
    assert records.resolve("异德") == "缄默德克萨斯"
    records.add("缄默德克萨斯", "易得")
    records = Records(path)
    assert records.resolve("易得") == "缄默德克萨斯"
    records.delete("异德")
    records.delete("易得")
    records = Records(path)
    assert records.resolve("异德") == "异德"
    assert records.resolve("易得") == "易得"
    records.add("缄默德克萨斯", "异德")
    assert not records.deleted("异德")
    messages = records.list_messages()
    assert len(messages) > 5
    assert all(message.startswith("【") and len(message) <= 1800 for message in messages)
    assert any("缄默德克萨斯：" in message and "异德" in message for message in messages)
    assert records.alters("德克萨斯") == ["缄默德克萨斯"]
    assert records.alters("异德") == ["缄默德克萨斯"]
    assert len(records.alters("陈")) >= 2
    assert not records.alters("桃金娘")


def test_nickname_conflicts_and_corrupt_file(tmp_path):
    records = Records(tmp_path / "aliases.json")
    for alias in ["银灰", "技能", "#foo", "two words", "[CQ:at,qq=all]"]:
        with pytest.raises(ValueError):
            records.add("缄默德克萨斯", alias)
    with pytest.raises(ValueError, match="已属于"):
        records.add("银灰", "异德")
    records.path.write_text("broken", "utf-8")
    with pytest.raises(ValueError):
        records.add("银灰", "新绰号")
    assert records.path.read_text("utf-8") == "broken"


class FakeBackend:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    @asynccontextmanager
    async def session(self):
        yield self

    async def call(self, tool, args):
        self.calls.append((tool, args))
        if tool == "search":
            return {"data": {"items": self.rows.get(args["query"], [])}}
        if tool == "get_operator_skill":
            return {
                "data": {
                    "技能": [
                        {
                            "名称": "测试技能",
                            "等级": [
                                {"游戏内等级": "专精三", "描述": "伤害提升", "解包等级": 10},
                            ],
                        }
                    ]
                }
            }
        return {"data": {"name": args}, "card_image_url": "http://localhost/card.png"}


def test_alter_and_nickname_use_identical_tool_arguments(tmp_path):
    backend = FakeBackend(
        {
            "缄默德克萨斯": [
                {"id": "texas2", "name": "缄默德克萨斯", "type": "干员"},
            ]
        }
    )
    queries = Queries(backend, Records(tmp_path / "aliases.json"))
    altered = run(queries.execute(parse("#粥 德克萨斯 异格 技能"), "user"))
    nickname = run(queries.execute(parse("#粥 异德 技能"), "user"))
    direct = run(queries.execute(parse("#粥 缄默德克萨斯 技能"), "user"))
    assert altered.pages == nickname.pages == direct.pages
    assert "解包" not in direct.pages[0]
    assert backend.calls[-1] == ("get_operator_skill", {"operator_id": "texas2"})


def test_enemy_disambiguation_and_deleted_upstream_alias(tmp_path):
    backend = FakeBackend(
        {
            "国度": [
                {"id": "char", "name": "国度", "type": "干员"},
                {"id": "enemy", "name": "国度", "type": "敌人"},
            ],
            "易得": [
                {"id": "texas2", "name": "缄默德克萨斯", "type": "干员", "from_alias": "易得"},
            ],
        }
    )
    records = Records(tmp_path / "aliases.json")
    queries = Queries(backend, records)
    run(queries.execute(parse("#粥 国度 敌人"), "user"))
    assert backend.calls[-1] == ("get_enemy_data", {"enemy_id": "enemy"})
    run(queries.execute(parse("#粥 国度"), "user"))
    assert backend.calls[-1] == ("get_operator_basic_data", {"operator_id": "char"})
    records.delete("易得")
    result = run(queries.execute(parse("#粥 易得"), "user"))
    assert "未找到" in result.text


def test_multiple_alters_selection_scoped_and_expiring(tmp_path):
    records = Records(tmp_path / "aliases.json")
    targets = records.alters("陈")
    backend = FakeBackend(
        {
            name: [{"id": str(index), "name": name, "type": "干员"}]
            for index, name in enumerate(targets)
        }
    )
    queries = Queries(backend, records)
    result = run(queries.execute(parse("#粥 陈 异格 技能"), "group1:user1"))
    assert len(result.choices) >= 2
    assert "过期" in run(queries.execute(parse("#粥 选择 1"), "group2:user1")).text
    run(queries.execute(parse("#粥 选择 2"), "group1:user1"))
    assert backend.calls[-1] == ("get_operator_skill", {"operator_id": "1"})
    queries.pending["expired"] = (0, Command(), result.choices)
    assert "过期" in run(queries.execute(parse("#粥 选择 1"), "expired")).text


def test_autochess_routes_and_rogue_not_enabled(tmp_path):
    backend = FakeBackend({"炎": [{"id": "yan", "name": "炎", "type": "卫戍协议盟约"}]})
    queries = Queries(backend, Records(tmp_path / "aliases.json"))
    run(queries.execute(parse("#粥 卫 炎 盟约"), "user"))
    assert backend.calls[-1] == ("get_autochess_bond_detail", {"bond_id": "yan"})
    backend.rows["真理"] = [{"id": "istina", "name": "真理", "type": "干员"}]
    run(queries.execute(parse("#粥 真理 卫 技能"), "user"))
    assert backend.calls[-1] == ("get_operator_basic_data", {"operator_id": "istina"})
    calls = len(backend.calls)
    assert "统计" in run(queries.execute(parse("#粥 肉鸽"), "user")).text
    assert len(backend.calls) == calls


@pytest.mark.parametrize("sse", [True, False])
def test_mcp_real_wire_handshake_and_cleanup(sse):
    calls = []

    def handle(request):
        if request.method == "DELETE":
            calls.append("DELETE")
            return httpx.Response(200)
        body = json.loads(request.content)
        calls.append(body["method"])
        if body["method"] == "notifications/initialized":
            assert request.headers["mcp-session-id"] == "session"
            return httpx.Response(202)
        result = (
            {"protocolVersion": "2025-03-26"}
            if body["method"] == "initialize"
            else {
                "structuredContent": {"data": {"items": [{"id": "silverash"}]}},
            }
        )
        payload = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        headers = {"Mcp-Session-Id": "session"}
        if sse:
            headers["Content-Type"] = "text/event-stream"
            return httpx.Response(
                200, headers=headers, text="event: message\ndata: " + json.dumps(payload) + "\n\n"
            )
        return httpx.Response(200, headers=headers, json=payload)

    async def scenario():
        backend = MCP("http://localhost/mcp", transport=httpx.MockTransport(handle))
        async with backend.session() as session:
            data = await session.call("search", {"query": "银灰"})
            assert data["data"]["items"][0]["id"] == "silverash"

    run(scenario())
    assert calls == ["initialize", "notifications/initialized", "tools/call", "DELETE"]


def test_mcp_payload_errors_and_origin_restriction():
    assert decode_tool({"content": [{"type": "text", "text": '{"data":{"ok":true}}'}]})
    assert decode_tool({"structuredContent": {"result": '{"攻击力":"说明"}'}}) == {"攻击力": "说明"}
    for result in [{"isError": True}, {"structuredContent": {"message": "未就绪"}}, {}]:
        with pytest.raises(BackendError):
            decode_tool(result)
    with pytest.raises(BackendError, match="地址"):
        run(MCP("http://localhost/mcp").image("http://other-host/card.png"))


def test_wiki_reclamation_selection_and_plaintext(tmp_path):
    backend = FakeBackend({})
    wiki = AsyncMock()
    wiki.search.return_value = [{"id": "生息演算/餐食", "name": "餐食", "type": "Wiki"}]
    wiki.detail.return_value = "餐食配方"
    queries = Queries(backend, Records(tmp_path / "aliases.json"), wiki)
    assert run(queries.execute(parse("#粥 盐酸 餐食"), "u")).text == "餐食配方"
    wiki.search.assert_awaited_once_with("餐食", reclamation=True)
    assert not backend.calls

    def handle(request):
        return httpx.Response(
            200,
            json={
                "parse": {
                    "text": {
                        "*": (
                            "<p>内容</p><script>secret()</script>"
                            "<table><tr><td>配方</td><td>效果</td></tr></table>"
                        )
                    }
                }
            },
        )

    result = run(Wiki(transport=httpx.MockTransport(handle)).detail("餐食"))
    assert "配方" in result and "效果" in result and "来源：PRTS" in result
    assert "secret" not in result and "<td>" not in result


def test_single_fuzzy_candidate_needs_user_selection(tmp_path):
    backend = FakeBackend({"银会": [{"id": "silverash", "name": "银灰", "type": "干员"}]})
    queries = Queries(backend, Records(tmp_path / "aliases.json"))
    result = run(queries.execute(parse("#粥 银会 技能"), "user"))
    assert len(result.choices) == 1
    assert not any(tool == "get_operator_skill" for tool, _ in backend.calls)


def test_mode_can_follow_operator_before_point():
    assert parse("#粥 银灰 卫 棋子") == Command(name="银灰", point="chess", mode="卫戍")
    with pytest.raises(ValueError, match="干员"):
        parse("#粥 德克萨斯 异格 敌人")


def test_onebot_handler_and_faction_forward_delivery(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter, GroupMessageEvent, Message

    nonebot.init(_env_file=None)
    nonebot.get_driver().register_adapter(Adapter)
    assert nonebot.load_plugin("kanamibot.plugins.arknights")
    from kanamibot.plugins import arknights as plugin

    backend = FakeBackend(
        {
            "缄默德克萨斯": [
                {"id": "texas2", "name": "缄默德克萨斯", "type": "干员"},
            ]
        }
    )
    queries = Queries(backend, Records(tmp_path / "aliases.json"))
    monkeypatch.setattr(plugin, "queries", queries)
    monkeypatch.setattr(plugin, "react_ok", AsyncMock())
    bot = SimpleNamespace(self_id="123", send=AsyncMock(), call_api=AsyncMock())
    event = GroupMessageEvent.model_construct(
        group_id=456,
        user_id=789,
        message_id=1,
        message=Message("#粥 德克萨斯 异格 技能"),
    )
    run(plugin.handle_query(bot, event))
    assert backend.calls[-1] == ("get_operator_skill", {"operator_id": "texas2"})
    assert bot.send.await_count == 1
    event.message = Message("#粥 绰号 列表")
    run(plugin.handle_query(bot, event))
    pages = queries.records.list_messages()
    assert bot.call_api.await_count == (len(pages) + 34) // 35
    for call in bot.call_api.await_args_list:
        assert call.args[0] == "send_group_forward_msg" and call.kwargs["group_id"] == 456
        assert all(node.type == "node" for node in call.kwargs["messages"])
    monkeypatch.setattr(
        backend, "image", AsyncMock(side_effect=BackendError("卡片不可用")), raising=False
    )
    from kanamibot.arknights.service import Result

    run(
        plugin.send_result(
            bot, event, Result(text="银灰", card_url="/card.png", fallback="天赋资料")
        )
    )
    assert "天赋资料" in str(bot.send.await_args.args[1])
