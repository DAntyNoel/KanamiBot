from __future__ import annotations

import asyncio
import importlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

from kanamibot.strinova.avatar import AvatarError, image_data_uri, load_avatar
from kanamibot.strinova.client import APIError, ServiceUnavailable, Settings, StrinovaClient
from kanamibot.strinova.query import VIEWS, Query, QueryError, parse_query, query_from_args
from kanamibot.strinova.render import match_row, render_html, report_context
from kanamibot.strinova.resources import ResourcePack
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
    def test_chinese_view_suffix_does_not_become_part_of_the_nickname(self):
        query = parse_query("测试玩家 对局战绩 --index 2")
        self.assertEqual((query.target, query.view, query.index), ("测试玩家", "standings", 2))
        self.assertEqual(parse_query("测试玩家").view, "overview")
        self.assertEqual(parse_query("测试玩家 玩家概览", view="standings").view, "overview")
        self.assertEqual(parse_query("--player-id 123456 对局战绩").view, "standings")
        self.assertEqual(parse_query("--nick 12345 引航者资料").view, "profile")
        self.assertEqual(parse_query('"Player Name 对局战绩"').target, "Player Name 对局战绩")
        self.assertEqual(parse_query('"Player Name" 对局战绩').target, "Player Name")
        self.assertEqual(parse_query("对局战绩").target, "对局战绩")
        with self.assertRaisesRegex(QueryError, "不一致"):
            parse_query("测试玩家 对局战绩 --view profile")

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

    async def test_cli_two_minute_timeout_cancels_work_without_writing_image(self):
        from kanamibot.strinova import cli

        self.assertEqual(cli.QUERY_TIMEOUT_SECONDS, 120)
        cancelled = asyncio.Event()

        async def stalled(*_):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "report.png"
            args = cli.build_parser().parse_args(["测试玩家", "-o", str(output)])
            with (
                patch.object(cli, "QUERY_TIMEOUT_SECONDS", 0.01),
                patch.object(cli, "create_report", stalled),
                patch.object(cli, "render_png", AsyncMock()) as render,
                redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(TimeoutError):
                    await cli.run(args)
            self.assertTrue(cancelled.is_set())
            render.assert_not_awaited()
            self.assertFalse(output.exists())

        errors = io.StringIO()
        with (
            patch.object(cli, "load_dotenv"),
            patch.object(cli, "run", AsyncMock(side_effect=TimeoutError("private timeout"))),
            redirect_stderr(errors),
        ):
            self.assertEqual(await asyncio.to_thread(cli.main, ["测试玩家"]), 2)
        self.assertIn("超过 2 分钟", errors.getvalue())
        self.assertNotIn("private timeout", errors.getvalue())


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
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        pack = ResourcePack(Path(folder.name))
        mocked = patch("kanamibot.strinova.render.resource_pack", return_value=pack)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_every_view_renders_without_an_installed_resource_pack(self):
        for view in VIEWS:
            with self.subTest(view=view):
                html = render_html(
                    Report(Query("测试玩家", view=view, room_id=ROOM_ID), "123456", PLAYER)
                )
                self.assertIn("测试玩家", html)
                self.assertIn("#卡丘战绩 测试玩家 对局战绩", html)
                self.assertNotIn("切换喵", html)
                self.assertNotIn('src="http', html)

    def test_picture_includes_real_avatar_and_roundtrippable_switch_commands(self):
        with io.BytesIO() as buffer:
            Image.new("RGB", (4, 4), "green").save(buffer, format="PNG")
            avatar = image_data_uri(buffer.getvalue())
        report = Report(Query("测试玩家"), "123456", PLAYER, avatar_data_uri=avatar)
        html = render_html(report)
        self.assertIn(f'src="{avatar}"', html)
        self.assertIn("#卡丘战绩 测试玩家 对局战绩", html)
        self.assertIn("#卡丘战绩 测试玩家 玩家概览", html)
        for query in (
            Query("Player Name"),
            Query("12345"),
            Query("12345", by_id=True),
            Query("Player 对局战绩"),
        ):
            context = report_context(Report(query, "123456", PLAYER))
            command_args = context["switch_command"].removeprefix("#卡丘战绩 ")
            switched = parse_query(command_args)
            self.assertEqual(switched.view, "standings")
            self.assertEqual(switched.target, "123456" if query.by_id else query.target)
            self.assertEqual(switched.by_id, query.by_id)

    def test_result_uses_team_not_score_order_and_keeps_missing_stats_missing(self):
        row = match_row(
            {"my_team": 2, "winner_team": [2], "scores": [6, 9], "room_id": ROOM_ID, "kill_num": 0}
        )
        self.assertEqual(row["result"], "胜利")
        self.assertEqual(row["kda"], "0 / — / —")
        self.assertEqual(row["score"], "9 : 6")
        self.assertEqual(row["score_label"], "我方 : 对方")
        self.assertEqual(row["room_id"], ROOM_ID)
        self.assertEqual(row["damage"], "—")
        self.assertEqual(match_row({})["result"], "未知")
        self.assertEqual(match_row({"my_team": 1, "winner_team": []})["result"], "平局")

    def test_scores_follow_queried_team_for_wins_losses_and_draws(self):
        for team, scores, winners, expected, result in (
            (1, [9, 2], [1], "9 : 2", "胜利"),
            (1, [3, 7], [2], "3 : 7", "失败"),
            (2, [3, 7], [2], "7 : 3", "胜利"),
            (2, [9, 4], [1], "4 : 9", "失败"),
            (1, [6, 6], [], "6 : 6", "平局"),
            (2, [6, 6], [], "6 : 6", "平局"),
        ):
            with self.subTest(team=team, scores=scores, winners=winners):
                original = {"my_team": team, "scores": scores, "winner_team": winners}
                row = match_row(original)
                self.assertEqual(row["score"], expected)
                self.assertEqual(row["result"], result)
                self.assertEqual(row["score_label"], "我方 : 对方")
                self.assertEqual(original["scores"], scores)

    def test_unknown_score_orientation_is_explicit_and_never_guessed(self):
        for team in (None, 0, 3, "2", True):
            with self.subTest(team=team):
                row = match_row({"my_team": team, "scores": [3, 7]})
                self.assertEqual(row["score"], "3 : 7")
                self.assertEqual(row["score_label"], "队伍原始顺序")
        for scores in (None, [], "3-7"):
            self.assertEqual(match_row({"my_team": 2, "scores": scores})["score"], "—")
        row = match_row({"my_team": 2, "scores": [3, 7, 4]})
        self.assertEqual(row["score"], "3 : 7 : 4")
        self.assertEqual(row["score_label"], "队伍原始顺序")

    def test_score_order_is_shared_by_overview_standings_and_match_summary(self):
        title = {"my_team": 2, "winner_team": [2], "scores": [3, 7], "room_id": ROOM_ID}
        for view in ("overview", "standings", "match"):
            with self.subTest(view=view):
                report = Report(
                    Query("测试玩家", view=view, room_id=ROOM_ID),
                    "123456",
                    PLAYER,
                    sections={"standings": {"titles": [title]}},
                )
                context = report_context(report)
                self.assertEqual(context["matches"][0]["score"], "7 : 3")
                self.assertEqual(context["match_summary"]["score"], "7 : 3")
                html = render_html(report)
                self.assertIn("7 : 3", html)
                self.assertIn("我方 : 对方", html)

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

    def setUp(self):
        self.bot = SimpleNamespace(delete_msg=AsyncMock())
        gate = patch.object(self.plugin, "query_gate", asyncio.Semaphore(2))
        gate.start()
        self.addCleanup(gate.stop)

    async def test_manual_disable_is_preserved_and_healthy_handler_returns_png(self):
        self.assertFalse(self.plugin.ENABLED)
        self.assertFalse(hasattr(self.plugin, "strinova_matcher"))

        from nonebot.adapters.onebot.v11 import Message

        class Finished(Exception):
            pass

        class Matcher:
            send = AsyncMock(return_value={"message_id": 456})

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
                    SimpleNamespace(message=Message("#卡丘 测试玩家")), matcher, self.bot
                )
        self.assertEqual(matcher.result.type, "image")
        self.assertTrue(matcher.result.data["file"].startswith("base64://"))
        self.bot.delete_msg.assert_awaited_once_with(message_id=456)

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
                        SimpleNamespace(message=Message(command)), matcher, self.bot
                    )
                self.assertIn("全部查询功能已暂停", matcher.finish.call_args.args[0])
                matcher.send.assert_not_awaited()
                create.assert_not_awaited()
                render.assert_not_awaited()

    async def test_main_command_defaults_to_overview_and_accepts_chinese_switch(self):
        from nonebot.adapters.onebot.v11 import Message

        class Finished(Exception):
            pass

        for command, view in (
            ("#卡丘战绩 测试玩家", "overview"),
            ("#卡丘战绩 测试玩家 对局战绩", "standings"),
            ("#卡丘战绩 测试玩家 生涯数据", "career"),
            ("#卡丘战绩 测试玩家 玩家概览", "overview"),
            ("#卡丘资料 测试玩家", "profile"),
        ):
            matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock(side_effect=Finished))
            report = Report(Query("测试玩家", view=view), "123456", PLAYER)
            with (
                self.subTest(command=command),
                patch.object(self.plugin, "check_available", AsyncMock()),
                patch.object(
                    self.plugin, "create_report", AsyncMock(return_value=report)
                ) as create,
                patch.object(self.plugin, "render_png", AsyncMock(return_value=b"png")),
            ):
                with self.assertRaises(Finished):
                    await self.plugin.handle_strinova(
                        SimpleNamespace(message=Message(command)), matcher, self.bot
                    )
                self.assertEqual(create.call_args.args[0].target, "测试玩家")
                self.assertEqual(create.call_args.args[0].view, view)

    async def test_query_and_render_failures_recall_prompt_and_release_query_slot(self):
        from nonebot.adapters.onebot.v11 import Message

        class Finished(Exception):
            pass

        report = Report(Query("测试玩家"), "123456", PLAYER)
        for stage, failure, expected in (
            ("query", APIError("API 请求超时"), "API 请求超时"),
            ("query", ServiceUnavailable(), "全部查询功能已暂停"),
            ("render", QueryError("图片渲染失败"), "图片渲染失败"),
            ("render", RuntimeError("private error"), "图片生成失败"),
        ):
            matcher = SimpleNamespace(
                send=AsyncMock(return_value={"message_id": 789}),
                finish=AsyncMock(side_effect=Finished),
            )
            self.bot.delete_msg.reset_mock()
            create = AsyncMock(return_value=report)
            render = AsyncMock(return_value=b"png")
            (create if stage == "query" else render).side_effect = failure
            with (
                self.subTest(stage=stage, failure=type(failure).__name__),
                patch.object(self.plugin, "check_available", AsyncMock()),
                patch.object(self.plugin, "create_report", create),
                patch.object(self.plugin, "render_png", render),
                patch.object(self.plugin.logger, "exception"),
            ):
                with self.assertRaises(Finished):
                    await self.plugin.handle_strinova(
                        SimpleNamespace(message=Message("#卡丘战绩 测试玩家")), matcher, self.bot
                    )
                self.assertIn(expected, matcher.finish.call_args.args[0])
                self.assertNotIn("private error", matcher.finish.call_args.args[0])
                self.bot.delete_msg.assert_awaited_once_with(message_id=789)
                self.assertEqual(self.plugin.query_gate._value, 2)

    async def test_timeout_cancels_query_or_render_and_recalls_prompt(self):
        from nonebot.adapters.onebot.v11 import Message

        class Finished(Exception):
            pass

        self.assertEqual(self.plugin.QUERY_TIMEOUT_SECONDS, 120)
        report = Report(Query("测试玩家"), "123456", PLAYER)
        for stage in ("query", "render"):
            cancelled = asyncio.Event()

            async def stalled(*_, cancelled=cancelled):
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()

            matcher = SimpleNamespace(
                send=AsyncMock(return_value={"message_id": 123}),
                finish=AsyncMock(side_effect=Finished),
            )
            self.bot.delete_msg.reset_mock()
            create = AsyncMock(return_value=report)
            render = AsyncMock(return_value=b"png")
            (create if stage == "query" else render).side_effect = stalled
            with (
                self.subTest(stage=stage),
                patch.object(self.plugin, "QUERY_TIMEOUT_SECONDS", 0.01),
                patch.object(self.plugin, "check_available", AsyncMock()),
                patch.object(self.plugin, "create_report", create),
                patch.object(self.plugin, "render_png", render),
            ):
                with self.assertRaises(Finished):
                    await self.plugin.handle_strinova(
                        SimpleNamespace(message=Message("#卡丘战绩 测试玩家")), matcher, self.bot
                    )
                self.assertTrue(cancelled.is_set())
                self.assertIn("超过 2 分钟", matcher.finish.call_args.args[0])
                self.bot.delete_msg.assert_awaited_once_with(message_id=123)
                self.assertEqual(self.plugin.query_gate._value, 2)

    async def test_cancelled_handler_still_recalls_prompt_and_releases_query_slot(self):
        from nonebot.adapters.onebot.v11 import Message

        started = asyncio.Event()

        async def stalled(*_):
            started.set()
            await asyncio.Future()

        matcher = SimpleNamespace(
            send=AsyncMock(return_value={"message_id": 123}), finish=AsyncMock()
        )
        with (
            patch.object(self.plugin, "check_available", AsyncMock()),
            patch.object(self.plugin, "create_report", stalled),
        ):
            task = asyncio.create_task(
                self.plugin.handle_strinova(
                    SimpleNamespace(message=Message("#卡丘战绩 测试玩家")), matcher, self.bot
                )
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.bot.delete_msg.assert_awaited_once_with(message_id=123)
        matcher.finish.assert_not_awaited()
        self.assertEqual(self.plugin.query_gate._value, 2)

    async def test_recall_failure_does_not_replace_success_or_error_response(self):
        from nonebot.adapters.onebot.v11 import Message, MessageSegment

        class Finished(Exception):
            pass

        for failure in (None, APIError("查询失败")):
            matcher = SimpleNamespace(
                send=AsyncMock(return_value={"message_id": 123}),
                finish=AsyncMock(side_effect=Finished),
            )
            self.bot.delete_msg = AsyncMock(side_effect=RuntimeError("QQ recall failed"))
            with (
                self.subTest(failure=failure),
                patch.object(self.plugin, "check_available", AsyncMock()),
                patch.object(
                    self.plugin,
                    "create_report",
                    AsyncMock(
                        return_value=Report(Query("测试玩家"), "123456", PLAYER),
                        side_effect=failure,
                    ),
                ),
                patch.object(self.plugin, "render_png", AsyncMock(return_value=b"png")),
            ):
                with self.assertRaises(Finished):
                    await self.plugin.handle_strinova(
                        SimpleNamespace(message=Message("#卡丘战绩 测试玩家")), matcher, self.bot
                    )
                result = matcher.finish.call_args.args[0]
                self.assertEqual(result, "查询失败" if failure else MessageSegment.image(b"png"))
                self.assertEqual(self.plugin.query_gate._value, 2)

    def test_cli_core_does_not_import_bot_entrypoint(self):
        from kanamibot.strinova import cli

        self.assertEqual(
            query_from_args(cli.build_parser().parse_args(["测试玩家"])).view, "overview"
        )
        self.assertNotIn("bot", sys.modules)


class AvatarTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.pack = ResourcePack(Path(folder.name))
        mocked = patch("kanamibot.strinova.avatar.resource_pack", return_value=self.pack)
        mocked.start()
        self.addCleanup(mocked.stop)

    async def test_local_avatar_is_used_without_network_or_external_cache(self):
        local = self.pack.root / "images/PaperMan/UI/Atlas/DynamicResource/IdCard/Appearance"
        local.mkdir(parents=True)
        avatar = local / "T_Dynamic_IdCard_31001183_icon.png"
        Image.new("RGB", (4, 4), "pink").save(avatar)
        requests = []

        def unavailable(request):
            requests.append(request)
            return httpx.Response(503)

        with tempfile.TemporaryDirectory() as folder:
            actual = await load_avatar(
                31001183, cache_dir=Path(folder), transport=httpx.MockTransport(unavailable)
            )
            self.assertEqual(actual, image_data_uri(avatar.read_bytes()))
            self.assertEqual(requests, [])
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_real_image_is_embedded_and_cached_by_icon(self):
        with io.BytesIO() as buffer:
            Image.new("RGB", (4, 4), "green").save(buffer, format="PNG")
            png = buffer.getvalue()
        urls = []

        def handler(request):
            urls.append(str(request.url))
            return httpx.Response(200, content=png)

        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            transport = httpx.MockTransport(handler)
            first = await load_avatar(31000001, cache_dir=cache, transport=transport)
            second = await load_avatar(31000001, cache_dir=cache, transport=transport)
            self.assertEqual(first, second)
            self.assertTrue(first.startswith("data:image/png;base64,"))
            self.assertEqual(len(urls), 1)
            self.assertIn("T_Dynamic_IdCard_31000001_icon.png", urls[0])
            cached = next(cache.glob("*.image"))
            cached.write_bytes(b"corrupted cache")
            self.assertEqual(
                await load_avatar(31000001, cache_dir=cache, transport=transport), first
            )
            self.assertEqual(len(urls), 2)

    async def test_error_pages_and_invalid_ids_are_never_embedded(self):
        with tempfile.TemporaryDirectory() as folder:
            for icon in (0, None, "../private", 31000001):
                with self.subTest(icon=icon), self.assertRaises(AvatarError):
                    await load_avatar(
                        icon,
                        cache_dir=Path(folder),
                        transport=httpx.MockTransport(
                            lambda _: httpx.Response(200, text="<html>verification required</html>")
                        ),
                    )
            self.assertEqual(list(Path(folder).glob("*.image")), [])

    async def test_oversized_avatar_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(AvatarError):
            await load_avatar(
                31000001,
                cache_dir=Path(folder),
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
                ),
            )


