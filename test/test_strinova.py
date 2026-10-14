from __future__ import annotations

import importlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from kanamibot.strinova.client import APIError, ServiceUnavailable, Settings, StrinovaClient
from kanamibot.strinova.query import VIEWS, Query, QueryError, parse_query
from kanamibot.strinova.render import match_row, render_html, report_context
from kanamibot.strinova.service import Report, fetch_report, resolve_player

PLAYER = {"player_id": 123456, "nick": "测试玩家", "level": 12, "rank": 22}
ROOM_ID = "751028091756896271"


def response(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "data": {"code": 0, **data}})


def healthy_transport(handler) -> httpx.MockTransport:
    def route(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        return handler(request)

    return httpx.MockTransport(route)


class QueryTests(unittest.TestCase):
    def test_numeric_nickname_can_be_explicit_and_names_can_contain_spaces(self):
        self.assertFalse(parse_query("--nick 12345").by_id)
        self.assertTrue(parse_query("12345").by_id)
        self.assertEqual(parse_query('"Player Name" --index 2').target, "Player Name")
        self.assertEqual(parse_query("Player Name --index 2").target, "Player Name")

    def test_room_id_keeps_integer_precision_and_rejects_path_input(self):
        self.assertEqual(parse_query(f"测试玩家 --view match --room-id {ROOM_ID}").room_id, ROOM_ID)
        for text in (
            "测试玩家 --view match",
            "测试玩家 --room-id ../private",
            "测试玩家 --index 0",
            "测试玩家 --limit 999",
            "测试玩家 --season-id -1",
            "--player-id 0",
            "测试玩家 --player-id 123",
            "测试玩家 --unknown",
            '"unclosed',
        ):
            with self.subTest(text=text), self.assertRaises(QueryError):
                parse_query(text)

    def test_settings_reject_bad_urls_and_nonfinite_timeout(self):
        for values in (
            {"base_url": "file:///tmp/x"},
            {"timeout": float("nan")},
            {"base_url": "http://secret@example.org"},
            {"timeout": 0},
        ):
            with self.subTest(values=values), self.assertRaises(QueryError):
                Settings(**values)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_real_routes_and_filters_are_encoded_without_float_room_ids(self):
        requests = []

        def handler(request):
            requests.append(request)
            return response({})

        async with StrinovaClient(Settings(), transport=healthy_transport(handler)) as client:
            await client.search(nick="测试玩家 & ?")
            await client.search(player_id="123456")
            await client.profile("123456")
            await client.status("123456")
            await client.career("123456")
            await client.battle_data("123456", 0, 1)
            await client.history("123456", 25)
            await client.standings("123456", 2, 5, 0, 2)
            await client.match("123456", ROOM_ID)

        self.assertEqual(len(requests), 9)
        self.assertEqual(requests[0].url.params["nick"], "测试玩家 & ?")
        self.assertEqual(requests[1].url.params["player_id"], "123456")
        self.assertEqual(
            [request.url.path for request in requests[2:]],
            [
                "/v1/players/123456",
                "/v1/players/123456/status",
                "/v1/players/123456/career",
                "/v1/players/123456/career/battle-data",
                "/v1/players/123456/career/history",
                "/v1/players/123456/standings",
                f"/v1/players/123456/standings/{ROOM_ID}",
            ],
        )
        self.assertEqual(dict(requests[5].url.params), {"season_id": "0", "season_type": "1"})
        self.assertEqual(dict(requests[6].url.params), {"season_id": "25"})
        self.assertEqual(
            dict(requests[7].url.params),
            {"index": "2", "game_mode": "5", "room_mode_type": "0", "play_mode": "2"},
        )

    async def test_http_success_does_not_turn_business_failure_into_zero_stats(self):
        for payload in (
            {"ok": False, "error": "private internal error"},
            {"ok": True, "data": {"code": 2, "battle_infos": []}},
            {"ok": True, "data": []},
            [],
        ):
            with self.subTest(payload=payload):
                transport = healthy_transport(
                    lambda _, payload=payload: httpx.Response(200, json=payload)
                )
                async with StrinovaClient(Settings(), transport=transport) as client:
                    with self.assertRaises(APIError):
                        await client.history("123456", 25)

    async def test_timeout_http_error_and_html_have_readable_failures(self):
        def timeout(request):
            raise httpx.ReadTimeout("internal error", request=request)

        for handler in (
            timeout,
            lambda _: httpx.Response(503),
            lambda _: httpx.Response(200, text="<html>unavailable</html>"),
        ):
            async with StrinovaClient(Settings(), transport=healthy_transport(handler)) as client:
                with self.assertRaises(APIError):
                    await client.profile("123456")

    async def test_pending_bridge_error_is_explained_without_leaking_stack_trace(self):
        transport = healthy_transport(
            lambda _: httpx.Response(
                200,
                json={
                    "ok": False,
                    "data": {"error": "in-game career request is still pending\nprivate trace"},
                },
            )
        )
        async with StrinovaClient(Settings(), transport=transport) as client:
            with self.assertRaisesRegex(APIError, "仍在处理中") as caught:
                await client.career("123456")
        self.assertNotIn("private trace", str(caught.exception))

    async def test_invalid_output_is_rejected_before_any_api_request(self):
        from kanamibot.strinova import cli

        args = cli.build_parser().parse_args(["测试玩家", "-o", "result.html"])
        with patch.object(cli, "create_report", AsyncMock()) as create:
            with self.assertRaisesRegex(QueryError, ".png"):
                await cli.run(args)
            create.assert_not_awaited()


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_health_blocks_every_view_before_player_requests(self):
        for view in VIEWS:
            paths = []

            def handler(request, paths=paths):
                paths.append(request.url.path)
                return httpx.Response(503)

            async with StrinovaClient(Settings(), transport=httpx.MockTransport(handler)) as client:
                with self.subTest(view=view), self.assertRaises(ServiceUnavailable):
                    await fetch_report(client, Query("测试玩家", view=view, room_id=ROOM_ID))
            self.assertEqual(paths, ["/health"])

    async def test_bad_json_ok_and_bridge_readiness_are_unavailable(self):
        for payload in (
            None,
            [],
            {},
            {"ok": False},
            {"ok": True, "hooked": False},
            {"ok": True, "lua_ready": False},
            {"ok": True, "game_tick_seen": False},
        ):
            transport = httpx.MockTransport(
                lambda _, payload=payload: httpx.Response(200, json=payload)
            )
            async with StrinovaClient(Settings(), transport=transport) as client:
                with self.subTest(payload=payload), self.assertRaises(ServiceUnavailable):
                    await client.check_health()

    async def test_health_timeout_and_connection_failure_block_queries(self):
        def disconnected(request):
            raise httpx.ConnectError("internal trace", request=request)

        def timeout(request):
            raise httpx.ReadTimeout("internal trace", request=request)

        for handler in (disconnected, timeout, lambda _: httpx.Response(200, text="<html>")):
            async with StrinovaClient(Settings(), transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(ServiceUnavailable) as caught:
                    await client.check_health()
                self.assertNotIn("internal trace", str(caught.exception))

    async def test_health_recovers_on_next_query_without_persisted_disabled_flag(self):
        healthy = False
        paths = []

        def handler(request):
            paths.append(request.url.path)
            if request.url.path == "/health":
                return httpx.Response(200, json={"ok": healthy})
            return response({"friends": [PLAYER]})

        async with StrinovaClient(Settings(), transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ServiceUnavailable):
                await resolve_player(client, Query("测试玩家"))
            healthy = True
            self.assertEqual(await resolve_player(client, Query("测试玩家")), PLAYER)
        self.assertEqual(paths, ["/health", "/health", "/v1/friends/search"])

    async def test_health_loss_mid_query_aborts_instead_of_partial_image(self):
        health_checks = 0
        paths = []

        def handler(request):
            nonlocal health_checks
            paths.append(request.url.path)
            if request.url.path == "/health":
                health_checks += 1
                return httpx.Response(200, json={"ok": health_checks < 3})
            if request.url.path == "/v1/friends/search":
                return response({"friends": [PLAYER]})
            return response({"friend": PLAYER})

        async with StrinovaClient(Settings(), transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ServiceUnavailable):
                await fetch_report(client, Query("测试玩家"))
        self.assertEqual(
            paths, ["/health", "/v1/friends/search", "/health", "/v1/players/123456", "/health"]
        )

    async def test_health_uses_short_timeout(self):
        for configured, expected in ((35, 5), (2, 2)):
            timeouts = []

            def handler(request, timeouts=timeouts):
                timeouts.append(request.extensions["timeout"]["read"])
                return httpx.Response(200, json={"ok": True})

            async with StrinovaClient(
                Settings(timeout=configured), transport=httpx.MockTransport(handler)
            ) as client:
                await client.check_health()
            self.assertEqual(timeouts, [expected])


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_nickname_selection_and_ambiguous_search(self):
        client = SimpleNamespace(
            search=AsyncMock(
                return_value={"friends": [PLAYER, {"player_id": 999, "nick": "测试玩家的朋友"}]}
            )
        )
        self.assertEqual(await resolve_player(client, Query("测试玩家")), PLAYER)
        with self.assertRaisesRegex(QueryError, "明确选择"):
            await resolve_player(client, Query("测试"))
        client.search.return_value = {"friends": []}
        with self.assertRaisesRegex(QueryError, "未找到"):
            await resolve_player(client, Query("不存在"))

    async def test_optional_failure_preserves_available_data_and_warning(self):
        client = SimpleNamespace(
            search=AsyncMock(return_value={"friends": [PLAYER]}),
            profile=AsyncMock(return_value={"friend": PLAYER}),
            status=AsyncMock(side_effect=APIError("API 请求超时")),
            career=AsyncMock(return_value={"career_info": {"battle_infos": []}}),
            standings=AsyncMock(return_value={"titles": []}),
        )
        report = await fetch_report(client, Query("测试玩家"))
        self.assertEqual(report.player_id, "123456")
        self.assertIn("standings", report.sections)
        self.assertNotIn("status", report.sections)
        self.assertIn("当前状态", report.warnings[0])
        html = render_html(report)
        self.assertIn("请求超时", html)
        self.assertIn("这一页没有返回对局记录", html)
        self.assertIn("公开对局数", html)
        self.assertNotIn("0.0%", html)

    async def test_profile_identity_mismatch_is_rejected(self):
        client = SimpleNamespace(
            search=AsyncMock(return_value={"friends": [PLAYER]}),
            profile=AsyncMock(return_value={"friend": {"player_id": 999}}),
            status=AsyncMock(return_value={"friend": PLAYER}),
        )
        with self.assertRaisesRegex(APIError, "不一致"):
            await fetch_report(client, Query("测试玩家", view="status"))

    async def test_failed_history_never_uses_career_stats(self):
        client = SimpleNamespace(
            search=AsyncMock(return_value={"friends": [PLAYER]}),
            profile=AsyncMock(return_value={"friend": PLAYER}),
            history=AsyncMock(side_effect=APIError("业务代码 2")),
        )
        report = await fetch_report(client, Query("测试玩家", view="history", season_id=25))
        self.assertEqual(report_context(report)["modes"], [])
        self.assertIn("该赛季数据暂不可用", render_html(report))


class RenderingTests(unittest.TestCase):
    def test_result_uses_team_not_score_order_and_keeps_missing_stats_missing(self):
        row = match_row(
            {"my_team": 2, "winner_team": [2], "scores": [6, 9], "room_id": ROOM_ID, "kill_num": 0}
        )
        self.assertEqual(row["result"], "胜利")
        self.assertEqual(row["kda"], "0 / — / —")
        self.assertEqual(row["score"], "6 : 9")
        self.assertEqual(row["room_id"], ROOM_ID)
        self.assertEqual(row["damage"], "—")
        self.assertEqual(match_row({})["result"], "未知")
        self.assertEqual(match_row({"my_team": 1, "winner_team": []})["result"], "平局")

    def test_external_nickname_is_escaped_and_match_highlights_query_player(self):
        report = Report(
            Query("测试玩家", view="match", room_id=ROOM_ID),
            "123456",
            PLAYER,
            sections={
                "match": {
                    "standings": {
                        "players": [
                            {
                                **PLAYER,
                                "nick": '<script>alert("x")</script>',
                                "team_id": 1,
                                "kill_num": 0,
                                "dead_num": 3,
                                "assists_num": 2,
                            }
                        ]
                    }
                }
            },
        )
        html = render_html(report)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("查询玩家", html)
        self.assertIn("0 / 3 / 2", html)
        self.assertNotIn('src="http', html)


class PluginTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        import nonebot

        nonebot.init(_env_file=None)
        with patch.dict("os.environ", {"KANAMIBOT_STRINOVA_ENABLED": "false"}):
            cls.plugin = importlib.import_module("kanamibot.plugins.strinova")

    async def test_manual_disable_is_preserved_and_healthy_handler_returns_png(self):
        self.assertFalse(self.plugin.ENABLED)
        self.assertFalse(hasattr(self.plugin, "strinova_matcher"))

        from nonebot.adapters.onebot.v11 import Message

        class Finished(Exception):
            pass

        class Matcher:
            send = AsyncMock()

            async def finish(self, message):
                self.result = message
                raise Finished

        matcher = Matcher()
        report = Report(Query("测试玩家"), "123456", PLAYER)
        with (
            patch.object(self.plugin, "check_available", AsyncMock()),
            patch.object(self.plugin, "create_report", AsyncMock(return_value=report)),
            patch.object(self.plugin, "render_png", AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")),
        ):
            with self.assertRaises(Finished):
                await self.plugin.handle_strinova(
                    SimpleNamespace(message=Message("#卡丘 测试玩家")), matcher
                )
        self.assertEqual(matcher.result.type, "image")
        self.assertTrue(matcher.result.data["file"].startswith("base64://"))

    async def test_health_failure_pauses_every_bot_entry_including_help(self):
        from nonebot.adapters.onebot.v11 import Message

        class Finished(Exception):
            pass

        for command in (
            "#卡丘",
            "#卡丘 help",
            "#KQ 测试玩家",
            "#卡丘资料 测试玩家",
            "#卡丘状态 测试玩家",
            "#卡丘生涯 测试玩家",
            "#卡丘战绩 测试玩家",
            "#卡丘赛季 测试玩家 --season-id 25",
            f"#卡丘对局 测试玩家 --room-id {ROOM_ID}",
        ):
            matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock(side_effect=Finished))
            with (
                self.subTest(command=command),
                patch.object(
                    self.plugin, "check_available", AsyncMock(side_effect=ServiceUnavailable())
                ),
                patch.object(self.plugin, "create_report", AsyncMock()) as create,
                patch.object(self.plugin, "render_png", AsyncMock()) as render,
            ):
                with self.assertRaises(Finished):
                    await self.plugin.handle_strinova(
                        SimpleNamespace(message=Message(command)), matcher
                    )
                self.assertIn("全部查询功能已暂停", matcher.finish.call_args.args[0])
                matcher.send.assert_not_awaited()
                create.assert_not_awaited()
                render.assert_not_awaited()

    def test_cli_core_does_not_import_bot_entrypoint(self):
        from kanamibot.strinova import cli

        self.assertEqual(cli.build_parser().parse_args(["测试玩家"]).view, "overview")
        self.assertNotIn("bot", sys.modules)


if __name__ == "__main__":
    unittest.main()
