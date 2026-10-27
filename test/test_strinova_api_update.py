from __future__ import annotations

import unittest
from copy import deepcopy
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
    def test_history_command_season_shortcuts_are_distinct_from_pages(self):
        for text, expected_id, expected_label, expected_page, expected_mode in (
            ("测试玩家 14", 14, None, 1, 0),
            ("测试玩家 14 排位", 14, None, 1, 5),
            ("测试玩家 排位 14 --page 2", 14, None, 2, 5),
            ("测试玩家 14 第2页", 14, None, 2, 0),
            ("测试玩家 SP3", 0, "SP3", 1, 0),
            ("测试玩家 SP3 2", 0, "SP3", 2, 0),
            ('"Player 14" S8 3', 0, "S8", 3, 0),
            ("测试玩家 SP3 2 排位", 0, "SP3", 2, 5),
            ("测试玩家 sp2 排位 第2页", 0, "SP2", 2, 5),
            ("测试玩家 S1 --index 2", 0, "S1", 2, 0),
            ("测试玩家 s8", 0, "S8", 1, 0),
            ("测试玩家 26SP3", 0, "26SP3", 1, 0),
            ("测试玩家 14 --season-id 14", 14, None, 1, 0),
        ):
            with self.subTest(text=text):
                query = parse_query(text, view="history", season_shortcuts=True)
                self.assertEqual(query.target, "Player 14" if text.startswith('"') else "测试玩家")
                self.assertEqual(
                    (query.season_id, query.season_label, query.index, query.game_mode),
                    (expected_id, expected_label, expected_page, expected_mode),
                )
        for text in ("测试玩家 14", "测试玩家 历史赛季 14"):
            query = parse_query(text)
            self.assertEqual((query.index, query.season_id, query.season_label), (14, 0, None))

    def test_history_shortcuts_preserve_player_identity_and_reject_conflicts(self):
        for text, target, by_id, season_id in (
            ('"Player 14"', "Player 14", False, 0),
            ('"Player SP3" 14', "Player SP3", False, 14),
            ("--nick 12345 14", "12345", False, 14),
            ("--player-id 12345 14", "12345", True, 14),
            ("12345", "12345", True, 0),
        ):
            with self.subTest(text=text):
                query = parse_query(text, view="history", season_shortcuts=True)
                self.assertEqual(
                    (query.target, query.by_id, query.season_id), (target, by_id, season_id)
                )
        for text in (
            "测试玩家 14 --season-id 15",
            "测试玩家 14 15",
            "测试玩家 SP3 SP2",
            "测试玩家 SP3 --view overview",
            "测试玩家 14 第2页 --page 3",
            "测试玩家 SP3 2 --page 3",
            "测试玩家 SP3 2 3",
            "测试玩家 4294967296",
        ):
            with self.subTest(text=text), self.assertRaises(QueryError):
                parse_query(text, view="history", season_shortcuts=True)
        self.assertEqual(parse_query("测试玩家 SP3").target, "测试玩家 SP3")

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
    async def test_short_season_names_resolve_to_catalog_ids_and_preserve_pagination(self):
        for label, season_id in (("SP3", 16), ("sp2", 15), ("S1", 1), ("S8", 8), ("26SP3", 16)):
            client = fake_client()
            client.seasons.return_value = {
                "current_main_season_id": 18,
                "seasons": [
                    {"season_id": sid, "season_type": 1, "available": True, "name": name}
                    for sid, name in (
                        (1, "S1.测试"),
                        (8, "S8.测试"),
                        (15, "26SP2.测试"),
                        (16, "26SP3.测试"),
                    )
                ],
            }
            client.history.return_value["season_id"] = season_id
            client.history.return_value["role_employs"] *= 20
            with self.subTest(label=label):
                report = await fetch_report(
                    client, parse_query(f"测试玩家 {label}", view="history", season_shortcuts=True)
                )
                client.history.assert_awaited_once_with(PLAYER["player_id"], season_id)
                client.career.assert_not_awaited()
                context = report_context(report)
                self.assertEqual(context["season_id"], season_id)
                command = context["next_page_command"]
                self.assertEqual(
                    command, f"#卡丘赛季 测试玩家 {label.upper().removeprefix('26')} 2"
                )
                next_query = parse_query(
                    command.removeprefix("#卡丘赛季 "), view="history", season_shortcuts=True
                )
                next_report = await fetch_report(client, next_query)
                self.assertEqual(
                    (report_context(next_report)["season_id"], next_query.index), (season_id, 2)
                )

    async def test_unknown_ambiguous_and_conflicting_season_labels_never_fall_back(self):
        for names, label, season_id, error in (
            (["26SP3.测试"], "SP9", 0, "未找到"),
            (["26SP3.测试", "27SP3.测试"], "SP3", 0, "多个赛季"),
            (["26SP3.测试"], "SP3", 14, "不一致"),
        ):
            client = fake_client()
            client.seasons.return_value = {
                "current_main_season_id": 18,
                "seasons": [
                    {"season_id": 16 + index, "season_type": 1, "available": True, "name": name}
                    for index, name in enumerate(names)
                ],
            }
            options = f" --season-id {season_id}" if season_id else ""
            with self.subTest(label=label, error=error), self.assertRaisesRegex(QueryError, error):
                await fetch_report(
                    client,
                    parse_query(
                        f"测试玩家 {label}{options}", view="history", season_shortcuts=True
                    ),
                )
            client.history.assert_not_awaited()
            client.career.assert_not_awaited()

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
            query = parse_query(
                command.removeprefix("#卡丘赛季 "), view="history", season_shortcuts=True
            )
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


