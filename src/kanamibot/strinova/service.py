from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from kanamibot.core.timing import timed

from .avatar import AvatarError, load_avatar
from .client import APIError, IdentityMismatch, ServiceUnavailable, Settings, StrinovaClient
from .query import SEASON_LABEL, Query, QueryError, numeric_id

QUERY_TIMEOUT_SECONDS = 120
# The standings API indexes batches of 20; image pages use the query's smaller limit.
STANDINGS_BATCH_SIZE = 20
# Detail enrichment is optional and shares a small budget across the visible page.
STANDINGS_DETAIL_TIMEOUT_SECONDS = 5
STANDINGS_DETAIL_BUDGET_SECONDS = 10


@dataclass
class Report:
    query: Query
    player_id: str
    profile: dict[str, Any]
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    sections: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    avatar_data_uri: str | None = None


@timed()
async def resolve_player(client: StrinovaClient, query: Query) -> dict:
    data = await client.search(
        **({"player_id": query.target} if query.by_id else {"nick": query.target})
    )
    friends = data.get("friends")
    if not isinstance(friends, list) or not all(isinstance(item, dict) for item in friends):
        raise APIError("玩家搜索结果结构不符合预期。")
    if not friends:
        raise QueryError(f"未找到玩家「{query.target}」。")
    matches = [
        item
        for item in friends
        if (
            str(item.get("player_id")) == str(int(query.target))
            if query.by_id
            else item.get("nick") == query.target
        )
    ]
    if len(matches) == 1:
        return matches[0]
    candidates = "\n".join(
        f"{item.get('nick', '未知昵称')} · ID {item.get('player_id', '未知')}"
        for item in (matches or friends)[:8]
    )
    raise QueryError(f"请使用完整昵称或 --player-id 明确选择玩家：\n{candidates}")


@timed()
async def fetch_standings_page(client: StrinovaClient, player_id: str, query: Query) -> dict:
    start = (query.index - 1) * query.limit
    api_index, offset = divmod(start, STANDINGS_BATCH_SIZE)
    api_index += 1
    matches: list[dict] = []
    managed_rooms: set[str] = set()
    # One extra record confirms whether the image can offer a next-page command.
    while len(matches) <= query.limit:
        data = await client.standings(
            player_id, api_index, query.game_mode, query.room_mode_type, query.play_mode
        )
        titles = data.get("titles")
        if "player_id" in data and str(data["player_id"]) != player_id:
            raise IdentityMismatch("战绩列表玩家 ID 与请求不一致。")
        if not isinstance(titles, list):
            raise APIError("API 数据结构不符合预期。")
        if isinstance(data.get("bridge_enrichment"), dict):
            managed_rooms.update(str(row.get("room_id")) for row in titles if isinstance(row, dict))
        matches.extend(titles[offset : offset + query.limit + 1 - len(matches)])
        if len(titles) < STANDINGS_BATCH_SIZE or len(matches) > query.limit or api_index >= 1000:
            break
        api_index += 1
        offset = 0
    titles = [row.copy() for row in matches[: query.limit] if isinstance(row, dict)]
    try:
        async with asyncio.timeout(STANDINGS_DETAIL_BUDGET_SECONDS):
            await _fill_missing_roles(client, player_id, titles, managed_rooms=managed_rooms)
    except TimeoutError:
        pass
    return {"titles": titles, "has_more": len(matches) > query.limit}


