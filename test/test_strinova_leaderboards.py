from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from kanamibot.strinova.client import APIError, Settings, StrinovaClient
from kanamibot.strinova.leaderboards import fetch_leaderboard


def row(position, *, rank=30, player_id=None, hero=False, score=250):
    return {
        "player_id": player_id or position, "nick": f"玩家{position}",
        "rank_division_data": {"rank": rank, "grade_score": 2800 + score},
        "rank_role_data": {"score": score, "win_games": 0},
        "bridge_display": {"rank_pos": position, "score": score,
                           "score_type": "hero_power" if hero else "grade_score_relative"},
    }


class LeaderboardTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler):
        def route(request):
            if request.url.path == "/health":
                return httpx.Response(200, json={"ok": True})
            if request.url.path == "/v1/leaderboards":
                data = {"current_season_id": 18}
            else:
                data = handler(request)
            return httpx.Response(200, json={"ok": True, "data": {"code": 0, **data}})
        return StrinovaClient(Settings(), transport=httpx.MockTransport(route))

    async def test_singularity_boundary_uses_display_score_and_partial_last_page(self):
        def handler(request):
            page = int(request.url.params["index"])
            self.assertEqual(request.url.params["season_id"], "18")
            return {"rank_total": 25, "version": 7, "rows_info": [
                row(i, rank=30 if i <= 23 else 29, score=350-i)
                for i in range((page-1)*10+1, min(page*10, 25)+1)
            ]}
        async with self.client(handler) as client:
            report = await fetch_leaderboard(client, "卡丘奇点", "")
        self.assertIn(("当前奇点末位积分", "327 分"), report.facts)
        self.assertIn(("当前奇点末位排名", "第 23 名"), report.facts)

    async def test_no_singularity_is_not_a_zero_score(self):
        async with self.client(lambda _: {
            "rank_total": 1, "rows_info": [row(1, rank=29)]
        }) as client:
            report = await fetch_leaderboard(client, "卡丘奇点", "")
        self.assertIn("暂无奇点", report.note)
        self.assertEqual(report.facts, [])

    async def test_refresh_between_pages_and_wrong_page_are_rejected(self):
        for wrong_page in (False, True):
            calls = []

            def handler(request, calls=calls, wrong_page=wrong_page):
                calls.append(request)
                page = int(request.url.params["index"])
                start = (page-1)*10+1
                return {"rank_total": 30, "version": 1 if wrong_page or len(calls) == 1 else 2,
                        "rows_info": [row(i) for i in range(
                            1 if wrong_page else start, (1 if wrong_page else start)+10)]}

            async with self.client(handler) as client:
                with self.assertRaises(APIError):
                    await fetch_leaderboard(client, "卡丘奇点", "")

    async def test_player_search_checks_id_and_does_not_use_upstream_self_rank(self):
        def handler(request):
            if request.url.path == "/v1/friends/search":
                return {"friends": [{"nick": "目标", "player_id": 123}]}
            return {"rows_info": [row(1, player_id=456)], "self_rank": row(2, player_id=123)}
        async with self.client(handler) as client:
            report = await fetch_leaderboard(client, "卡丘排名", "目标")
        self.assertEqual(report.rows, [])
        self.assertIn("未查询到", report.note)

    async def test_exact_player_id_and_zero_score_are_preserved(self):
        def handler(request):
            if request.url.path == "/v1/friends/search":
                return {"friends": [{"nick": "目标", "player_id": 123}]}
            return {"rows_info": [row(3, player_id=123, score=0)]}
        async with self.client(handler) as client:
            report = await fetch_leaderboard(client, "卡丘排名", "目标")
        self.assertEqual(report.rows[0]["position"], 3)
        self.assertEqual(report.rows[0]["score"], "0 分")

    async def test_role_name_resolves_id_and_hero_score_is_not_rank_score(self):
        def handler(request):
            self.assertEqual(request.url.params["role_id"], "137")
            return {"rank_type": 4, "sub_type": 137, "custom": 1,
                    "rows_info": [row(1, hero=True, score=0)]}
        with patch("kanamibot.strinova.leaderboards.resource_pack") as pack:
            pack.return_value.records.return_value = {
                137: {"id": 137, "name_cn": "香奈美", "name_en": "Kanami"}
            }
            async with self.client(handler) as client:
                report = await fetch_leaderboard(client, "卡丘角色榜", "香奈美")
        self.assertEqual(report.rows[0]["score"], "0 战力")
        self.assertEqual(report.rows[0]["detail"], "0 胜")

    async def test_wrong_role_response_is_rejected(self):
        with patch("kanamibot.strinova.leaderboards.resolve_role", return_value=(137, "香奈美")):
            async with self.client(lambda _: {
                "sub_type": 103, "rows_info": [row(1, hero=True)]
            }) as client:
                with self.assertRaisesRegex(APIError, "不一致"):
                    await fetch_leaderboard(client, "卡丘角色榜", "香奈美")

    async def test_lobby_not_ready_has_actionable_message(self):
        def handler(request):
            if request.url.path == "/health":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(503, json={
                "ok": False, "data": {"error_code": "lobby_not_ready"}
            })
        async with StrinovaClient(Settings(), transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(APIError, "进入大厅"):
                await fetch_leaderboard(client, "卡丘奇点", "")
