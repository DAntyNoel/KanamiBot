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


@pytest.mark.parametrize("production", [False, True])
def test_official_qq_routes_without_onebot_plugins(tmp_path, production):
    credentials = tmp_path / "credentials.txt"
    credentials.write_text("appid:123456\nsecret:test-secret", encoding="utf-8")
    script = dedent('''
        import asyncio
        import sys
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        import nonebot
        from nonebot.adapters.qq import Bot
        from nonebot.adapters.qq.event import (
            C2CMessageCreateEvent, GroupAddRobotEvent, GroupDelRobotEvent,
        )
        from nonebot.adapters.qq.models import Dispatch, User
        from nonebot.exception import NetworkError

        from qq_sandbox import create_app

        production = sys.argv[2] == "True"
        create_app(Path(sys.argv[1]), production=production)
        driver = nonebot.get_driver()
        adapter = driver._adapters["QQ"]
        expected_base = (
            "https://api.bot.qq.com/" if production else "https://sandbox.api.sgroup.qq.com/"
        )
        assert str(adapter.qq_config.qq_api_base) == expected_base
        assert adapter.qq_config.qq_verify_webhook is True
        assert adapter.qq_config.qq_bots[0].use_websocket is True
        assert {plugin.name for plugin in nonebot.get_loaded_plugins()} == {
            "ping", "permissions",
        }
        from kanamibot.qqbot.permissions import PERMISSION_GUIDE, _sent_events

        bot = Bot(adapter, "123456", adapter.qq_config.qq_bots[0])
        bot.self_info = User(id="bot-user", username="Test", bot=True)

        def group_event(command, event_type="GROUP_AT_MESSAGE_CREATE"):
            data = {
                "id": "message-id", "content": " " + command,
                "timestamp": "2026-11-19T12:00:00+08:00",
                "group_openid": "group-openid",
                "author": {"id": "user", "member_openid": "member",
                           "bot": False, "member_role": "member"},
            }
            payload = Dispatch(op=0, s=1, t=event_type, d=data)
            event = adapter.payload_to_event(payload)
            assert "group_id" not in payload.data
            return event

        async def check():
            with patch.object(Bot, "call_api", new_callable=AsyncMock) as api:
                for command in ("ping", "/ping", "#ping"):
                    event = group_event(command)
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

                await bot.handle_event(group_event("ping", "GROUP_MESSAGE_CREATE"))
                api.assert_awaited_once()
                assert api.call_args.kwargs["content"] == "pong"
                api.reset_mock()

                for command in ("权限设置", "/群权限", "#机器人权限"):
                    await bot.handle_event(group_event(command))
                    api.assert_awaited_once()
                    assert api.call_args.kwargs["content"] == PERMISSION_GUIDE
                    assert api.call_args.kwargs["msg_id"] == "message-id"
                    api.reset_mock()

                event = C2CMessageCreateEvent(
                    id="private-help", content="权限设置",
                    timestamp="2026-11-19T12:00:00+08:00",
                    author={"id": "user", "user_openid": "user"},
                )
                await bot.handle_event(event)
                api.assert_not_awaited()

                event_data = dict(
                    timestamp=1795060800, group_openid="group-openid",
                    op_member_openid="operator",
                )
                notice = GroupAddRobotEvent(event_id="join-event", **event_data)
                await bot.handle_event(notice)
                api.assert_awaited_once()
                assert api.call_args.args == ("post_group_messages",)
                assert api.call_args.kwargs["content"] == PERMISSION_GUIDE
                assert api.call_args.kwargs["event_id"] == "join-event"
                assert not api.call_args.kwargs.get("msg_id")
                assert api.call_args.kwargs["group_openid"] == "group-openid"
                api.reset_mock()
                await bot.handle_event(notice)
                api.assert_not_awaited()
                await bot.handle_event(GroupDelRobotEvent(**event_data))
                await bot.handle_event(GroupAddRobotEvent(**event_data))
                api.assert_not_awaited()

                retry_notice = GroupAddRobotEvent(event_id="retry-event", **event_data)
                api.side_effect = NetworkError("test failure")
                await bot.handle_event(retry_notice)
                assert ("123456", "group-openid", "retry-event") not in _sent_events
                api.reset_mock(side_effect=True)
                await bot.handle_event(retry_notice)
                api.assert_awaited_once()
                assert ("123456", "group-openid", "retry-event") in _sent_events

                from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter
                from nonebot.adapters.onebot.v11 import Bot as OneBot
                from nonebot.adapters.onebot.v11 import GroupMessageEvent
                from nonebot.adapters.onebot.v11 import Message as OneBotMessage

                driver.register_adapter(OneBotAdapter)
                onebot = OneBot(driver._adapters["OneBot V11"], "10000")
                onebot_event = GroupMessageEvent(
                    time=1795060800, self_id=10000, post_type="message",
                    message_type="group", sub_type="normal", message_id=1,
                    user_id=20000, group_id=30000,
                    message=OneBotMessage("权限设置"), raw_message="权限设置",
                    font=0, sender={"user_id": 20000, "role": "owner"},
                )
                api.reset_mock()
                with patch.object(OneBot, "call_api", new_callable=AsyncMock) as onebot_api:
                    await onebot.handle_event(onebot_event)
                    onebot_api.assert_not_awaited()
                api.assert_not_awaited()

        asyncio.run(check())
    ''')
    result = subprocess.run(
        [sys.executable, "-c", script, str(credentials), str(production)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
