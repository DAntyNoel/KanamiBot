"""Read-only QQ leaderboard queries; never expose the upstream account's self_rank."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from .client import APIError, IdentityMismatch, Settings, StrinovaClient
from .query import Query, QueryError
from .resources import resource_pack
from .service import resolve_player

COMMANDS = {"卡丘奇点", "卡丘排名", "卡丘角色榜"}
USAGE = "#卡丘奇点\n#卡丘排名 昵称\n#卡丘角色榜 角色"


@dataclass
class LeaderboardReport:
    title: str
    subtitle: str
    facts: list[tuple[str, str]] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    note: str = "榜单按游戏服务器刷新，查询时间不代表榜单刷新时间。"
    fetched_at: str = field(
        default_factory=lambda: datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M")
    )

    def text(self) -> str:
        lines = [self.title, self.subtitle]
        lines.extend(f"{key}：{value}" for key, value in self.facts)
        lines.extend(
            f"第 {row['position']} 名 · {row['nick']} · {row['score']} · {row['detail']}"
            for row in self.rows
        )
        return "\n".join([*lines, f"查询时间：{self.fetched_at}", self.note])


def resolve_role(value: str) -> tuple[int, str]:
    roles = resource_pack().records("characters")
    matches = [
        row for row in roles.values()
        if value.casefold() in {
            str(row["id"]), str(row.get("name_cn", "")).casefold(),
            str(row.get("name_en", "")).casefold(), str(row.get("name_key", "")).casefold(),
        }
    ]
    if not matches:
        matches = [row for row in roles.values() if value in row.get("name_cn", "")]
    if len(matches) != 1:
        names = "、".join(row["name_cn"] for row in (matches or roles.values()))
        raise QueryError(f"角色名称不明确，请填写完整角色名：{names}")
    return matches[0]["id"], matches[0]["name_cn"]


def board_rows(data: dict) -> list[dict]:
    rows = data.get("rows_info")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise APIError("排行榜数据结构不符合预期。")
    return rows


def display_row(row: dict, *, hero: bool = False) -> dict:
    display = row.get("bridge_display") or {}
    position = display.get("rank_pos")
    if type(position) is not int or position <= 0:
        raise APIError("排行榜未返回有效名次。")
    score = display.get("score")
    if hero and display.get("score_type") != "hero_power":
        raise APIError("角色榜未返回有效战力数据。")
    unit = "战力" if hero else "星" if display.get("score_type") == "stars" else "分"
    score_text = f"{score} {unit}" if type(score) is int else "积分未提供"
    division = row.get("rank_division_data") or {}
    rank = resource_pack().records("ranks", "rank").get(division.get("rank"), {})
    wins = (row.get("rank_role_data") or {}).get("win_games")
    detail = (
        f"{wins} 胜" if hero and type(wins) is int
        else "" if hero else rank.get("name", "段位未提供")
    )
    return {
        "position": position, "nick": str(row.get("nick") or "昵称未提供"),
        "score": score_text, "detail": detail,
    }


async def query_board(
    client: StrinovaClient, kind: int, *, search: bool = False, **params: int | str
) -> dict:
    data = await client.get(f"v1/leaderboards/{kind}" + ("/search" if search else ""), **params)
    expected = {"rank_type": kind, "city_code": 0, "sub_type": params.get("role_id", 0),
                "custom": 1 if kind == 4 else params["season_id"]}
    for key, value in expected.items():
        if key in data and data[key] != value:
            raise IdentityMismatch("排行榜类型、赛季或角色与请求不一致。")
    return data


async def fetch_leaderboard(
    client: StrinovaClient, command: str, args: str
) -> LeaderboardReport:
    args = args.strip()
    if command not in COMMANDS or args in ("帮助", "help", "--help"):
        raise QueryError(USAGE)
    if (command == "卡丘奇点" and args) or (command != "卡丘奇点" and not args):
        raise QueryError(USAGE)
    role = resolve_role(args) if command == "卡丘角色榜" else None
    catalog = await client.get("v1/leaderboards", allow_missing_code=True)
    season = catalog.get("current_season_id")
    if type(season) is not int or season <= 0:
        raise APIError("排行榜未返回当前赛季。")
    subtitle = f"当前赛季 #{season} · 全区榜"
    if role:
        role_id, name = role
        data = await query_board(client, 4, role_id=role_id, index=1)
        rows = board_rows(data)
        return LeaderboardReport(
            f"{name} · 角色战力榜", "全区榜 · 前 10 名",
            rows=[display_row(row, hero=True) for row in rows],
            note="暂无公开榜单数据。" if not rows else LeaderboardReport.note,
        )
    if command == "卡丘排名":
        # Resolve the exact player first. Search replies and self_rank are not identity proof.
        player = await resolve_player(client, Query(args, by_id=False))
        data = await query_board(client, 1, search=True, nick=player["nick"], season_id=season)
        rows = [
            row for row in board_rows(data)
            if str(row.get("player_id")) == str(player["player_id"])
        ]
        if len(rows) > 1:
            raise APIError("排行榜返回了重复玩家，暂时无法确认名次。")
        return LeaderboardReport(
            f"{player['nick']} · 段位排名", subtitle,
            facts=[("玩家 ID", str(player["player_id"]))],
            rows=[display_row(row) for row in rows],
            note="未查询到该玩家的公开榜内排名。" if not rows else LeaderboardReport.note,
        )
    return await fetch_singularity(client, season, subtitle)


async def fetch_singularity(
    client: StrinovaClient, season: int, subtitle: str
) -> LeaderboardReport:
    """Locate the last currently classified Singularity in the public ranked list."""
    pages: dict[int, list[dict]] = {}

    async def page(index: int) -> tuple[dict, list[dict]]:
        data = await query_board(client, 1, season_id=season, index=index)
        rows = board_rows(data)
        if not rows:
            raise APIError("排行榜边界数据暂不可用，请稍后重试。")
        if pages and (
            data.get("rank_total") != first.get("rank_total")
            or data.get("version") != first.get("version")
        ):
            raise APIError("排行榜正在刷新，请稍后重新查询奇点线。")
        for offset, row in enumerate(rows):
            rank = (row.get("rank_division_data") or {}).get("rank")
            if type(rank) is not int or not 0 <= rank <= 30:
                raise APIError("排行榜缺少有效段位，暂时无法确定奇点线。")
            if display_row(row)["position"] != (index - 1) * 10 + offset + 1:
                raise APIError("排行榜返回了不匹配的页码，暂时无法确定奇点线。")
        pages[index] = rows
        return data, rows

    first, rows = await page(1)
    total = first.get("rank_total")
    if type(total) is not int or not 0 < total <= 10000:
        raise APIError("排行榜未返回有效公开人数，暂时无法确定奇点线。")
    if (rows[0].get("rank_division_data") or {}).get("rank") != 30:
        return LeaderboardReport("当前奇点分数线", subtitle, note="当前公开榜单暂无奇点玩家。")
    # Rank order is monotone in score. Search pages rather than downloading the entire list.
    low, high = 1, (total + 9) // 10
    boundary = rows[0]
    while low <= high:
        middle = (low + high) // 2
        if middle not in pages:
            await page(middle)
        if len(pages[middle]) != min(10, total - (middle - 1) * 10):
            raise APIError("排行榜边界页不完整，请稍后重试。")
        singularity = [
            row for row in pages[middle]
            if (row.get("rank_division_data") or {}).get("rank") == 30
        ]
        if singularity:
            boundary = singularity[-1]
            low = middle + 1
        else:
            high = middle - 1
    display = display_row(boundary)
    if type((boundary.get("bridge_display") or {}).get("score")) is not int:
        raise APIError("奇点边界积分暂不可用，请稍后重试。")
    return LeaderboardReport(
        "当前奇点分数线", subtitle,
        facts=[("当前奇点末位积分", display["score"]),
               ("当前奇点末位排名", f"第 {display['position']} 名")],
        note="按公开榜中当前奇点玩家的末位积分统计；同分排序、刷新和晋级结算以游戏为准。",
    )


async def create_leaderboard(command: str, args: str, settings: Settings) -> LeaderboardReport:
    async with StrinovaClient(settings) as client:
        return await fetch_leaderboard(client, command, args)
