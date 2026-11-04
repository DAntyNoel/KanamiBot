"""Fresh DAK tracking reads and locally drawn full-lobby result cards.

Public API contracts were checked against EternalReturnLoMu 6f0031e.
The Python implementation and layout are independent; no upstream code is bundled.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import quote

from erbs_plugin import PlayerNotFound, RateLimited, SkiaCardRenderer, UpstreamUnavailable
from erbs_plugin.models import CardPayload

from .group_pet import Snapshot


def number(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def match_time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).timestamp()
    except ValueError:
        return None


class PetSource:
    def __init__(self, client):
        self.client = client
        self._lock = asyncio.Lock()
        self._last_lookup = 0.0

    async def __call__(self, nickname, *, allow_sync_limited=False):
        async with self._lock:
            delay = 2 - (time.monotonic() - self._last_lookup)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_lookup = time.monotonic()
            return await self._fetch(nickname, allow_sync_limited=allow_sync_limited)

    async def for_subscription(self, nickname):
        return await self(nickname, allow_sync_limited=True)

    async def _sync(self, nickname):
        # use_cache=False also disables stale-on-error fallback. Tracking must not use old evidence.
        for attempt in range(3):
            synced = await self.client._request_json(
                f"/api/v0/rpc/player-sync/by-name/{quote(nickname, safe='')}",
                ttl=0,
                use_cache=False,
            )
            if synced.get("retry_after") is not None:
                if attempt == 2:
                    raise RateLimited("DAK player sync rate limited")
                await asyncio.sleep(min(30, max(1, number(synced["retry_after"], 1000) / 1000)))
                continue
            if synced.get("not_found") is True and not synced.get("player_name"):
                raise PlayerNotFound(nickname)
            canonical = synced.get("player_name")
            if synced.get("not_found") or not isinstance(canonical, str) or not canonical.strip():
                raise UpstreamUnavailable("DAK player sync returned no identity")
            return canonical

    async def _fetch(self, nickname, *, allow_sync_limited=False):
        try:
            canonical = await self._sync(nickname)
        except RateLimited as exc:
            if not allow_sync_limited:
                raise
            # Registration needs a verified baseline, not a successful refresh job.
            # A profile 404 here is not proof of account deletion: DAK may not index it yet.
            profile = await self.client._request_json(
                f"/api/v1/players/{quote(nickname, safe='')}/profile",
                ttl=0,
                use_cache=False,
            )
            player = profile.get("player")
            canonical = player.get("name") if isinstance(player, dict) else None
            if not isinstance(canonical, str) or not canonical.strip():
                raise UpstreamUnavailable("DAK player profile returned no identity") from exc
        data = await self.client._request_json(
            f"/api/v1/players/{quote(canonical, safe='')}/matches",
            params={"matchingMode": "ALL", "teamMode": "ALL", "page": 1},
            ttl=0,
            use_cache=False,
        )
        rows = data.get("matches")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise UpstreamUnavailable("DAK matches response malformed")
        if any(number(row.get("gameId")) <= 0 for row in rows):
            raise UpstreamUnavailable("DAK matches response has invalid IDs")
        if not rows:
            return Snapshot(canonical)
        latest = max(rows, key=lambda row: number(row["gameId"]))
        if str(latest.get("nickname", "")).casefold() != canonical.casefold():
            raise UpstreamUnavailable("DAK match identity mismatch")
        return Snapshot(
            canonical,
            number(latest["gameId"]),
            match_time(latest.get("startDtm")),
            0 if number(latest.get("matchingMode")) == 6 else number(latest.get("seasonId")),
        )


async def detail_card(client, nickname, season, game_id):
    data = await client._request_json(
        f"/api/v1/players/{quote(nickname, safe='')}/matches/{season}/{game_id}",
        ttl=0,
        use_cache=False,
    )
    return await build_detail_card(client, data, nickname, game_id)


async def build_detail_card(client, data, nickname, game_id):
    rows = data.get("matches")
    if not isinstance(rows, list) or not rows or len(rows) > 64:
        raise UpstreamUnavailable("对局结算数据暂不可用")
    if any(not isinstance(r, dict) or number(r.get("gameId")) != game_id for r in rows):
        raise UpstreamUnavailable("对局结算数据与请求不匹配")
    if not any(str(r.get("nickname", "")).casefold() == nickname.casefold() for r in rows):
        raise UpstreamUnavailable("对局中未找到指定玩家")
    tables = await asyncio.gather(
        *(
            client.metadata(name)
            for name in (
                "characters",
                "items",
                "trait-skills",
                "tactical-skills",
            )
        )
    )
    maps = []
    for table, key in zip(
        tables, ("characters", "items", "traitSkills", "tacticalSkills"), strict=True
    ):
        maps.append({number(r.get("id") or r.get("code")): r for r in table.get(key, [])})
    characters, items, traits, tactics = maps

    def asset(table, code, prefix):
        code = number(code)
        entry = table.get(code, {})
        return {
            "name": entry.get("name") or (f"{prefix} {code}" if code else "—"),
            "imageUrl": entry.get("imageUrl"),
        }

    def shown(row, key):
        return row[key] if row.get(key) is not None else "—"

    teams = defaultdict(list)
    for row in rows:
        character = asset(characters, row.get("characterNum"), "实验体")
        equipment = row.get("equipment") or []
        if isinstance(equipment, dict):
            equipment = list(equipment.values())
        perks = [
            row.get("traitFirstCore"),
            *(row.get("traitFirstSub") or []),
            *(row.get("traitSecondSub") or []),
        ]
        tactical = tactics.get(number(row.get("tacticalSkillGroup")), {})
        player = {
            "nickname": str(row.get("nickname") or "—"),
            "focused": str(row.get("nickname", "")).casefold() == nickname.casefold(),
            "character": character,
            "rank": number(row.get("gameRank"), 999),
            "level": shown(row, "characterLevel"),
            "kda": " / ".join(
                str(shown(row, k)) for k in ("playerKill", "playerDeaths", "playerAssistant")
            ),
            "damage": shown(row, "damageToPlayer"),
            "taken": shown(row, "damageFromPlayer"),
            "rp": f"{shown(row, 'mmrAfter')} ({shown(row, 'mmrGain')})",
            "equipment": [asset(items, code, "装备") for code in equipment[:5]],
            "traits": [asset(traits, code, "潜能") for code in perks if code],
            "tactical": tactical or {"name": f"战术 {row.get('tacticalSkillGroup', '—')}"},
        }
        # Missing team IDs must never collapse unrelated players into one team.
        team = row.get("teamNumber")
        teams[str(team) if team is not None else f"未知-{len(teams) + 1}"].append(player)
    sections = tuple(
        {
            "title": f"第 {rank if (rank := min(p['rank'] for p in players)) < 999 else '—'} 名"
            f" · 队伍 {team}",
            "items": players,
        }
        for team, players in sorted(teams.items(), key=lambda t: min(p["rank"] for p in t[1]))
    )
    start = match_time(rows[0].get("startDtm"))
    when = (
        datetime.fromtimestamp(start, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        if start
        else "时间未知"
    )
    return CardPayload(
        kind="match-detail",
        title=f"对局结算 · {game_id}",
        subtitle=f"{nickname} · {when} · {rows[0].get('serverName', '—')}",
        sections=sections,
        footer={"source": "DAK.GG"},
    )


class MatchDetailRenderer(SkiaCardRenderer):
    """Use the pinned renderer's font fallback and manifest-only image loading."""

    def _render(self, payload):
        self._initialize()
        height = 160 + sum(48 + 148 * len(s["items"]) for s in payload.sections)
        surface = self._skia.Surface.MakeRasterN32Premul(1100, height)
        canvas = surface.getCanvas()
        canvas.clear(0xFFF3F5F8)
        self._rect(canvas, 0, 0, 1100, 110, 0xFF202B40, 0)
        self._label(canvas, payload.title, 28, 44, size=30, color=0xFFFFFFFF, bold=True)
        self._label(canvas, payload.subtitle, 28, 81, size=19, color=0xFFCAD4E6, width=1044)
        colors = (
            0xFF4266D5,
            0xFF169779,
            0xFF9963B8,
            0xFFCF8528,
            0xFF318CB0,
            0xFFB85C85,
            0xFF6A8C32,
            0xFF9C6D4C,
        )
        y = 126
        for index, section in enumerate(payload.sections):
            color = colors[index % len(colors)]
            self._label(canvas, section["title"], 28, y + 24, size=22, color=color, bold=True)
            y += 40
            for p in section["items"]:
                self._rect(canvas, 24, y, 1052, 140, 0xFFFFFFFF)
                self._rect(canvas, 24, y, 5, 140, color, 0)
                self._image(canvas, p["character"]["imageUrl"], 40, y + 14, 68, 68)
                name_color = 0xFFD83C4B if p["focused"] else 0xFF202733
                self._label(
                    canvas,
                    p["nickname"],
                    124,
                    y + 30,
                    size=23,
                    color=name_color,
                    bold=True,
                    width=365,
                )
                self._label(
                    canvas,
                    f"{p['character']['name']} · Lv.{p['level']}",
                    124,
                    y + 58,
                    size=18,
                    width=365,
                )
                self._label(canvas, f"K / D / A  {p['kda']}", 510, y + 30, size=21)
                self._label(
                    canvas,
                    f"伤害 {p['damage']}   承伤 {p['taken']}   RP {p['rp']}",
                    510,
                    y + 58,
                    size=17,
                    width=540,
                )
                for slot, item in enumerate(p["equipment"]):
                    x = 124 + slot * 72
                    self._image(canvas, item["imageUrl"], x, y + 69, 48, 42)
                    self._label(canvas, item["name"], x - 5, y + 130, size=12, width=69)
                skills = [p["tactical"], *p["traits"]]
                for slot, skill in enumerate(skills):
                    x = 510 + slot * 89
                    self._image(canvas, skill.get("imageUrl"), x, y + 69, 38, 38)
                    self._label(canvas, skill.get("name"), x - 4, y + 130, size=12, width=86)
                y += 148
            y += 8
        self._label(canvas, "DAK.GG · 对局结算", 28, height - 14, size=16, color=0xFF6D7889)
        return bytes(surface.makeImageSnapshot().encodeToData())
