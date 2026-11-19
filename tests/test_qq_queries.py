from __future__ import annotations

import subprocess
import sys
from textwrap import dedent


def test_official_game_query_routes(tmp_path):
    script = dedent('''
        import asyncio
        import json
        import sys
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        import nonebot
        from nonebot.adapters.qq import Bot
        from nonebot.adapters.qq.event import C2CMessageCreateEvent
        from nonebot.adapters.qq.models import Dispatch, User
        from qq_sandbox import create_app

        root = Path(sys.argv[1])
        credentials = root / "credentials.txt"
        credentials.write_text("appid:123456\\nsecret:test-secret")
        create_app(credentials)
        driver = nonebot.get_driver()
        adapter = driver._adapters["QQ"]
        bot = Bot(adapter, "123456", adapter.qq_config.qq_bots[0])
        bot.self_info = User(id="bot", username="Test", bot=True)
        assert "nonebot_plugin_apscheduler" not in {
            plugin.name for plugin in nonebot.get_loaded_plugins()
        }
        from kanamibot.core import query_transport as transport
        from kanamibot.core import gallery_transport
        from kanamibot.plugins import strinova, er_dak
        from kanamibot.qqbot import query_bindings
        from kanamibot.qqbot.image import build_test_image
        transport.DATA_PATH = root / "group_manager.json"
        gallery_transport.CONFIG_PATH = root / "mapping.json"
        query_bindings.PATH = root / "qq_bindings.json"
        sent = []

        def event(command, private=False, group="group", user="user"):
            data = {"id": "incoming", "content": command,
                    "timestamp": "2026-11-19T14:00:00+08:00"}
            if private:
                return C2CMessageCreateEvent(**data, author={"id": user, "user_openid": user})
            data.update(group_openid=group, author={"id": user, "member_openid": user,
                                                  "bot": False, "member_role": "member"})
            return adapter.payload_to_event(
                Dispatch(op=0, s=1, t="GROUP_AT_MESSAGE_CREATE", d=data)
            )

        async def api(name, **kwargs):
            if name in {"post_group_files", "post_c2c_files"}:
                assert kwargs["file_data"]
                assert kwargs["srv_send_msg"] is False
                return SimpleNamespace(file_info="test-media")
            assert name in {"post_group_messages", "post_c2c_messages"}, name
            assert kwargs["msg_id"] == "incoming"
            assert kwargs["msg_seq"] >= 1
            sent.append(kwargs)
            return SimpleNamespace(id="sent")

        async def command(text, *, private=False, reset=True, **kwargs):
            if reset:
                er_dak._user_cooldowns.clear()
                er_dak._group_cooldowns.clear()
            previous = len(sent)
            await bot.handle_event(event(text, private, **kwargs))
            assert len(sent) > previous, text
            return sent[-1]

        async def check():
            renderer = SimpleNamespace(render=AsyncMock(return_value=build_test_image()))
            detail_renderer = SimpleNamespace(
                render=AsyncMock(return_value=build_test_image()), close=AsyncMock()
            )
            service = SimpleNamespace(
                search_player_card=AsyncMock(return_value={"card": "player"}),
                character_analysis_card=AsyncMock(return_value={"card": "character"}),
                tier_statistics_card=AsyncMock(return_value={"card": "tier"}),
                item_card=AsyncMock(return_value={"card": "item"}),
                player_overview=AsyncMock(return_value={"card": "overview"}),
                published_player_summary=AsyncMock(return_value={"card": "summary"}),
            )
            runtime = SimpleNamespace(client=SimpleNamespace(config=None), service=service,
                                      renderer=renderer)
            for name in ("rank_card", "stats_card", "recent_card", "characters_card",
                         "skins_card", "teammates_card", "best_match_card", "hero_pool_card",
                         "equipment_card", "radar_card"):
                setattr(service, name, AsyncMock(return_value={"card": name}))
            with patch.object(Bot, "call_api", side_effect=api), patch.object(
                strinova, "check_available", new_callable=AsyncMock
            ) as health, patch.object(
                strinova, "create_report", new_callable=AsyncMock
            ) as report, patch.object(
                strinova, "render_png", new_callable=AsyncMock, return_value=build_test_image()
            ), patch.object(
                strinova, "create_leaderboard", new_callable=AsyncMock
            ), patch.object(
                strinova, "render_leaderboard_png", new_callable=AsyncMock,
                return_value=build_test_image()
            ), patch.object(
                er_dak, "_get_runtime", new_callable=AsyncMock, return_value=runtime
            ), patch.object(
                er_dak, "detail_card", new_callable=AsyncMock, return_value={"card": "match"}
            ), patch.object(
                er_dak, "MatchDetailRenderer", return_value=detail_renderer
            ), patch.object(er_dak, "annotate_query_payload", side_effect=lambda value: value):
                for private in (False, True):
                    for text in ("#卡丘战绩 玩家", "#卡丘资料 玩家", "#卡丘状态 玩家",
                                 "#卡丘生涯 玩家", "#卡丘赛季 玩家 SP3", "#卡丘名片 玩家",
                                 "#卡丘赛季列表", "#卡丘战绩 玩家 对局战绩 2"):
                        assert (await command(text, private=private))["msg_type"] == 7
                    for text in ("#卡丘奇点", "#卡丘排名 玩家", "#卡丘角色榜 香奈美"):
                        assert (await command(text, private=private))["msg_type"] == 7
                    help_reply = await command("#卡丘战绩 帮助", private=private)
                    assert "卡丘战绩" in help_reply["content"]
                    for text in ("#BS战绩 玩家", "#永恒轮回战绩 玩家 排位",
                                 "#BS角色 席琳 金魄", "#BS 永恒多少分 国服",
                                 "#BS 物品 lieyang", "#BS评价 玩家"):
                        assert (await command(text, private=private))["msg_type"] == 7
                    assert "永恒轮回查询" in (await command("#BS 帮助", private=private))["content"]
                    assert "DAK.GG" in (await command("#BS 来源", private=private))["content"]
                    pet_reply = await command("#BS 群宠 列表", private=private)
                    assert "暂未接入" in pet_reply["content"]
                    assert "已绑定" in (await command("#BS 绑定 玩家", private=private))["content"]
                    assert (await command("#BS 查询", private=private))["msg_type"] == 7
                    assert "请提供昵称" in (await command("#BS 查询", private=private,
                                                        user="other"))["content"]
                    assert "已解绑" in (await command("#BS 解绑", private=private))["content"]
                    assert (await command("#BS 对局 123 玩家 37", private=private))["msg_type"] == 7
                assert detail_renderer.close.await_count == 2
                assert report.await_count == 16
                assert health.await_count >= 22
                await command("#BS战绩 玩家")
                assert "冷却" in (await command("#BS战绩 玩家", reset=False))["content"]
                with patch.object(strinova, "check_available",
                                  side_effect=strinova.QueryError("离线")):
                    assert "离线" in (await command("#卡丘战绩 玩家"))["content"]
                with patch.object(service, "search_player_card",
                                  side_effect=er_dak.PlayerNotFound()):
                    assert "未找到" in (await command("#BS战绩 无此玩家"))["content"]
                with patch.object(strinova, "query_client_versions", new_callable=AsyncMock,
                                  return_value="版本测试"):
                    assert "版本测试" in (await command("#卡丘客户端"))["content"]
                group = str(transport.group_id(event("")))
                transport.DATA_PATH.write_text(json.dumps({group: {
                    "modules": {"strinova": False, "er_dak": False}, "blacklist": []
                }}))
                previous = len(sent)
                await bot.handle_event(event("#卡丘奇点"))
                await bot.handle_event(event("#BS战绩 玩家"))
                assert len(sent) == previous
                assert (await command("#BS战绩 玩家", private=True))["msg_type"] == 7
                from contextlib import asynccontextmanager
                @asynccontextmanager
                async def expired(seconds):
                    assert seconds == 240
                    raise TimeoutError
                    yield
                with patch.object(transport.asyncio, "timeout", expired):
                    assert "超过4分钟" in (await command("#BS战绩 玩家", private=True))["content"]

        asyncio.run(check())
    ''')
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