@timed()
async def _fill_missing_roles(
    client: StrinovaClient,
    player_id: str,
    titles: list[dict],
    *,
    managed_rooms: set[str] | None = None,
) -> None:
    for row in titles:
        # New bridges already budget and cache enrichment; do not repeat or bypass it.
        if str(row.get("room_id")) in (managed_rooms or ()) or row.get("bridge_stats_status") in (
            "detail",
            "detail_cache",
            "deferred",
            "privacy",
        ):
            continue
        # Zero is the list API's missing-detail sentinel, not a character ID.
        if type(row.get("role_id")) is not int or row["role_id"] != 0:
            continue
        try:
            room_id = numeric_id(str(row.get("room_id", "")), "房间 ID")
            async with asyncio.timeout(STANDINGS_DETAIL_TIMEOUT_SECONDS):
                data = await client.match(player_id, room_id)
        except ServiceUnavailable:
            raise
        except APIError as exc:
            if exc.code in (8603, 8609):
                return
            continue
        except (QueryError, TimeoutError):
            continue
        # Only use this room's queried player; other players may have different roles.
        if not isinstance(data, dict) or str(data.get("room_id")) != room_id:
            continue
        details = data.get("standings")
        players = details.get("players") if isinstance(details, dict) else None
        if not isinstance(players, list):
            continue
        own = [
            item
            for item in players
            if isinstance(item, dict) and str(item.get("player_id")) == player_id
        ]
        if len(own) != 1:
            continue
        role_id = own[0].get("final_role_id")
        if type(role_id) is not int or role_id <= 0:
            continue
        row["role_id"] = role_id
        stats = {key: own[0].get(key) for key in ("kill_num", "dead_num", "assists_num", "damage")}
        if all(
            type(value) in (int, float) and math.isfinite(value) and value >= 0
            for value in stats.values()
        ):
            row.update(stats, bridge_stats_status="detail")


@timed()
async def fetch_season_data(client: StrinovaClient, report: Report) -> dict:
    query = report.query
    catalog = await client.seasons()
    rows = catalog.get("seasons")
    if not isinstance(rows, list):
        raise APIError("赛季目录结构不符合预期。")
    report.sections["seasons"] = catalog
    current = catalog.get("current_main_season_id")
    season_id = query.season_id
    if query.season_label is not None:
        label = SEASON_LABEL.fullmatch(query.season_label)
        matches = []
        for row in rows:
            if not isinstance(row, dict) or row.get("season_type") != 1:
                continue
            name = row.get("name")
            code = SEASON_LABEL.match(name) if isinstance(name, str) else None
            if (
                code is not None
                and code["series"].upper() == label["series"].upper()
                and int(code["number"]) == int(label["number"])
                and (label["year"] is None or code["year"] == label["year"])
                and type(row.get("season_id")) is int
                and row["season_id"] > 0
            ):
                matches.append(row["season_id"])
        if not matches:
            raise QueryError(f"未找到赛季「{query.season_label}」，请先查询 #卡丘赛季列表。")
        if len(matches) != 1:
            raise QueryError(
                f"赛季简称「{query.season_label}」对应多个赛季，请用 --season-id 指定。"
            )
        if season_id and season_id != matches[0]:
            raise QueryError("赛季简称与 --season-id 不一致，请只指定一个赛季。")
        season_id = matches[0]
    if query.view == "history" and season_id == 0:
        past = [
            row["season_id"]
            for row in rows
            if isinstance(row, dict)
            and row.get("season_type") == 1
            and row.get("available") is True
            and type(row.get("season_id")) is int
            and type(current) is int
            and 0 < row["season_id"] < current
        ]
        if not past:
            raise APIError("赛季目录未提供可查询的已结束主赛季。")
        season_id = max(past)
    if season_id == 0:
        season_id = current if type(current) is int and current > 0 else 0
    selected = next(
        (
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("season_id") == season_id
            and row.get("season_type") == 1
        ),
        None,
    )
    if season_id and (selected is None or selected.get("available") is not True):
        raise APIError("该主赛季不存在或尚未开放，请先查询赛季列表。")
    is_current = season_id == 0 or (type(current) is int and season_id == current)
    if not is_current and (type(current) is not int or current <= 0):
        raise APIError("当前主赛季未知，暂无法确认历史赛季。")
    if is_current:
        data = await client.career(report.player_id)
        career = data.get("career_info")
        if not isinstance(career, dict):
            raise APIError("生涯资料结构不符合预期。")
        player = career.get("player")
        if isinstance(player, dict) and str(player.get("player_id")) != report.player_id:
            raise IdentityMismatch("生涯玩家 ID 与请求不一致。")
        report.sections["career"] = data
    else:
        data = await client.history(report.player_id, season_id)
        if str(data.get("player_id")) != report.player_id or data.get("season_id") != season_id:
            raise IdentityMismatch("历史赛季的玩家或赛季 ID 与请求不一致。")
        if not isinstance(data.get("battle_infos"), list):
            raise APIError("历史赛季统计结构不符合预期。")
        report.sections["history"] = data
    return {
        "season_id": season_id,
        "name": selected.get("name") if selected else None,
        "is_current": is_current,
    }


