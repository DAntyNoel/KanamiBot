from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from kanamibot.strinova.client import APIError, Settings, StrinovaClient
from kanamibot.strinova.query import QueryError, parse_query
from kanamibot.strinova.render import match_row, render_html, report_context
from kanamibot.strinova.service import Report, fetch_report, fetch_standings_page

PLAYER = {"player_id": "18446744073709551615", "nick": "测试玩家"}
ROOM = "18446744073709551614"
CATALOG = {
    "current_main_season_id": 18,
    "seasons": [
        {"season_id": 17, "season_type": 1, "name": "历史测试季", "available": True},
        {"season_id": 18, "season_type": 1, "name": "当前测试季", "available": True},
        {"season_id": 19, "season_type": 1, "name": "未来测试季", "available": False},
    ],
}


def fake_client():
    return SimpleNamespace(
        search=AsyncMock(return_value={"friends": [PLAYER]}),
        profile=AsyncMock(return_value={"friend": PLAYER}),
        seasons=AsyncMock(return_value=CATALOG),
        career=AsyncMock(
            return_value={
                "career_info": {
                    "player": PLAYER,
                    "battle_infos": [{"game_mode": 5, "round": 10}],
                    "role_employs": [{"role_id": 137, "game_mode": 5, "count": 5}],
                }
            }
        ),
        history=AsyncMock(
            return_value={
                "player_id": PLAYER["player_id"],
                "season_id": 17,
                "battle_infos": [
                    {"game_mode": 2, "round": 3, "total_win_count": 0, "total_kill": 0}
                ],
                "role_employs": [{"role_id": 146, "game_mode": 2, "count": 2}],
                "freq_roles": [146],
                "final_seg": 0,
            }
        ),
    )


class NewQueryTests(unittest.TestCase):
    def test_new_views_uint64_and_season_mode_filters(self):
        self.assertTrue(parse_query(PLAYER["player_id"]).by_id)
        self.assertEqual(parse_query("--view seasons").target, "")
        self.assertEqual(parse_query(f"--view replay --room-id {ROOM}").room_id, ROOM)
        self.assertEqual(parse_query("测试玩家 玩家名片").view, "card")
        query = parse_query("测试玩家 历史赛季 排位 --season-id 17")
        self.assertEqual((query.game_mode, query.season_id), (5, 17))
        self.assertEqual(
            parse_query("测试玩家 --view career --season-id 4294967295").season_id, 4294967295
        )
        for text in (
            "18446744073709551616",
            "测试玩家 --view replay",
            "--view card",
            "测试玩家 对局战绩 --season-id 17",
            "测试玩家 --view history --season-type 2",
            "测试玩家 --view career --season-id 4294967296",
        ):
            with self.subTest(text=text), self.assertRaises(QueryError):
                parse_query(text)


class NewClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_code_routes_and_key_on_health_and_data(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path == "/health":
                return httpx.Response(200, json={"ok": True})
            data = {"code": 0} if request.url.path.endswith("/icons") else {}
            return httpx.Response(200, json={"ok": True, "data": data})

        settings = Settings(api_key="test-key")
        self.assertNotIn("test-key", repr(settings))
        async with StrinovaClient(settings, transport=httpx.MockTransport(handler)) as client:
            await client.me()
            await client.personalization()
            await client.icons()
            await client.seasons()
            with self.assertRaises(APIError):
                await client.card(PLAYER["player_id"])
        self.assertTrue(all(request.headers.get("X-API-Key") == "test-key" for request in requests))
        self.assertTrue(all(not request.url.query for request in requests))

    async def test_authentication_failure_does_not_retry_or_leak_key(self):
        with patch("kanamibot.strinova.client.asyncio.sleep", new_callable=AsyncMock) as sleep:
            async with StrinovaClient(
                Settings(api_key="test-secret"),
                transport=httpx.MockTransport(lambda _: httpx.Response(401)),
            ) as client:
                with self.assertRaisesRegex(APIError, "鉴权失败") as caught:
                    await client.seasons()
        sleep.assert_not_awaited()
        self.assertNotIn("test-secret", str(caught.exception))

    async def test_codes_and_missing_success_are_not_empty_statistics(self):
        for code, message in (
            (8603, "开启了隐私保护"),
            (8609, "同一战区"),
            (8610, "频繁"),
            (8611, "当日次数"),
            (8704, "版本不匹配"),
            (2, "业务代码 2"),
            (None, "业务代码 None"),
            (False, "业务代码 False"),
        ):

            def handler(request, code=code):
                data = {"code": code}
                return httpx.Response(
                    200,
                    json={"ok": True, "data": data}
                    if request.url.path != "/health"
                    else {"ok": True},
                )

            async with StrinovaClient(Settings(), transport=httpx.MockTransport(handler)) as client:
                with self.subTest(code=code), self.assertRaisesRegex(APIError, message):
                    await client.card(PLAYER["player_id"])

    async def test_replay_room_identity_and_url_are_validated_without_download(self):
        for room, url, success in (
            (ROOM, "https://example.invalid/demo.replay", True),
            ("1", "https://example.invalid/demo.replay", False),
            (ROOM, "", False),
            (ROOM, "javascript:alert(1)", False),
            (ROOM, "https://user:password@example.invalid/a", False),
        ):
            requests = []

            def handler(request, requests=requests, room=room, url=url):
                requests.append(request.url.path)
                return httpx.Response(
                    200,
                    json={"ok": True}
                    if request.url.path == "/health"
                    else {"ok": True, "data": {"code": 0, "room_id": room, "download_url": url}},
                )

            async with StrinovaClient(Settings(), transport=httpx.MockTransport(handler)) as client:
                if success:
                    self.assertEqual((await client.replay(ROOM))["download_url"], url)
                else:
                    with self.assertRaises(APIError):
                        await client.replay(ROOM)
            self.assertEqual(requests, ["/health", f"/v1/replays/{ROOM}"])


class SeasonRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_pages_preserve_season_filters_and_limit(self):
        client = fake_client()
        client.history.return_value["role_employs"] = [
            {"game_mode": 5, "role_id": 137, "count": 30 - index} for index in range(25)
        ]
        report = await fetch_report(
            client, parse_query("测试玩家 历史赛季 排位 2 --season-id 17 --limit 10")
        )
        context = report_context(report)
        self.assertEqual([row["count"] for row in context["roles"]], list(range(20, 10, -1)))
        self.assertEqual(context["role_count"], 25)
        for command, page in (
            (context["previous_page_command"], 1),
            (context["next_page_command"], 3),
        ):
            query = parse_query(command.removeprefix("#卡丘战绩 "))
            self.assertEqual(
                (
                    query.target,
                    query.view,
                    query.index,
                    query.season_id,
                    query.game_mode,
                    query.limit,
                ),
                ("测试玩家", "history", page, 17, 5, 10),
            )

    async def test_history_defaults_to_latest_finished_season_with_roles(self):
        client = fake_client()
        report = await fetch_report(client, parse_query("测试玩家 历史赛季"))
        client.history.assert_awaited_once_with(PLAYER["player_id"], 17)
        client.career.assert_not_awaited()
        context = report_context(report)
        self.assertEqual(context["season_id"], 17)
        self.assertEqual(context["modes"][0]["round"], 3)
        self.assertEqual(context["roles"][0]["role_id"], 146)
        self.assertEqual(dict(context["metrics"])["赛季胜率"], "0.0%")
        self.assertIn("历史测试季", render_html(report))

    async def test_current_season_never_uses_history_or_duplicate_battle_query(self):
        for text in ("测试玩家 生涯数据", "测试玩家 历史赛季 --season-id 18"):
            client = fake_client()
            report = await fetch_report(client, parse_query(text))
            client.career.assert_awaited_once_with(PLAYER["player_id"])
            client.history.assert_not_awaited()
            self.assertTrue(report_context(report)["season_current"])
            self.assertEqual(report_context(report)["roles"][0]["role_id"], 137)

    async def test_past_career_uses_only_that_seasons_stats_and_filters_roles(self):
        client = fake_client()
        report = await fetch_report(
            client, parse_query("测试玩家 生涯数据 --season-id 17 --mode 一般爆破")
        )
        client.career.assert_not_awaited()
        self.assertEqual(report_context(report)["roles"][0]["role_id"], 146)
        other_mode = await fetch_report(
            client, parse_query("测试玩家 历史赛季 --season-id 17 --mode 排位")
        )
        self.assertEqual(report_context(other_mode)["roles"], [])
        self.assertEqual(report_context(other_mode)["modes"], [])

    async def test_mismatched_history_player_zero_and_season_are_rejected(self):
        for update in ({"player_id": 0}, {"season_id": 16}, {"player_id": "1"}):
            client = fake_client()
            client.history.return_value.update(update)
            with self.subTest(update=update), self.assertRaisesRegex(APIError, "不一致"):
                await fetch_report(client, parse_query("测试玩家 历史赛季"))

    async def test_future_season_and_failed_history_never_use_current_stats(self):
        for season_id in (17, 19, 99):
            client = fake_client()
            client.history.side_effect = APIError("数据暂不可用")
            report = await fetch_report(
                client, parse_query(f"测试玩家 生涯数据 --season-id {season_id}")
            )
            client.career.assert_not_awaited()
            self.assertEqual(report_context(report)["modes"], [])
            self.assertTrue(report.warnings)

    async def test_season_directory_and_replay_do_not_search_a_player(self):
        client = fake_client()
        report = await fetch_report(client, parse_query("--view seasons"))
        self.assertEqual(len(report_context(report)["season_rows"]), 3)
        client.search.assert_not_awaited()
        client.replay = AsyncMock(return_value={"room_id": ROOM, "download_url": "https://x/a"})
        report = await fetch_report(client, parse_query(f"--view replay --room-id {ROOM}"))
        self.assertIn("https://x/a", render_html(report))
        client.search.assert_not_awaited()

    async def test_card_identity_and_privacy_stop_protected_queries(self):
        client = fake_client()
        client.card = AsyncMock(return_value={"player_info": {"player_id": "1"}})
        with self.assertRaisesRegex(APIError, "不一致"):
            await fetch_report(client, parse_query("测试玩家 玩家名片"))
        client.status = AsyncMock(return_value={"friend": PLAYER})
        client.career.side_effect = APIError("开启了隐私保护", code=8603)
        client.standings = AsyncMock()
        report = await fetch_report(client, parse_query("测试玩家"))
        client.standings.assert_not_awaited()
        self.assertIn("隐私", render_html(report))


class EnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_budget_privacy_and_cache_are_respected(self):
        for status in ("deferred", "privacy", "unavailable", "detail_cache"):
            client = SimpleNamespace(
                standings=AsyncMock(
                    return_value={
                        "titles": [
                            {
                                "room_id": ROOM,
                                "role_id": 0,
                                "bridge_stats_status": status,
                                "kill_num": 0,
                                "dead_num": 0,
                                "assists_num": 0,
                            }
                        ],
                        "bridge_enrichment": {"version": 1, "reason": "budget"},
                    }
                ),
                match=AsyncMock(),
            )
            page = await fetch_standings_page(
                client, PLAYER["player_id"], parse_query("测试玩家 对局战绩")
            )
            client.match.assert_not_awaited()
            expected = "0 / 0 / 0" if status == "detail_cache" else "— / — / —"
            self.assertEqual(match_row(page["titles"][0])["kda"], expected)

    def test_external_card_and_season_content_are_escaped(self):
        report = Report(
            parse_query("--view seasons"),
            "",
            {},
            sections={
                "seasons": {
                    "seasons": [{"season_id": 17, "name": "<script>bad</script>"}],
                }
            },
        )
        self.assertIn("&lt;script&gt;bad&lt;/script&gt;", render_html(report))


if __name__ == "__main__":
    unittest.main()
