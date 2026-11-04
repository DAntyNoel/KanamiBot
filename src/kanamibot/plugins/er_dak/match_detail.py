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
from erbs_plugin.rendering.dak import DARK, INK, LINE, MUTED, WHITE, Drawing, num

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
                "masteries",
            )
        )
    )
    maps = []
    for table, key in zip(
        tables, ("characters", "items", "traitSkills", "tacticalSkills", "masteries"), strict=True
    ):
        maps.append({number(r.get("id") or r.get("code")): r for r in table.get(key, [])})
    characters, items, traits, tactics, weapons = maps
    trait_groups = {r["key"]: r for r in tables[2].get("traitSkillGroups", [])}

    def asset(table, code, prefix):
        code = number(code)
        entry = table.get(code, {})
        return {
            "name": entry.get("name") or (f"{prefix} {code}" if code else "—"),
            "imageUrl": entry.get("imageUrl") or entry.get("iconUrl"),
            "grade": entry.get("grade"),
        }

    def shown(row, key):
        return row[key] if row.get(key) is not None else "—"

    teams = defaultdict(list)
    for row in rows:
        character = asset(characters, row.get("characterNum"), "实验体")
        skin = next(
            (
                s
                for s in characters.get(number(row.get("characterNum")), {}).get("skins", [])
                if s.get("id") == row.get("skinCode")
            ),
            {},
        )
        if skin.get("imageUrl"):
            character["imageUrl"] = skin["imageUrl"].replace("CharResult_", "CharProfile_")
        equipment = row.get("equipment") or []
        if isinstance(equipment, dict):
            equipment = list(equipment.values())
        perks = [
            row.get("traitFirstCore"),
            *(row.get("traitFirstSub") or []),
            *(row.get("traitSecondSub") or []),
        ]
        tactical = tactics.get(number(row.get("tacticalSkillGroup")), {})
        secondary = next(iter(row.get("traitSecondSub") or []), None)
        secondary_group = trait_groups.get(traits.get(number(secondary), {}).get("group"), {})
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
            "monsterDamage": shown(row, "damageToMonster"),
            "credits": shown(row, "totalGainVFCredit"),
            "teamKills": shown(row, "teamKill"),
            "taken": shown(row, "damageFromPlayer"),
            "rp": f"{shown(row, 'mmrAfter')} ({shown(row, 'mmrGain')})",
            "rpAfter": row.get("mmrAfter"),
            "weapon": asset(weapons, row.get("bestWeapon"), "武器"),
            "coreTrait": asset(traits, row.get("traitFirstCore"), "潜能"),
            "secondaryGroup": secondary_group,
            "tacticalLevel": shown(row, "tacticalSkillLevel"),
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
        footer={
            "source": "DAK.GG",
            "averageRp": (data.get("matchTier") or {}).get("mmr"),
            "mode": {2: "匹配", 3: "排位", 6: "钴协议"}.get(rows[0].get("matchingMode"), "对局"),
        },
    )