class SeasonPresentationTests(unittest.TestCase):
    def report(self, stats, *, current=False, target="测试玩家", options=""):
        return Report(
            parse_query(f'--nick "{target}" --view career {options}'),
            PLAYER["player_id"],
            {**PLAYER, "rank": 30, "leisure_rank": 41},
            sections={
                "season": {"season_id": 16, "name": "26SP3.测试", "is_current": current},
                "seasons": {"seasons": [{"season_id": 16, "season_type": 1, "name": "26SP3.测试"}]},
                **({"career": {"career_info": stats}} if current else {"history": stats}),
            },
        )

    def test_ranked_metrics_use_selected_season_mode_values_including_zero_wins(self):
        for current in (False, True):
            for wins in (0, 6):
                with self.subTest(current=current, wins=wins):
                    stats = {
                        "battle_infos": [
                            {"game_mode": 2, "round": 20, "total_win_count": 18, "total_kill": 100},
                            {
                                "game_mode": 5,
                                "round": 10,
                                "total_win_count": wins,
                                "win_rate": 0.75 if wins else 0,
                                "total_kill": 50,
                            },
                        ]
                    }
                    original = deepcopy(stats)
                    report = self.report(stats, current=current)
                    metrics = dict(report_context(report)["metrics"])
                    self.assertEqual(
                        metrics,
                        {
                            "赛季对局": "30",
                            "排位爆破胜场": str(wins),
                            "排位爆破胜率": "75.0%" if wins else "0.0%",
                            "赛季击杀": "150",
                        },
                    )
                    self.assertIn("排位爆破胜率", render_html(report))
                    self.assertEqual(stats, original)

    def test_unplayed_ranked_keeps_season_totals_and_missing_ranked_stats_stay_missing(self):
        for ranked in ([], [{"game_mode": 5, "round": 0, "total_win_count": 0}]):
            stats = {
                "battle_infos": [
                    {"game_mode": 2, "round": 10, "total_win_count": 3},
                    *ranked,
                ]
            }
            metrics = dict(report_context(self.report(stats))["metrics"])
            self.assertEqual(metrics["赛季胜场"], "3")
            self.assertEqual(metrics["赛季胜率"], "30.0%")
        stats = {"battle_infos": [{"game_mode": 5, "round": 10}]}
        metrics = dict(report_context(self.report(stats))["metrics"])
        self.assertEqual(metrics["排位爆破胜场"], "—")
        self.assertEqual(metrics["排位爆破胜率"], "—")

    def test_season_badges_use_season_ranks_instead_of_profile_or_peak(self):
        for current in (False, True):
            stats = {
                "cur_seg": 12,
                "final_seg": 22,
                "top_seg": 29,
                "cur_leisure_rank": 11,
                "top_history_leisure_rank": 41,
            }
            report = self.report(stats, current=current)
            with patch("kanamibot.strinova.render.resource_pack") as resource:
                pack = resource.return_value
                pack.records.return_value = {}
                pack.mode_name.return_value = None
                pack.rank_image.side_effect = lambda value, **kw: f"badge-{value}"
                context = report_context(report)
                cards = context["season_rank_cards"]
                self.assertEqual(
                    [card["badge"] for card in cards],
                    ["badge-12" if current else "badge-22", "badge-11"],
                )
                self.assertEqual(cards[0]["label"], "当前排位" if current else "最终排位")
                html = render_html(report)
                self.assertIn('class="season-ranks"', html)
                self.assertIn('src="badge-11"', html)
        cards = report_context(self.report({}))["season_rank_cards"]
        self.assertEqual([card["name"] for card in cards], ["—", "—"])
        self.assertEqual([card["badge"] for card in cards], [None, None])

    def test_season_page_commands_preserve_identity_and_show_real_values_in_footer(self):
        for target in ("测试玩家", "Player 14", "12345"):
            report = self.report(
                {"role_employs": [{"count": 1}] * 25},
                target=target,
                options="--index 2 --limit 10",
            )
            context = report_context(report)
            self.assertTrue(context["next_page_command"].startswith("#卡丘赛季 "))
            for key, page in (("previous_page_command", 1), ("next_page_command", 3)):
                command = context[key]
                query = parse_query(
                    command.removeprefix("#卡丘赛季 "), view="history", season_shortcuts=True
                )
                self.assertEqual(
                    (query.target, query.by_id, query.season_label, query.index, query.limit),
                    (target, False, "SP3", page, 10),
                )
            html = render_html(report)
            self.assertIn("下一页：<code>", html)
            self.assertIn("SP3 3 --limit 10", html)
            self.assertNotIn("赛季简称", html)
        self.assertEqual(
            report_context(self.report({"role_employs": [{"count": 1}] * 9}))["next_page_command"],
            "#卡丘赛季 测试玩家 SP3 2",
        )

    def test_ambiguous_season_abbreviations_keep_year(self):
        report = self.report({"role_employs": [{"count": 1}] * 9})
        report.sections["seasons"]["seasons"].append(
            {"season_id": 20, "season_type": 1, "name": "27SP3.测试"}
        )
        self.assertEqual(report_context(report)["next_page_command"], "#卡丘赛季 测试玩家 26SP3 2")


