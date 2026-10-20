from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .avatar import AvatarError, load_avatar
from .client import APIError, ServiceUnavailable, Settings, StrinovaClient
from .query import Query, QueryError, numeric_id

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


async def fetch_standings_page(client: StrinovaClient, player_id: str, query: Query) -> dict:
    start = (query.index - 1) * query.limit
    api_index, offset = divmod(start, STANDINGS_BATCH_SIZE)
    api_index += 1
    matches: list[dict] = []
    # One extra record confirms whether the image can offer a next-page command.
    while len(matches) <= query.limit:
        data = await client.standings(
            player_id, api_index, query.game_mode, query.room_mode_type, query.play_mode
        )
        titles = data.get("titles")
        if not isinstance(titles, list):
            raise APIError("API 数据结构不符合预期。")
        matches.extend(titles[offset : offset + query.limit + 1 - len(matches)])
        if len(titles) < STANDINGS_BATCH_SIZE or len(matches) > query.limit or api_index >= 1000:
            break
        api_index += 1
        offset = 0
    titles = [row.copy() for row in matches[: query.limit] if isinstance(row, dict)]
    try:
        async with asyncio.timeout(STANDINGS_DETAIL_BUDGET_SECONDS):
            await _fill_missing_roles(client, player_id, titles)
    except TimeoutError:
        pass
    return {"titles": titles, "has_more": len(matches) > query.limit}


async def _fill_missing_roles(client: StrinovaClient, player_id: str, titles: list[dict]) -> None:
    for row in titles:
        # Zero is the list API's missing-detail sentinel, not a character ID.
        if type(row.get("role_id")) is not int or row["role_id"] != 0:
            continue
        try:
            room_id = numeric_id(str(row.get("room_id", "")), "房间 ID")
            async with asyncio.timeout(STANDINGS_DETAIL_TIMEOUT_SECONDS):
                data = await client.match(player_id, room_id)
        except ServiceUnavailable:
            raise
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


async def fetch_report(client: StrinovaClient, query: Query) -> Report:
    profile = await resolve_player(client, query)
    player_id = str(profile.get("player_id", ""))
    report = Report(query=query, player_id=player_id, profile=profile.copy())
    jobs = {"profile": lambda: client.profile(player_id)}
    if query.view in ("overview", "status"):
        jobs["status"] = lambda: client.status(player_id)
    if query.view in ("overview", "career", "profile"):
        jobs["career"] = lambda: client.career(player_id)
    if query.view == "career":
        jobs["battle"] = lambda: client.battle_data(player_id, query.season_id, query.season_type)
    if query.view == "history":
        jobs["history"] = lambda: client.history(player_id, query.season_id)
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
        "standings": "近期战绩",
        "match": "单局详情",
    }

    async def load(name: str) -> None:
        try:
            data = await jobs[name]()
            field_name, expected_type = {
                "profile": ("friend", dict),
                "status": ("friend", dict),
                "career": ("career_info", dict),
                "history": ("battle_infos", list),
                "standings": ("titles", list),
                "match": ("standings", dict),
            }.get(name, (None, dict))
            if field_name is not None and not isinstance(data.get(field_name), expected_type):
                raise APIError("API 数据结构不符合预期。")
            report.sections[name] = data
        except ServiceUnavailable:
            raise
        except APIError as exc:
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


async def create_report(query: Query, settings: Settings) -> Report:
    async with StrinovaClient(settings) as client:
        report = await fetch_report(client, query)
        await client.check_health()
        try:
            report.avatar_data_uri = await load_avatar(
                report.profile.get("icon"), url_template=settings.avatar_url_template
            )
        except AvatarError:
            report.warnings.append("头像资源暂不可用，已使用昵称占位。")
        return report


async def check_available(settings: Settings) -> None:
    async with StrinovaClient(settings) as client:
        await client.check_health()