class MatchDetailRenderer(SkiaCardRenderer):
    """Use the pinned renderer's font fallback and manifest-only image loading."""

    def _render(self, payload):
        self._initialize()
        row_height, rank_width = 96, 74
        top = 162
        height = top + sum(len(section["items"]) * row_height for section in payload.sections)
        d = Drawing(self, 1100, height)
        d.rect(0, 0, 1100, 48, WHITE)
        d.text(payload.subtitle, 20, 30, 16, MUTED, width=890)
        d.text(payload.title.split(" · ")[-1], 934, 30, 16, MUTED, width=150)
        d.rect(0, 48, 1100, 54, 0xFFEE818B)
        average = payload.footer.get("averageRp")
        average = num(average, 0) if isinstance(average, (int, float)) and average > 0 else "—"
        d.center(
            f"对局平均 RP {average}  ·  {payload.footer.get('mode', '对局')}",
            0,
            82,
            1100,
            18,
            WHITE,
            True,
        )
        d.rect(0, 102, 1100, 60, 0xFFE8EFF1)
        d.center("#", 0, 138, rank_width, 15, MUTED)
        d.text("玩家", 90, 138, 16, MUTED)
        for label, x, width in (
            ("TK / K / D / A", 494, 146),
            ("伤害", 640, 95),
            ("荣誉值", 850, 90),
            ("物品出装", 940, 160),
        ):
            d.center(label, x, 138, width, 15, MUTED)
        d.center("对野生动物", 735, 127, 115, 14, MUTED)
        d.center("造成的伤害", 735, 146, 115, 14, MUTED)
        players = [p for section in payload.sections for p in section["items"]]
        max_damage = max((number(p["damage"]) for p in players), default=1) or 1
        max_monster = max((number(p["monsterDamage"]) for p in players), default=1) or 1

        def metric(value, maximum, x, y, width, color):
            d.rect(x + (width - 58) / 2, y + 29, 58, 11, LINE, 6)
            if isinstance(value, (int, float)) and value > 0:
                d.rect(
                    x + (width - 58) / 2, y + 29, max(4, 58 * min(1, value / maximum)), 11, color, 6
                )
            d.center(num(value, 0), x, y + 61, width, 17, MUTED)

        y = top
        for section in payload.sections:
            team_height = len(section["items"]) * row_height
            highlighted = any(p["focused"] for p in section["items"])
            d.rect(0, y, 1100, team_height, 0xFFFFFBEA if highlighted else WHITE)
            d.rect(0, y, rank_width, team_height, 0xFFFFF9E5 if highlighted else 0xFFFAFAFA)
            rank = min(p["rank"] for p in section["items"])
            d.center(
                f"#{rank}" if rank < 999 else "—", 0, y + team_height / 2 + 6, rank_width, 17, INK
            )
            for p in section["items"]:
                d.image(p["character"]["imageUrl"], 92, y + 18, 60, 60, circle=True)
                d.rect(131, y + 58, 28, 23, WHITE, 11)
                d.center(p["level"], 131, y + 75, 28, 14)
                icons = [p["weapon"], p["coreTrait"], p["tactical"], p["secondaryGroup"]]
                for index, icon in enumerate(icons):
                    d.image(
                        icon.get("imageUrl"),
                        169 + index % 2 * 30,
                        y + 21 + index // 2 * 30,
                        27,
                        27,
                        circle=index % 2 == 1,
                        bg=0xFF45474B if index % 2 == 0 else WHITE,
                    )
                d.rect(181, y + 66, 15, 16, DARK, 3)
                d.center(p["tacticalLevel"], 181, y + 79, 15, 11, WHITE)
                d.text(
                    p["nickname"],
                    244,
                    y + 39,
                    18,
                    0xFFD85160 if p["focused"] else 0xFF153E68,
                    p["focused"],
                    242,
                )
                rp = num(p["rpAfter"], 0)
                d.text(f"{rp} RP", 244, y + 65, 16, MUTED, width=240)
                d.center(f"{p['teamKills']} / {p['kda']}", 494, y + 53, 146, 17, INK, True)
                metric(p["damage"], max_damage, 640, y, 95, 0xFFCD9474)
                metric(p["monsterDamage"], max_monster, 735, y, 115, 0xFF789638)
                d.center(num(p["credits"], 0), 850, y + 53, 90, 17, MUTED)
                for slot in range(5):
                    x = 950 + (slot % 3 if slot < 3 else slot - 3 + 0.5) * 47
                    ey = y + 16 + (0 if slot < 3 else 34)
                    d.rect(x, ey, 43, 29, LINE, 4)
                    if slot < len(p["equipment"]):
                        d.item(p["equipment"][slot], x, ey, 43, 29)
                y += row_height
            d.line(0, y - 1, 1100)
        return d.finish()