class HistoricalZeroTests(unittest.TestCase):
    def report(self, stats, *, current=False, options="", view="history"):
        return Report(
            parse_query(f"测试玩家 --view {view} {options}"),
            PLAYER["player_id"],
            PLAYER,
            sections={
                "season": {"season_id": 18 if current else 17, "is_current": current},
                **({"career": {"career_info": stats}} if current else {"history": stats}),
            },
        )

    def test_only_observed_historical_placeholders_are_hidden(self):
        stats = {
            "final_seg": 29,
            "top_seg": 29,
            "final_stars": 0,
            "top_stars": 0.0,
            "final_rank_pos": 0,
            "top_rank_pos": 0,
            "final_grade_score": 0,
            "top_grade_score": 3008,
            "final_division_score": 0,
            "top_division_score": 208,
            "battle_infos": [{"game_mode": 5, "round": 1, "total_win_count": 0}],
            "role_employs": [{"role_id": 137, "count": 1, "pure_win_rate": 0}],
        }
        original = deepcopy(stats)
        for view in ("history", "career"):
            with self.subTest(view=view):
                report = self.report(stats, view=view)
                context = report_context(report)
                facts = dict(context["season_facts"])
                self.assertTrue(
                    {"最终星数", "最高星数", "最终排名", "最高排名", "最终评价分"}.isdisjoint(facts)
                )
                self.assertEqual(facts["最终段位分"], "0")
                self.assertEqual(facts["最高段位分"], "208")
                self.assertEqual(facts["最高评价分"], "3,008")
                self.assertEqual(dict(context["metrics"])["排位爆破胜场"], "0")
                self.assertNotIn("原始胜率", render_html(report))
        self.assertEqual(stats, original)

    def test_historical_fields_return_when_upstream_supplies_nonzero_values(self):
        stats = {
            "final_stars": 1,
            "top_stars": 2,
            "final_rank_pos": 100,
            "top_rank_pos": 50,
            "final_grade_score": 3000,
        }
        facts = dict(report_context(self.report(stats))["season_facts"])
        for label, value in (
            ("最终星数", "1"),
            ("最高星数", "2"),
            ("最终排名", "100"),
            ("最高排名", "50"),
            ("最终评价分", "3,000"),
        ):
            self.assertEqual(facts[label], value)

    def test_role_rate_visibility_uses_full_season_before_filter_and_page(self):
        stats = {
            "role_employs": [
                {"role_id": 137, "game_mode": 5, "count": 3, "pure_win_rate": 0},
                {"role_id": 146, "game_mode": 5, "count": 2, "pure_win_rate": 0},
                {"role_id": 126, "game_mode": 2, "count": 1, "pure_win_rate": 0.5},
            ]
        }
        for page in (1, 2):
            with self.subTest(page=page):
                report = self.report(stats, options=f"--mode 排位 --limit 1 --index {page}")
                self.assertTrue(report_context(report)["show_role_win_rate"])
                self.assertIn("<th>原始胜率</th>", render_html(report))
                self.assertIn("<td>0.0%</td>", render_html(report))

    def test_current_and_missing_values_are_not_classified_as_zero_placeholders(self):
        stats = {
            "cur_stars": 0,
            "top_stars": 0,
            "cur_rank_pos": 0,
            "top_rank_pos": 0,
            "final_grade_score": 0,
            "role_employs": [{"role_id": 137, "count": 1, "pure_win_rate": 0}],
        }
        report = self.report(stats, current=True)
        facts = dict(report_context(report)["season_facts"])
        for label in ("当前星数", "最高星数", "当前排名", "最高排名", "最终评价分"):
            self.assertEqual(facts[label], "0")
        self.assertIn("<th>原始胜率</th>", render_html(report))
        stats = {"role_employs": [{"role_id": 137, "pure_win_rate": None}]}
        context = report_context(self.report(stats))
        self.assertEqual(dict(context["season_facts"])["最终评价分"], "—")
        self.assertTrue(context["show_role_win_rate"])


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
