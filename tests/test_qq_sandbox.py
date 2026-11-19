from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from qq_sandbox import read_credentials


@pytest.mark.parametrize("content", [
    "appid:123456\nsecret:test-secret",
    "AppID=123456\nAppSecret=test-secret",
    "appid：123456\nsecret：test-secret",
    "123456\ntest-secret",
])
def test_credentials_formats(tmp_path, content):
    credentials = tmp_path / "credentials.txt"
    credentials.write_text(content, encoding="utf-8-sig")
    assert read_credentials(credentials) == ("123456", "test-secret")


def test_invalid_credentials_do_not_disclose_secret(tmp_path):
    credentials = tmp_path / "credentials.txt"
    credentials.write_text("appid:invalid\nsecret:private-value", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        read_credentials(credentials)
    assert "private-value" not in str(error.value)


def test_official_qq_ping_routes_without_other_plugins(tmp_path):
    credentials = tmp_path / "credentials.txt"
    credentials.write_text("appid:123456\nsecret:test-secret", encoding="utf-8")
    script = dedent('''
        import asyncio
        import sys
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        import nonebot
        from nonebot.adapters.qq import Bot
        from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent
        from nonebot.adapters.qq.models import User

        from qq_sandbox import create_app

        create_app(Path(sys.argv[1]))
        driver = nonebot.get_driver()
        adapter = driver._adapters["QQ"]
        assert str(adapter.qq_config.qq_api_base) == "https://sandbox.api.sgroup.qq.com/"
        assert adapter.qq_config.qq_verify_webhook is True
        assert adapter.qq_config.qq_bots[0].use_websocket is True
        assert {plugin.name for plugin in nonebot.get_loaded_plugins()} == {"ping"}
        bot = Bot(adapter, "123456", adapter.qq_config.qq_bots[0])
        bot.self_info = User(id="bot-user", username="Test", bot=True)

        async def check():
            with patch.object(Bot, "call_api", new_callable=AsyncMock) as api:
                for command in ("ping", "/ping", "#ping"):
                    event = GroupAtMessageCreateEvent(
                        id="message-id", content=" " + command,
                        timestamp="2026-11-19T12:00:00+08:00",
                        group_id="group", group_openid="group-openid",
                        author={"id": "user", "member_openid": "member",
                                "bot": False, "member_role": "member"},
                    )
                    await bot.handle_event(event)
                    api.assert_awaited_once()
                    assert api.call_args.args == ("post_group_messages",)
                    assert api.call_args.kwargs["content"] == "pong"
                    assert api.call_args.kwargs["msg_id"] == "message-id"
                    assert api.call_args.kwargs["group_openid"] == "group-openid"
                    api.reset_mock()
                event = C2CMessageCreateEvent(
                    id="private-id", content="/ping",
                    timestamp="2026-11-19T12:00:00+08:00",
                    author={"id": "user", "user_openid": "user"},
                )
                await bot.handle_event(event)
                api.assert_awaited_once()
                assert api.call_args.args == ("post_c2c_messages",)
                assert api.call_args.kwargs["content"] == "pong"
                assert api.call_args.kwargs["msg_id"] == "private-id"
                api.reset_mock()
                event.content = "unknown-command"
                del event.message
                await bot.handle_event(event)
                api.assert_not_awaited()

        asyncio.run(check())
    ''')
    result = subprocess.run(
        [sys.executable, "-c", script, str(credentials)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
