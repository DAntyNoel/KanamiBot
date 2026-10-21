from __future__ import annotations

import asyncio
import base64
import importlib
import io
import json
import re
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
from kanamibot.strinova.render import match_row, rank_name, render_html, report_context, role_name
from kanamibot.strinova.resources import ResourcePack
from kanamibot.strinova.service import Report, fetch_report, fetch_standings_page, resolve_player

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
        self.assertEqual(parse_query("12345 2").index, 2)
        self.assertEqual(parse_query('"Player 2" 对局战绩').target, "Player 2")
        self.assertEqual(parse_query('"Player Name" --index 2').target, "Player Name")
        self.assertEqual(parse_query("Player Name --index 2").target, "Player Name")

    def test_recent_match_modes_accept_names_aliases_and_unambiguous_prefixes(self):
        for text, expected_view, expected_mode in (
            ("测试玩家 对局战绩 排位", "standings", 5),
            ("测试玩家 对局战绩 一般爆破", "standings", 2),
            ("测试玩家 生化", "overview", 13),
            ("测试玩家 --mode 枪王", "overview", 30),
            ("测试玩家 对局战绩 晶源感", "standings", 13),
            ("测试玩家 --game-mode 31", "overview", 31),
        ):
            with self.subTest(text=text):
                query = parse_query(text)
                self.assertEqual(
                    (query.target, query.view, query.game_mode),
                    ("测试玩家", expected_view, expected_mode),
                )
        for text, message in (
            ("测试玩家 --mode 晶源", "可指"),
            ("测试玩家 --mode 不存在", "未知战绩模式"),
            ("测试玩家 --mode 枪王 --game-mode 5", "不一致"),
            ("测试玩家 玩家名片 --mode 排位", "仅适用于"),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(QueryError, message):
                parse_query(text)

    def test_recent_match_page_accepts_chinese_suffix_and_option_alias(self):
        plain = parse_query("测试玩家 对局战绩 第2页")
        self.assertEqual((plain.view, plain.index, plain.game_mode), ("standings", 2, 0))
        for text, expected_view, expected_mode, expected_page in (
            ("测试玩家 2", "overview", 0, 2),
            ("测试玩家 排位 2", "overview", 5, 2),
            ("测试玩家 排位", "overview", 5, 1),
            ("测试玩家 对局战绩 2", "standings", 0, 2),
            ("测试玩家 对局战绩 排位 2", "standings", 5, 2),
            ("测试玩家 2 排位", "overview", 5, 2),
            ("--nick 12345 2", "overview", 0, 2),
        ):
            with self.subTest(text=text):
                query = parse_query(text)
                self.assertEqual(
                    (query.target, query.view, query.game_mode, query.index),
                    (
                        "12345" if text.startswith("--nick") else "测试玩家",
                        expected_view,
                        expected_mode,
                        expected_page,
                    ),
                )
        for text in (
            "测试玩家 对局战绩 枪王 第2页",
            "测试玩家 对局战绩 第2页 枪王",
            "测试玩家 枪王 --page 2",
            "测试玩家 枪王 --index 2",
        ):
            with self.subTest(text=text):
                query = parse_query(text)
                self.assertEqual((query.target, query.index, query.game_mode), ("测试玩家", 2, 30))
        with self.assertRaisesRegex(QueryError, "页码不一致"):
            parse_query("测试玩家 对局战绩 第2页 --page 3")
        with self.assertRaisesRegex(QueryError, "页码不一致"):
            parse_query("测试玩家 排位 2 --page 3")

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

        for handler, expected in (
            (timeout, "API 请求超时，请稍后重试。"),
            (lambda _: httpx.Response(503), "API 返回 HTTP 503。"),
            (
                lambda _: httpx.Response(
                    504,
                    json={"ok": False, "error": "query has no waiters or its deadline expired"},
                ),
                "查询超时，后端服务无响应",
            ),
            (
                lambda _: httpx.Response(200, text="<html>unavailable</html>"),
                "API 返回了非 JSON 数据。",
            ),
        ):
            async with StrinovaClient(Settings(), transport=healthy_transport(handler)) as client:
                with self.subTest(expected=expected), self.assertRaises(APIError) as caught:
                    await client.profile("123456")
                self.assertEqual(str(caught.exception), expected)

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
    async def asyncSetUp(self):
        sleep_patch = patch("kanamibot.strinova.client.asyncio.sleep", new_callable=AsyncMock)
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    async def test_unavailable_health_blocks_every_view_before_player_requests(self):
        for view in VIEWS:
            paths = []

            def handler(request, paths=paths):
                paths.append(request.url.path)
                return httpx.Response(503)

            async with StrinovaClient(Settings(), transport=httpx.MockTransport(handler)) as client:
                with self.subTest(view=view), self.assertRaises(ServiceUnavailable):
                    await fetch_report(client, Query("测试玩家", view=view, room_id=ROOM_ID))
            self.assertEqual(paths, ["/health"] * 5)

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
        self.assertEqual(paths, ["/health"] * 6 + ["/v1/friends/search"])

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
            paths,
            ["/health", "/v1/friends/search", "/health", "/v1/players/123456"] + ["/health"] * 5,
        )

    async def test_five_failures_use_exponential_delays_within_two_minutes(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(503)

        async with StrinovaClient(Settings(), transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ServiceUnavailable):
                await client.check_health()
            delays = [call.args[0] for call in self.sleep.await_args_list]
            self.assertEqual(len(requests), 5)
            self.assertEqual(delays, [5, 10, 20, 40])
            self.assertLessEqual(sum(delays) + len(requests) * client.health_timeout, 120)

    async def test_any_success_including_fifth_check_resumes_player_request(self):
        for recover_at in range(1, 6):
            self.sleep.reset_mock()
            paths = []

            def handler(request, paths=paths, recover_at=recover_at):
                paths.append(request.url.path)
                if request.url.path == "/health":
                    return httpx.Response(200, json={"ok": len(paths) == recover_at})
                return response({"friends": [PLAYER]})

            with self.subTest(recover_at=recover_at):
                async with StrinovaClient(
                    Settings(), transport=httpx.MockTransport(handler)
                ) as client:
                    self.assertEqual(await resolve_player(client, Query("测试玩家")), PLAYER)
                self.assertEqual(paths, ["/health"] * recover_at + ["/v1/friends/search"])
                self.assertEqual(
                    [call.args[0] for call in self.sleep.await_args_list],
                    [5, 10, 20, 40][: recover_at - 1],
                )

    async def test_whole_health_request_timeout_retries_five_times(self):
        cancelled = 0

        async def stalled(request):
            nonlocal cancelled
            try:
                await asyncio.Future()
            finally:
                cancelled += 1

        async with StrinovaClient(Settings(), transport=httpx.MockTransport(stalled)) as client:
            client.health_timeout = 0.01
            with self.assertRaises(ServiceUnavailable):
                await client.check_health()
        self.assertEqual(cancelled, 5)
        self.assertEqual(self.sleep.await_count, 4)

    async def test_cancelling_health_retry_propagates_without_unavailable_verdict(self):
        started = asyncio.Event()
        paths = []

        async def stalled_delay(_):
            started.set()
            await asyncio.Future()

        def handler(request):
            paths.append(request.url.path)
            return httpx.Response(503)

        self.sleep.side_effect = stalled_delay
        async with StrinovaClient(Settings(), transport=httpx.MockTransport(handler)) as client:
            task = asyncio.create_task(client.check_health())
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(paths, ["/health"])
        self.sleep.assert_awaited_once_with(5)

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
    async def test_recent_pages_cover_all_records_across_api_batches(self):
        for total, page, first, last, has_more, api_pages in (
            (45, 1, 1, 8, True, [1]),
            (45, 2, 9, 16, True, [1]),
            (45, 3, 17, 24, True, [1, 2]),
            (45, 6, 41, 45, False, [3]),
            (40, 5, 33, 40, False, [2, 3]),
        ):
            with self.subTest(page=page):
                all_rows = [{"room_id": str(index)} for index in range(1, total + 1)]
                client = SimpleNamespace(
                    search=AsyncMock(return_value={"friends": [PLAYER]}),
                    profile=AsyncMock(return_value={"friend": PLAYER}),
                    standings=AsyncMock(
                        side_effect=lambda _, api_page, *filters, rows=all_rows: {
                            "titles": rows[(api_page - 1) * 20 : api_page * 20]
                        }
                    ),
                )
                query = parse_query(f"测试玩家 对局战绩 枪王 {page}")
                report = await fetch_report(client, query)
                self.assertEqual(
                    [int(row["room_id"]) for row in report.sections["standings"]["titles"]],
                    list(range(first, last + 1)),
                )
                self.assertEqual(report.sections["standings"]["has_more"], has_more)
                self.assertEqual(
                    [call.args[1] for call in client.standings.await_args_list], api_pages
                )
                self.assertTrue(
                    all(call.args[2] == 30 for call in client.standings.await_args_list)
                )
                context = report_context(report)
                if page > 1:
                    previous_query = parse_query(
                        context["previous_page_command"].removeprefix("#卡丘战绩 ")
                    )
                    self.assertEqual(previous_query.index, page - 1)
                else:
                    self.assertIsNone(context["previous_page_command"])
                if has_more:
                    next_command = context["next_page_command"]
                    self.assertIn(" " + str(page + 1) + "</code>", render_html(report))
                    next_query = parse_query(next_command.removeprefix("#卡丘战绩 "))
                    self.assertEqual((next_query.index, next_query.game_mode), (page + 1, 30))
                else:
                    self.assertIsNone(context["next_page_command"])
                    self.assertNotIn("还有未显示的战绩", render_html(report))

    async def test_named_recent_mode_reaches_standings_api(self):
        client = SimpleNamespace(
            search=AsyncMock(return_value={"friends": [PLAYER]}),
            profile=AsyncMock(return_value={"friend": PLAYER}),
            standings=AsyncMock(return_value={"titles": []}),
        )
        report = await fetch_report(client, parse_query("测试玩家 对局战绩 枪王"))
        client.standings.assert_awaited_once_with("123456", 1, 30, 0, 0)
        self.assertEqual(report_context(report)["filter"], "枪王乱斗")

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
        self.assertIn("对局数", html)
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
            seasons=AsyncMock(
                return_value={
                    "current_main_season_id": 26,
                    "seasons": [{"season_id": 25, "season_type": 1, "available": True}],
                }
            ),
        )
        report = await fetch_report(client, Query("测试玩家", view="history", season_id=25))
        self.assertEqual(report_context(report)["modes"], [])
        self.assertIn("该赛季数据暂不可用", render_html(report))


class StandingsRoleRecoveryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def detail(room_id="3", **own):
        return {
            "room_id": room_id,
            "standings": {
                "players": [
                    {"player_id": 999, "final_role_id": 101},
                    {
                        **PLAYER,
                        "final_role_id": 146,
                        "kill_num": 0,
                        "dead_num": 2,
                        "assists_num": 3,
                        "damage": 0,
                        **own,
                    },
                ]
            },
        }

    async def test_only_visible_zero_roles_use_same_player_detail_and_preserve_real_zero_stats(
        self,
    ):
        rows = [
            {
                "room_id": str(i),
                "role_id": 137 if i == 4 else 0,
                "bridge_stats_status": "unavailable",
            }
            for i in range(1, 7)
        ]
        client = SimpleNamespace(
            standings=AsyncMock(return_value={"titles": rows}),
            match=AsyncMock(return_value=self.detail()),
        )
        page = await fetch_standings_page(
            client, "123456", Query("测试玩家", view="standings", index=2, limit=2)
        )
        self.assertEqual([row["room_id"] for row in page["titles"]], ["3", "4"])
        self.assertTrue(page["has_more"])
        client.match.assert_awaited_once_with("123456", "3")
        self.assertEqual(rows[2]["role_id"], 0)
        row = page["titles"][0]
        self.assertEqual((row["role_id"], row["bridge_stats_status"]), (146, "detail"))
        rendered = match_row(row)
        self.assertEqual(
            (rendered["role"], rendered["kda"], rendered["damage"]), ("星绘", "0 / 2 / 3", "0")
        )

    async def test_failed_detail_keeps_list_and_partial_detail_does_not_invent_stats(self):
        rows = [
            {"room_id": str(i), "role_id": 0, "bridge_stats_status": "unavailable"} for i in (1, 2)
        ]
        client = SimpleNamespace(
            standings=AsyncMock(return_value={"titles": rows}),
            match=AsyncMock(
                side_effect=[APIError("查询超时，后端服务无响应"), self.detail("2", damage=None)]
            ),
        )
        page = await fetch_standings_page(client, "123456", Query("测试玩家"))
        self.assertEqual(page["titles"][0], rows[0])
        row = page["titles"][1]
        self.assertEqual(row["role_id"], 146)
        self.assertEqual(match_row(row)["kda"], "— / — / —")
        self.assertNotIn("damage", row)

    async def test_mismatched_or_invalid_detail_is_not_used(self):
        invalid = [
            self.detail("4"),
            self.detail(player_id=999),
            {"room_id": "3", "standings": {"players": []}},
            {"room_id": "3", "standings": {"players": [PLAYER, PLAYER]}},
            {"room_id": "3", "standings": None},
            *[self.detail(final_role_id=role) for role in (0, -1, True, "146", None)],
        ]
        for detail in invalid:
            with self.subTest(detail=detail):
                row = {"room_id": "3", "role_id": 0, "bridge_stats_status": "unavailable"}
                client = SimpleNamespace(
                    standings=AsyncMock(return_value={"titles": [row]}),
                    match=AsyncMock(return_value=detail),
                )
                page = await fetch_standings_page(client, "123456", Query("测试玩家"))
                self.assertEqual(page["titles"], [row])

    async def test_service_unavailable_still_aborts_query(self):
        client = SimpleNamespace(
            standings=AsyncMock(return_value={"titles": [{"room_id": "3", "role_id": 0}]}),
            match=AsyncMock(side_effect=ServiceUnavailable()),
        )
        with self.assertRaises(ServiceUnavailable):
            await fetch_standings_page(client, "123456", Query("测试玩家"))

    async def test_per_detail_timeout_allows_next_visible_row(self):
        async def detail(_, room_id):
            if room_id == "2":
                await asyncio.Event().wait()
            return self.detail(room_id)

        client = SimpleNamespace(
            standings=AsyncMock(
                return_value={"titles": [{"room_id": str(i), "role_id": 0} for i in (2, 3)]}
            ),
            match=AsyncMock(side_effect=detail),
        )
        with patch("kanamibot.strinova.service.STANDINGS_DETAIL_TIMEOUT_SECONDS", 0.01):
            page = await asyncio.wait_for(
                fetch_standings_page(client, "123456", Query("测试玩家")), 1
            )
        self.assertEqual([row["role_id"] for row in page["titles"]], [0, 146])

    async def test_page_budget_keeps_recovered_rows_and_stops_remaining_requests(self):
        async def detail(_, room_id):
            if room_id != "3":
                await asyncio.Event().wait()
            return self.detail(room_id)

        client = SimpleNamespace(
            standings=AsyncMock(
                return_value={"titles": [{"room_id": str(i), "role_id": 0} for i in (3, 4, 5)]}
            ),
            match=AsyncMock(side_effect=detail),
        )
        with patch("kanamibot.strinova.service.STANDINGS_DETAIL_BUDGET_SECONDS", 0.02):
            page = await asyncio.wait_for(
                fetch_standings_page(client, "123456", Query("测试玩家")), 1
            )
        self.assertEqual([row["role_id"] for row in page["titles"]], [146, 0, 0])
        self.assertEqual([call.args[1] for call in client.match.await_args_list], ["3", "4"])

    async def test_caller_cancellation_propagates(self):
        started = asyncio.Event()

        async def detail(*_):
            started.set()
            await asyncio.Event().wait()

        client = SimpleNamespace(
            standings=AsyncMock(return_value={"titles": [{"room_id": "3", "role_id": 0}]}),
            match=AsyncMock(side_effect=detail),
        )
        task = asyncio.create_task(fetch_standings_page(client, "123456", Query("测试玩家")))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