@timed()
async def fetch_report(client: StrinovaClient, query: Query) -> Report:
    if query.view in ("seasons", "replay"):
        report = Report(query, "", {"nick": "赛季目录" if query.view == "seasons" else "对局录像"})
        if query.view == "seasons":
            data = await client.seasons()
            if not isinstance(data.get("seasons"), list):
                raise APIError("赛季目录结构不符合预期。")
        else:
            data = await client.replay(query.room_id or "")
        report.sections[query.view] = data
        return report
    profile = await resolve_player(client, query)
    player_id = str(profile.get("player_id", ""))
    report = Report(query=query, player_id=player_id, profile=profile.copy())
    jobs = {"profile": lambda: client.profile(player_id)}
    if query.view in ("overview", "status"):
        jobs["status"] = lambda: client.status(player_id)
    if query.view in ("overview", "profile"):
        jobs["career"] = lambda: client.career(player_id)
    if query.view in ("career", "history"):
        jobs["season"] = lambda: fetch_season_data(client, report)
    if query.view == "card":
        jobs["card"] = lambda: client.card(player_id)
    if query.view in ("overview", "standings"):
        jobs["standings"] = lambda: fetch_standings_page(client, player_id, query)
    if query.view == "match":
        jobs["standings"] = lambda: client.standings(
            player_id, query.index, query.game_mode, query.room_mode_type, query.play_mode
        )
    if query.view == "match":
        jobs["match"] = lambda: client.match(player_id, query.room_id or "")
    labels = {
        "profile": "玩家资料",
        "status": "当前状态",
        "career": "生涯资料",
        "battle": "赛季统计",
        "history": "历史赛季",
        "season": "赛季统计",
        "card": "玩家名片",
        "standings": "近期战绩",
        "match": "单局详情",
    }

    protected = False

    @timed()
    async def load(name: str) -> None:
        nonlocal protected
        if protected and name in ("career", "season", "card", "standings", "match"):
            return
        try:
            data = await jobs[name]()
            field_name, expected_type = {
                "profile": ("friend", dict),
                "status": ("friend", dict),
                "career": ("career_info", dict),
                "card": ("player_info", dict),
                "history": ("battle_infos", list),
                "standings": ("titles", list),
                "match": ("standings", dict),
            }.get(name, (None, dict))
            if field_name is not None and not isinstance(data.get(field_name), expected_type):
                raise APIError("API 数据结构不符合预期。")
            career_info = data.get("career_info")
            identity = {
                "profile": data.get("friend"),
                "status": data.get("friend"),
                "career": career_info.get("player") if isinstance(career_info, dict) else None,
                "card": data.get("player_info"),
                "standings": data,
            }.get(name)
            if isinstance(identity, dict) and "player_id" in identity:
                if str(identity["player_id"]) != player_id:
                    raise IdentityMismatch("玩家资料 ID 与搜索结果不一致。")
            if name == "match" and str(data.get("room_id")) != query.room_id:
                raise IdentityMismatch("单局房间 ID 与请求不一致。")
            report.sections[name] = data
        except (ServiceUnavailable, IdentityMismatch):
            raise
        except APIError as exc:
            protected = protected or exc.code in (8603, 8609)
            report.warnings.append(f"{labels[name]}：{exc}")

    # Profile/status may share one upstream query in the lobby bridge. Avoid overlapping them.
    for name in jobs:
        await load(name)
    friend = report.sections.get("profile", {}).get("friend")
    if isinstance(friend, dict):
        if str(friend.get("player_id")) != player_id:
            raise APIError("玩家资料 ID 与搜索结果不一致。")
        report.profile.update(friend)
    return report


@timed()
async def create_report(query: Query, settings: Settings) -> Report:
    async with StrinovaClient(settings) as client:
        report = await fetch_report(client, query)
        await client.check_health()
        if query.view in ("seasons", "replay"):
            return report
        try:
            report.avatar_data_uri = await load_avatar(
                report.profile.get("icon"), url_template=settings.avatar_url_template
            )
        except AvatarError:
            report.warnings.append("头像资源暂不可用，已使用昵称占位。")
        return report


@timed()
async def check_available(settings: Settings) -> None:
    async with StrinovaClient(settings) as client:
        await client.check_health()
