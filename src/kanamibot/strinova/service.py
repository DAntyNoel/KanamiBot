from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .avatar import AvatarError, load_avatar
from .client import APIError, ServiceUnavailable, Settings, StrinovaClient
from .query import Query, QueryError


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
    if query.view in ("overview", "standings", "match"):
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