class RenderingTests(unittest.TestCase):
    def test_missing_character_id_is_not_presented_as_character_zero(self):
        for invalid in (0, -1, True, None, "146"):
            self.assertEqual(role_name(invalid), "角色未提供")
        self.assertEqual(role_name(146), "星绘")
        self.assertEqual(role_name(999), "角色 #999")
        for view in ("overview", "standings", "career", "match"):
            with self.subTest(view=view):
                report = Report(
                    Query("测试玩家", view=view, room_id=ROOM_ID),
                    "123456",
                    {**PLAYER, "freq_roles": [0]},
                    sections={
                        "season": {"season_id": 18, "is_current": True},
                        "standings": {"titles": [{"role_id": 0}]},
                        "career": {"career_info": {"role_employs": [{"role_id": 0, "count": 1}]}},
                        "match": {"standings": {"players": [{**PLAYER, "final_role_id": 0}]}},
                    },
                )
                html = render_html(report)
                self.assertNotIn("角色 #0", html)
                self.assertIn("角色未提供", html)

    def test_rank_cards_keep_ranked_and_challenge_names_and_scores_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            pack = ResourcePack(Path(folder))
            with patch("kanamibot.strinova.render.resource_pack", return_value=pack):
                for view in ("overview", "profile"):
                    report = Report(
                        Query("测试玩家", view=view),
                        "123456",
                        {**PLAYER, "grade_score": 2139, "leisure_rank": 22, "leisure_score": 5000},
                    )
                    cards = report_context(report)["rank_cards"]
                    self.assertEqual(
                        [(card["label"], card["name"], card["score"]) for card in cards],
                        [("排位段位", "束缚电子II", "2,139"), ("挑战赛段位", "翡翠IV", "5,000")],
                    )
                    html = render_html(report)
                    self.assertIn("挑战赛段位", html)
                    self.assertIn("翡翠IV", html)
                    self.assertNotIn('src="None"', html)

    def test_challenge_rank_uses_only_current_career_fallback_and_preserves_zero(self):
        report = Report(
            Query("测试玩家"),
            "123456",
            PLAYER.copy(),
            sections={
                "career": {
                    "career_info": {
                        "cur_leisure_rank": 11,
                        "cur_leisure_score": 0,
                        "top_history_leisure_rank": 41,
                        "top_history_leisure_score": 99999,
                    }
                }
            },
        )
        self.assertEqual(report_context(report)["rank_cards"][1]["name"], "黄金V")
        self.assertEqual(report_context(report)["rank_cards"][1]["score"], "0")
        report.profile.update(leisure_rank=1, leisure_score=0)
        self.assertEqual(report_context(report)["rank_cards"][1]["name"], "青铜V")
        report.profile.update(leisure_rank=None, leisure_score=None)
        self.assertEqual(report_context(report)["rank_cards"][1]["name"], "—")
        self.assertEqual(report_context(report)["rank_cards"][1]["score"], "—")
        report.sections = {}
        report.profile = PLAYER.copy()
        self.assertEqual(report_context(report)["rank_cards"][1]["name"], "—")
        self.assertEqual(report_context(report)["rank_cards"][1]["score"], "—")
        for invalid in (True, [], {}, "22"):
            self.assertEqual(rank_name(invalid, leisure=True), "—")
        self.assertEqual(rank_name(999, leisure=True), "段位 #999")

    def test_overview_metrics_aggregate_career_counters_without_mixing_denominators(self):
        rows = [
            {
                "game_mode": 2,
                "round": 10,
                "total_kill": 20,
                "total_damage": 3000,
                "round_ave_damage": 100,
                "total_dead_count": 10,
                "kill_head_num": 5,
                "head_hit_rate": 0.95,
            },
            {
                "game_mode": 5,
                "round": 20,
                "total_kill": 60,
                "total_damage": 12000,
                "round_ave_damage": 237.04,
                "total_dead_count": 20,
                "k_d": 1.73,
                "kill_head_num": 12,
                "head_hit_rate": 0.95,
            },
            {
                "game_mode": 17,
                "round": 5,
                "total_kill": 5,
                "total_damage": 1000,
                "total_dead_count": 5,
                "kill_head_num": 1,
                "head_hit_rate": 0.95,
            },
            {
                "game_mode": 30,
                "round": 5,
                "total_kill": 100,
                "total_damage": 5000,
                "total_dead_count": 50,
                "kill_head_num": 15,
                "head_hit_rate": 0.95,
            },
        ]
        report = Report(
            Query("测试玩家"),
            "123456",
            {**PLAYER, "total_games": 40, "win_games": 25, "mvp_count": 7, "avr_kill": 0},
            sections={"career": {"career_info": {"battle_infos": rows}}},
        )
        self.assertEqual(
            report_context(report)["metrics"],
            [
                ("对局数", "40"),
                ("胜率", "62.5%"),
                ("胜场", "25"),
                ("MVP数", "7"),
                ("场均击杀", "4.6"),
                ("回合平均伤害", "237"),
                ("排位爆破KD", "1.73"),
                ("头部击败率", "17.8%"),
            ],
        )

    def test_missing_career_counters_stay_unavailable_in_overview(self):
        report = Report(Query("测试玩家"), "123456", PLAYER)
        self.assertEqual(
            report_context(report)["metrics"][4:],
            [("场均击杀", "—"), ("回合平均伤害", "—"), ("排位爆破KD", "—"), ("头部击败率", "—")],
        )
        report.sections["career"] = {
            "career_info": {
                "battle_infos": [
                    {
                        "game_mode": 30,
                        "round": 3,
                        "total_kill": 9,
                        "total_damage": 3000,
                        "kill_head_num": 2,
                    }
                ]
            }
        }
        metrics = dict(report_context(report)["metrics"])
        self.assertEqual((metrics["场均击杀"], metrics["回合平均伤害"]), ("3.0", "—"))
        self.assertEqual(metrics["排位爆破KD"], "—")
        profile_report = Report(Query("测试玩家", view="profile"), "123456", PLAYER)
        self.assertEqual(len(report_context(profile_report)["metrics"]), 4)

    def test_overview_round_damage_uses_server_average_and_preserves_missing_or_zero(self):
        for value, expected in (
            (0, "0"),
            (237.04, "237"),
            (None, "—"),
            (True, "—"),
            (-1, "—"),
            (float("nan"), "—"),
            (float("inf"), "—"),
            ("237", "—"),
        ):
            report = Report(
                Query("测试玩家"),
                "123456",
                PLAYER,
                sections={
                    "career": {
                        "career_info": {
                            "battle_infos": [
                                {"game_mode": 2, "round": 100, "round_ave_damage": 999},
                                {
                                    "game_mode": 5,
                                    "round": 10,
                                    "total_damage": 9000,
                                    "round_ave_damage": value,
                                },
                            ]
                        }
                    }
                },
            )
            with self.subTest(value=value):
                self.assertEqual(dict(report_context(report)["metrics"])["回合平均伤害"], expected)
                self.assertNotIn("场均伤害", dict(report_context(report)["metrics"]))

    def test_overview_round_damage_falls_back_only_without_ranked_participation(self):
        regular = {"game_mode": 2, "round": 2, "round_ave_damage": 200}
        other = {"game_mode": 9, "round": 50, "round_ave_damage": 1500}
        for rows, expected in (
            ([regular, other], "200"),
            ([{"game_mode": 5, "round": 0, "round_ave_damage": 0}, regular, other], "200"),
            ([{**regular, "round": 0}, other], "1,500"),
            ([{**regular, "round_ave_damage": None}, other], "1,500"),
            ([other, {"game_mode": 30, "round": 100, "round_ave_damage": 600}], "600"),
            ([{"game_mode": 5, "round": 1}, regular], "—"),
            ([{"game_mode": 5, "round_ave_damage": 300}, regular], "—"),
            ([{"game_mode": 5, "round": 0}, {**regular, "round_ave_damage": 0}], "0"),
            ([{**regular, "round": 0}], "—"),
        ):
            report = Report(
                Query("测试玩家"),
                "123456",
                PLAYER,
                sections={"career": {"career_info": {"battle_infos": rows}}},
            )
            with self.subTest(rows=rows):
                self.assertEqual(dict(report_context(report)["metrics"])["回合平均伤害"], expected)

    def test_page_hint_preserves_filters_and_display_limit(self):
        report = Report(
            Query(
                "测试玩家",
                view="standings",
                index=2,
                limit=10,
                game_mode=5,
                room_mode_type=2,
                play_mode=3,
            ),
            "123456",
            PLAYER,
            sections={
                "standings": {"titles": [{"room_id": str(i)} for i in range(10)], "has_more": True}
            },
        )
        context = report_context(report)
        next_command = context["next_page_command"]
        next_query = parse_query(next_command.removeprefix("#卡丘战绩 "))
        self.assertEqual(
            (
                next_query.index,
                next_query.limit,
                next_query.game_mode,
                next_query.room_mode_type,
                next_query.play_mode,
            ),
            (3, 10, 5, 2, 3),
        )
        self.assertIn(next_command, render_html(report))
        self.assertIn("排位爆破 3 --room-mode-type 2 --play-mode 3 --limit 10", next_command)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        pack = ResourcePack(Path(folder.name))
        self.pack = pack
        mocked = patch("kanamibot.strinova.render.resource_pack", return_value=pack)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_portrait_uses_first_frequent_role_even_when_model_defaults_to_michele(self):
        for first in (131, 146, 101):
            with (
                self.subTest(first=first),
                patch.object(
                    self.pack, "role_image", return_value="data:image/png;base64,portrait"
                ) as image,
            ):
                context = report_context(
                    Report(
                        Query("测试玩家"),
                        "123456",
                        {**PLAYER, "freq_roles": [first, 137]},
                        sections={
                            "career": {
                                "career_info": {
                                    "role_module": {"profile_show_type": 0, "role_id": 101}
                                }
                            }
                        },
                    )
                )
                self.assertEqual(context["portrait"], "data:image/png;base64,portrait")
                self.assertEqual(
                    context["portrait_name"], {131: "伊薇特", 146: "星绘", 101: "米雪儿·李"}[first]
                )
                self.assertEqual(context["portrait_team"], "常用角色")
                self.assertIn(((first,), {"portrait": True}), image.call_args_list)

    def test_portrait_falls_back_to_career_frequent_list_when_profile_has_none(self):
        with patch.object(self.pack, "role_image", return_value="career-portrait") as image:
            context = report_context(
                Report(
                    Query("测试玩家"),
                    "123456",
                    {**PLAYER, "freq_roles": []},
                    sections={"career": {"career_info": {"freq_roles": [137, 101]}}},
                )
            )
        self.assertEqual(context["portrait_name"], "香奈美")
        self.assertEqual(context["freq_roles"][0]["name"], "香奈美")
        self.assertIn(((137,), {"portrait": True}), image.call_args_list)

    def test_missing_frequent_role_or_artwork_never_displays_another_character(self):
        for roles in ([], None, {}, [None], [True], [999]):
            with self.subTest(roles=roles):
                context = report_context(
                    Report(
                        Query("测试玩家"),
                        "123456",
                        {**PLAYER, "freq_roles": roles},
                        sections={"career": {"career_info": {"role_module": {"role_id": 101}}}},
                    )
                )
                self.assertIsNone(context["portrait"])
                self.assertNotEqual(context["portrait_name"], "米雪儿·李")

    def test_every_view_renders_without_an_installed_resource_pack(self):
        for view in VIEWS:
            with self.subTest(view=view):
                html = render_html(
                    Report(Query("测试玩家", view=view, room_id=ROOM_ID), "123456", PLAYER)
                )
                self.assertIn("测试玩家", html)
                self.assertIn(
                    "#卡丘战绩 玩家名 玩家概览/引航者资料/生涯数据/对局战绩 可切换展示页", html
                )
                self.assertNotIn("切换喵", html)
                self.assertNotIn('src="http', html)

    def test_picture_includes_real_avatar_and_roundtrippable_switch_commands(self):
        with io.BytesIO() as buffer:
            Image.new("RGB", (4, 4), "green").save(buffer, format="PNG")
            avatar = image_data_uri(buffer.getvalue())
        report = Report(Query("测试玩家"), "123456", PLAYER, avatar_data_uri=avatar)
        html = render_html(report)
        self.assertIn(f'src="{avatar}"', html)
        self.assertIn("#卡丘战绩 玩家名 玩家概览/引航者资料/生涯数据/对局战绩 可切换展示页", html)
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
        self.assertEqual((row["score"], row["score_label"]), ("—", "多方对局"))
        gun_king = match_row(
            {"game_mode": 30, "my_team": 2, "scores": [739151, 736117, 1161000, 139081, 738063]}
        )
        self.assertEqual((gun_king["score"], gun_king["score_label"]), ("—", "无队伍比分"))
        self.assertEqual(match_row({"my_team": 1, "scores": [739151, 736117]})["score"], "—")

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
                self.assertNotIn("我方 : 对方", html)

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
                    SimpleNamespace(message=Message("#卡丘战绩 测试玩家")), matcher, self.bot
                )
        self.assertEqual(matcher.result.type, "image")
        self.assertTrue(matcher.result.data["file"].startswith("base64://"))
        matcher.send.assert_awaited_once_with("正在查询「测试玩家」的卡丘战绩……")
        self.bot.delete_msg.assert_awaited_once_with(message_id=456)

    async def test_health_failure_pauses_every_bot_entry_including_help(self):
        from nonebot.adapters.onebot.v11 import Message

        class Finished(Exception):
            pass

        for command in (
            "#卡丘战绩",
            "#卡丘战绩 help",
            "#卡丘资料 测试玩家",
            "#卡丘状态 测试玩家",
            "#卡丘生涯 测试玩家",
            "#卡丘战绩 测试玩家",
            "#卡丘赛季 测试玩家 --season-id 25",
            "#卡丘名片 测试玩家",
            "#卡丘赛季列表",
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

    async def test_disabled_commands_are_not_matched_or_executed(self):
        from nonebot.adapters.onebot.v11 import Message

        for command in (
            "#卡丘",
            "#卡丘 help",
            "#卡丘 测试玩家",
            "#KQ",
            "#KQ 测试玩家",
            "#kq 测试玩家",
            "#卡丘录像",
            f"#卡丘录像 {ROOM_ID}",
            f"#卡丘录像 --room-id {ROOM_ID}",
            "#卡丘对局",
            f"#卡丘对局 测试玩家 --room-id {ROOM_ID}",
        ):
            matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock())
            with (
                self.subTest(command=command),
                patch.object(self.plugin, "check_available", AsyncMock()) as health,
                patch.object(self.plugin, "create_report", AsyncMock()) as create,
                patch.object(self.plugin, "render_png", AsyncMock()) as render,
            ):
                self.assertIsNone(self.plugin.COMMAND.fullmatch(command))
                await self.plugin.handle_strinova(
                    SimpleNamespace(message=Message(command)), matcher, self.bot
                )
                health.assert_not_awaited()
                create.assert_not_awaited()
                render.assert_not_awaited()
                matcher.send.assert_not_awaited()
                matcher.finish.assert_not_awaited()

    async def test_disabled_views_cannot_be_called_through_other_qq_commands(self):
        from nonebot.adapters.onebot.v11 import Message

        class Finished(Exception):
            pass

        for command in (
            f"#卡丘战绩 测试玩家 --view match --room-id {ROOM_ID}",
            f"#卡丘战绩 --view replay --room-id {ROOM_ID}",
            f"#卡丘战绩 测试玩家 --view=replay --room-id {ROOM_ID}",
            f"#卡丘资料 测试玩家 单局详情 --room-id {ROOM_ID}",
            f"#卡丘战绩 测试玩家 对局录像 --room-id {ROOM_ID}",
            "#卡丘战绩 测试玩家 单局详情",
            "#卡丘战绩 测试玩家 对局录像",
            "#卡丘战绩 测试玩家 --view invalid",
        ):
            matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock(side_effect=Finished))
            with (
                self.subTest(command=command),
                patch.object(self.plugin, "check_available", AsyncMock()),
                patch.object(self.plugin, "create_report", AsyncMock()) as create,
                patch.object(self.plugin, "render_png", AsyncMock()) as render,
            ):
                with self.assertRaises(Finished):
                    await self.plugin.handle_strinova(
                        SimpleNamespace(message=Message(command)), matcher, self.bot
                    )
                matcher.send.assert_not_awaited()
                create.assert_not_awaited()
                render.assert_not_awaited()
                message = matcher.finish.call_args.args[0]
                choices = message.partition("(choose from ")[2]
                self.assertNotIn("'match'", choices)
                self.assertNotIn("'replay'", choices)
                self.assertNotIn("查询需要 --room-id", message)

        for hidden in ("卡丘录像", "卡丘对局", "单局详情", "对局录像", "--room-id", "KQ"):
            self.assertNotIn(hidden, self.plugin.__plugin_meta__.usage)
            self.assertNotIn(hidden, self.plugin.COMMAND.pattern)
        self.assertIsNone(re.search(r"#卡丘(?=\s|/|$)", self.plugin.USAGE))

    async def test_other_views_return_images_without_battle_query_prompt(self):
        from nonebot.adapters.onebot.v11 import Message

        class Finished(Exception):
            pass

        for command, view, target in (
            ("#卡丘赛季列表", "seasons", ""),
            ("#卡丘战绩 --view seasons", "seasons", ""),
            ("#卡丘名片 测试玩家", "card", "测试玩家"),
            ("#卡丘资料 测试玩家", "profile", "测试玩家"),
            ("#卡丘状态 测试玩家", "status", "测试玩家"),
            ("#卡丘生涯 测试玩家", "career", "测试玩家"),
            ("#卡丘赛季 测试玩家", "history", "测试玩家"),
            ("#卡丘战绩 测试玩家 生涯数据", "career", "测试玩家"),
        ):
            matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock(side_effect=Finished))
            report = Report(Query(target, view=view), "", PLAYER)
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
            actual = create.call_args.args[0]
            self.assertEqual((actual.view, actual.target), (view, target))
            self.assertEqual(matcher.finish.call_args.args[0].type, "image")
            matcher.send.assert_not_awaited()
            self.bot.delete_msg.assert_not_awaited()

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
                if view in ("overview", "standings"):
                    matcher.send.assert_awaited_once_with("正在查询「测试玩家」的卡丘战绩……")
                else:
                    matcher.send.assert_not_awaited()

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
        for view, target in (("match", ["测试玩家"]), ("replay", [])):
            query = query_from_args(
                cli.build_parser().parse_args([*target, "--view", view, "--room-id", ROOM_ID])
            )
            self.assertEqual((query.view, query.room_id), (view, ROOM_ID))
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
    def test_rank_badges_compose_transparent_numerals_and_handle_missing_layers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "mappings").mkdir()
            (root / "images").mkdir()
            Image.new("RGBA", (256, 256), "blue").save(root / "images/badge.png")
            Image.new("RGBA", (68, 64), (255, 0, 0, 128)).save(root / "images/level.png")
            row = {
                "id": 22,
                "verified": True,
                "extracted_paths": {
                    "IconDivision": {"png": "images/badge.png"},
                    "IconDivisionLevel": {"png": "images/level.png"},
                },
            }
            rows = {
                "rank": [
                    row,
                    {
                        **row,
                        "id": 29,
                        "extracted_paths": {"IconDivision": {"png": "images/badge.png"}},
                    },
                ],
                "leisure_rank": [row],
            }
            (root / "mappings/ranks.json").write_text(json.dumps(rows), encoding="utf-8")
            pack = ResourcePack(root)

            def decoded(uri):
                return Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))

            ranked = pack.rank_image(22)
            with decoded(ranked) as image:
                self.assertEqual(image.size, (256, 256))
                self.assertEqual(image.getpixel((128, 210)), (128, 0, 127, 255))
                self.assertEqual(image.getpixel((128, 80)), (0, 0, 255, 255))
            with decoded(pack.rank_image(22, leisure=True)) as image:
                self.assertEqual(image.getpixel((128, 80)), (128, 0, 127, 255))
                self.assertEqual(image.getpixel((128, 210)), (0, 0, 255, 255))
            self.assertEqual(pack.rank_image(22), ranked)
            self.assertEqual(pack.rank_image(29), pack.data_uri(root / "images/badge.png"))
            for invalid in (None, True, "22", 999, [], {}):
                self.assertIsNone(pack.rank_image(invalid))
            (root / "images/level.png").write_bytes(b"broken image")
            self.assertEqual(
                ResourcePack(root).rank_image(22), pack.data_uri(root / "images/badge.png")
            )
            (root / "images/badge.png").unlink()
            self.assertIsNone(ResourcePack(root).rank_image(22))

    def test_wiki_original_overrides_only_portrait_and_preserves_game_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "mappings").mkdir()
            game = root / "images/game.png"
            game.parent.mkdir()
            Image.new("RGB", (4, 4), "pink").save(game)
            (root / "mappings/characters.json").write_text(
                json.dumps(
                    [
                        {
                            "id": 131,
                            "verified": True,
                            "extracted_paths": {
                                "CareerIcon": {"png": "images/game.png"},
                                "IconRoleHud": {"png": "images/game.png"},
                            },
                        }
                    ]
                ),
                encoding="utf-8",
            )
            pack = ResourcePack(root)
            self.assertEqual(pack.role_image(131, portrait=True), pack.data_uri(game))
            original = root / "images/wiki/portraits/131.png"
            original.parent.mkdir(parents=True)
            Image.new("RGBA", (4, 4), "blue").save(original)
            self.assertEqual(pack.role_image(131, portrait=True), pack.data_uri(original))
            self.assertEqual(pack.role_image(131), pack.data_uri(game))
            original.with_name("999.png").write_bytes(original.read_bytes())
            self.assertIsNone(pack.role_image(999, portrait=True))

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
