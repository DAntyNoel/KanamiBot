from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock

import httpx
import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Adapter, Bot, GroupMessageEvent, Message
from nonebot.message import handle_event

from kanamibot.core import group_manager, paths


@pytest.fixture(scope="module")
def majsoul(tmp_path_factory):
    nonebot.init(_env_file=None)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(paths, "DATA_DIR", tmp_path_factory.mktemp("majsoul"))
        assert nonebot.load_plugin("kanamibot.plugins.majsoul") is not None
    return importlib.import_module("kanamibot.plugins.majsoul")


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (429, "[x-cap-token-required] private upstream text", "要求接口授权"),
        (429, "private upstream text", "请求太频繁"),
        (403, "private upstream text", "拒绝访问"),
        (404, '{"error":"id_not_found"}', "未找到对应数据"),
        (502, "private upstream text", "HTTP 502"),
        (200, "private upstream text", "数据格式有误"),
    ],
)
def test_paipu_route_replies_to_upstream_errors(majsoul, monkeypatch, status, body, expected):
    monkeypatch.delenv("MAJSOUL_API_KEY", raising=False)
    monkeypatch.setattr(majsoul.source, "get_playerid", lambda _: 33336)
    group_id = 987654321
    group_config = {str(group_id): {"modules": {}, "blacklist": []}}
    monkeypatch.setattr(group_manager.group_config, "config", group_config)
    transport = httpx.MockTransport(lambda _: httpx.Response(status, text=body))
    client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(majsoul.source.httpx, "AsyncClient", lambda **_: client)
    call_api = AsyncMock()
    monkeypatch.setattr(Bot, "call_api", call_api)
    bot = Bot(Adapter(nonebot.get_driver()), "10001")
    message = Message("qhpaipu 测试昵称 4 1")
    event = GroupMessageEvent(
        time=0,
        self_id=10001,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=1,
        user_id=10002,
        group_id=group_id,
        message=message,
        original_message=message,
        raw_message=str(message),
        font=0,
        sender={"user_id": 10002, "role": "member"},
    )

    asyncio.run(handle_event(bot, event))

    call_api.assert_awaited_once()
    assert call_api.call_args.args == ("send_msg",)
    reply = call_api.call_args.kwargs["message"]
    assert reply[0].type == "at"
    assert expected in reply.extract_plain_text()
    assert "private upstream text" not in reply.extract_plain_text()


def test_key_is_sent_only_to_https_data_nodes_with_tls_verification(majsoul, monkeypatch):
    monkeypatch.setenv("MAJSOUL_API_KEY", "test-secret")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=[{"uuid": "match"}])

    def make_client(**kwargs):
        assert kwargs["verify"] is True
        return client

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(majsoul.source.httpx, "AsyncClient", make_client)
    wait = AsyncMock()
    monkeypatch.setattr(majsoul.source, "_wait_for_api_slot", wait)

    async def run():
        connector = majsoul.source.TCPConnector(ssl=False)
        async with majsoul.source.ClientSession(connector=connector) as session:
            for url in (
                "https://5.data.amae-koromo.com/records",
                "http://5.data.amae-koromo.com/records",
                "https://5.data.amae-koromo.com.example.com/records",
            ):
                async with session.get(url) as response:
                    assert await response.json() == [{"uuid": "match"}]

    asyncio.run(run())

    authorization = [request.headers.get("Authorization") for request in requests]
    assert authorization == ["Bearer test-secret", None, None]
    wait.assert_awaited_once()