class ResourceTests(unittest.TestCase):
    def test_paths_stay_inside_pack_and_only_verified_metadata_is_used(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "pack"
            (root / "mappings").mkdir(parents=True)
            image = root / "images/PaperMan/avatar.png"
            image.parent.mkdir(parents=True)
            Image.new("RGB", (4, 4), "pink").save(image)
            (Path(folder) / "outside.png").write_bytes(image.read_bytes())
            (root / "mappings/avatars.json").write_text(
                json.dumps(
                    [
                        {
                            "id": 1,
                            "verified": True,
                            "extracted_paths": {
                                "IconItem": {"png": "strinova_resources/images/PaperMan/avatar.png"}
                            },
                        },
                        {"id": 2, "verified": False},
                        {"id": 3},
                    ]
                ),
                encoding="utf-8",
            )
            pack = ResourcePack(root)
            self.assertEqual(pack.avatar_path(1), image.resolve())
            self.assertEqual(pack.resolve("/Game/PaperMan/avatar.avatar"), image.resolve())
            self.assertEqual(list(pack.records("avatars")), [1])
            self.assertIsNone(pack.resolve("../outside.png"))
            self.assertIsNone(pack.resolve(str(Path(folder) / "outside.png")))
            self.assertIsNone(pack.data_uri(Path(folder) / "outside.png"))
            self.assertEqual(pack.mapping("../outside"), {})

    def test_missing_or_malformed_metadata_preserves_unknown_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "mappings").mkdir()
            (root / "mappings/game_modes.json").write_text("[]", encoding="utf-8")
            (root / "mappings/enums.json").write_text("{bad json", encoding="utf-8")
            pack = ResourcePack(root)
            self.assertIsNone(pack.mode_name(5))
            self.assertEqual(pack.map_name(207), "地图 #207")
            self.assertEqual(pack.status_name(None), "状态未提供")
            self.assertEqual(pack.status_name(1), "状态码 1")
            self.assertIsNone(pack.role_image(101))
            self.assertIsNone(pack.image_path({"extracted_paths": []}, "IconItem"))


if __name__ == "__main__":
    unittest.main()
