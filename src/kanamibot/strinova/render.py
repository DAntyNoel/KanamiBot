from __future__ import annotations

import asyncio
import math
import shlex
import sys
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

from .client import Settings
from .query import QueryError
from .service import Report

TEMPLATE_DIR = Path(__file__).with_name("templates")
SHANGHAI = timezone(timedelta(hours=8))
ROLES = {
    101: "米雪儿·李",
    105: "奥黛丽",
    107: "玛德蕾娜",
    108: "信",
    109: "令",
    110: "白墨",
    112: "绯莎",
    115: "芙拉薇娅",
    119: "艾卡",
    120: "珐格兰丝",
    121: "忧雾",
    122: "玛拉",
    123: "蕾欧娜",
    124: "心夏",
    125: "千代",
    126: "诺诺",
    128: "拉薇",
    130: "汐",
    131: "伊薇特",
    132: "明",
    133: "梅瑞狄斯",
    137: "香奈美",
    146: "星绘",
    205: "加拉蒂亚",
    220: "哨兵机器人",
    301: "爆裂魔怪",
    302: "血荆皇女",
    304: "刺镰魔怪",
    305: "冥荆皇女",
    306: "莉莉丝",
}
RANKS = [
    "未定级",
    "至纯物质III",
    "至纯物质II",
    "至纯物质I",
    "升格分子III",
    "升格分子II",
    "升格分子I",
    "虚空原子IV",
    "虚空原子III",
    "虚空原子II",
    "虚空原子I",
    "聚变质子IV",
    "聚变质子III",
    "聚变质子II",
    "聚变质子I",
    "自由中子IV",
    "自由中子III",
    "自由中子II",
    "自由中子I",
    "束缚电子V",
    "束缚电子IV",
    "束缚电子III",
    "束缚电子II",
    "束缚电子I",
    "禁闭夸克V",
    "禁闭夸克IV",
    "禁闭夸克III",
    "禁闭夸克II",
    "禁闭夸克I",
    "无限超弦",
    "奇点",
]
VIEW_NAMES = {
    "overview": "玩家概览",
    "profile": "引航者资料",
    "status": "当前状态",
    "career": "生涯数据",
    "history": "历史赛季",
    "standings": "对局战绩",
    "match": "单局详情",
}


def number(value: Any, decimals: int = 0) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "—"
    return f"{value:,.{decimals}f}"


def percent(value: Any) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        return "—"
    return f"{value * 100:.1f}%"


def timestamp(value: Any) -> str:
    from datetime import datetime

    if not isinstance(value, (int, float)) or value <= 0:
        return "—"
    try:
        return datetime.fromtimestamp(value, SHANGHAI).strftime("%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return "—"


def objects(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def role_name(value: Any) -> str:
    if not isinstance(value, int):
        return "角色未提供"
    return ROLES.get(value, f"角色 #{value}")


def dictionary(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def rank_name(value: Any) -> str:
    return RANKS[value] if isinstance(value, int) and 0 <= value < len(RANKS) else "—"


def mode_name(row: dict) -> str:
    # The raw bridge has no dictionary. Preserve unknown enum values rather than guessing names.
    return " / ".join(
        f"{label} {row[key]}"
        for key, label in (("game_mode", "模式"), ("room_mode_type", "房间"), ("play_mode", "玩法"))
        if key in row
    )


def match_result(row: dict) -> tuple[str, str]:
    winners = row.get("winner_team")
    if isinstance(winners, list) and row.get("my_team") is not None:
        if not winners:
            return "平局", "draw"
        return ("胜利", "win") if row["my_team"] in winners else ("失败", "loss")
    return "未知", "unknown"


def match_row(row: dict) -> dict:
    result, tone = match_result(row)
    scores = row.get("scores")
    score = " : ".join(str(item) for item in scores) if isinstance(scores, list) else "—"
    current, old = row.get("divison_score"), row.get("old_division_score")
    change = current - old if isinstance(current, int) and isinstance(old, int) else None
    stats_available = row.get("bridge_stats_status") not in (
        "pending",
        "missing",
        "unavailable",
        "failed",
        "timeout",
    )
    return {
        "result": result,
        "tone": tone,
        "score": score,
        "time": timestamp(row.get("time")),
        "duration": f"{row['fight_time'] // 60}分{row['fight_time'] % 60:02d}秒"
        if isinstance(row.get("fight_time"), int)
        else "—",
        "map": f"地图 #{row['map_id']}" if row.get("map_id") else "地图未提供",
        "mode": mode_name(row),
        "role": role_name(row.get("role_id")),
        "kda": " / ".join(
            number(row.get(key)) if stats_available else "—"
            for key in ("kill_num", "dead_num", "assists_num")
        ),
        "damage": number(row.get("damage")) if stats_available else "—",
        "change": f"{change:+d}" if change is not None else "—",
        "positive": change is not None and change > 0,
        "room_id": str(row.get("room_id", "—")),
    }


def report_context(report: Report) -> dict:
    query, profile = report.query, report.profile
    career = report.sections.get("career", {}).get("career_info", {})
    career = career if isinstance(career, dict) else {}
    stats = (
        report.sections.get("history")
        if query.view == "history"
        else (report.sections.get("battle") or career)
    )
    stats = stats or {}
    total, wins = profile.get("total_games"), profile.get("win_games")
    win_rate = (
        wins / total if isinstance(total, int) and total > 0 and isinstance(wins, int) else None
    )
    details = report.sections.get("match", {}).get("standings", {})
    details = details if isinstance(details, dict) else {}
    players = objects(details.get("players"))
    teams: dict[str, list[dict]] = {}
    for player in players:
        team = str(player.get("team_id", "未知"))
        teams.setdefault(team, []).append(
            {
                "nick": player.get("nick", "未知昵称"),
                "self": str(player.get("player_id")) == report.player_id,
                "role": role_name(player.get("final_role_id")),
                "kda": " / ".join(
                    number(player.get(key)) for key in ("kill_num", "dead_num", "assists_num")
                ),
                "damage": number(player.get("damage")),
                "score": number(player.get("scores")),
                "award": "MVP" if player.get("mvp") else "SVP" if player.get("smvp") else "",
            }
        )
    titles = objects(report.sections.get("standings", {}).get("titles"))
    selected_match = next(
        (item for item in titles if str(item.get("room_id")) == query.room_id), None
    )
    status = report.sections.get("status", {}).get("friend", profile)
    status = status if isinstance(status, dict) else profile
    roles = sorted(
        objects(stats.get("role_employs")), key=lambda row: row.get("count", 0), reverse=True
    )[:8]
    target = query.target
    if query.by_id:
        target = report.player_id
    elif target.isdecimal() or target.startswith("-"):
        target = f"--nick {shlex.quote(target)}"
    elif any(char.isspace() or char in "'\"\\" for char in target):
        target = shlex.quote(target)
    command = f"#卡丘战绩 {target}"
    return {
        "title": VIEW_NAMES[query.view],
        "view": query.view,
        "name": str(profile.get("nick") or query.target),
        "player_id": report.player_id,
        "initial": str(profile.get("nick") or query.target)[:1],
        "avatar_data_uri": report.avatar_data_uri,
        "switch_command": f"{command} 对局战绩",
        "overview_command": f"{command} 玩家概览",
        "level": number(profile.get("level")),
        "rank": rank_name(profile.get("rank")),
        "grade": number(profile.get("grade_score")),
        "status": f"状态码 {status['online_status']}"
        if "online_status" in status
        else "状态未提供",
        "updated": report.fetched_at.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": [
            ("公开对局数", number(total)),
            ("胜率", percent(win_rate)),
            ("胜利场次", number(wins)),
            ("MVP", number(profile.get("mvp_count"))),
        ],
        "facts": [
            ("排位段位", rank_name(profile.get("rank"))),
            ("段位积分", number(profile.get("grade_score"))),
            ("历史最高段位", rank_name(career.get("his_top_rank"))),
            ("最高段位赛季", number(career.get("his_top_rank_season"))),
            ("战队", dictionary(career.get("clan_show_info")).get("clan_name") or "—"),
            ("获赞", number(dictionary(career.get("player")).get("laud"))),
        ],
        "freq_roles": [role_name(item) for item in (profile.get("freq_roles") or [])],
        "modes": objects(stats.get("battle_infos")),
        "roles": roles,
        "matches": [match_row(item) for item in titles[: query.limit]],
        "match_summary": match_row(selected_match) if selected_match else None,
        "match_teams": teams,
        "room_id": query.room_id,
        "match_available": "match" in report.sections,
        "history_available": "history" in report.sections,
        "standings_available": "standings" in report.sections,
        "career_available": bool(career) or "battle" in report.sections,
        "history": report.sections.get("history", {}),
        "last_login": timestamp(status.get("time")),
        "last_logout": timestamp(status.get("logout_time")),
        "battle_start": timestamp(status.get("battle_start_time")),
        "battle_scores": status.get("battle_scores") or "—",
        "season_id": query.season_id,
        "season_type": query.season_type,
        "index": query.index,
        "filter": f"模式 {query.game_mode} / 房间 {query.room_mode_type} / 玩法 {query.play_mode}",
        "warnings": report.warnings,
    }


def render_html(report: Report) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError as exc:
        raise QueryError("缺少 Jinja2，请先安装战绩渲染依赖。") from exc
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(["html"]))
    env.globals.update(
        number=number,
        percent=percent,
        role_name=role_name,
        rank_name=rank_name,
        mode_name=mode_name,
    )
    return env.get_template("report.html").render(
        **report_context(report), css=(TEMPLATE_DIR / "report.css").read_text(encoding="utf-8")
    )


async def _screenshot(html: str, settings: Settings) -> bytes:
    try:
        from playwright.async_api import Error, async_playwright
    except ImportError as exc:
        raise QueryError(
            "缺少 Playwright，请安装依赖并运行 python -m playwright install chromium。"
        ) from exc
    try:
        async with async_playwright() as playwright:
            options: dict[str, Any] = {"headless": True}
            if settings.browser_executable:
                options["executable_path"] = settings.browser_executable
            elif settings.browser_channel:
                options["channel"] = settings.browser_channel
            browser = await playwright.chromium.launch(**options)
            try:
                page = await browser.new_page(
                    viewport={"width": 1040, "height": 800},
                    device_scale_factor=1.5,
                    java_script_enabled=False,
                )
                # Templates are self-contained: rendering must not depend on the reference site.
                await page.route("**/*", lambda route: route.abort())
                await page.set_content(html, wait_until="load")
                return await page.locator("#report").screenshot(type="png", timeout=20000)
            finally:
                await browser.close()
    except Error as exc:
        raise QueryError(
            "图片渲染失败。请运行 python -m playwright install chromium，"
            "或设置 STRINOVA_BROWSER_CHANNEL=chrome / msedge。"
        ) from exc


def _render_in_thread(html: str, settings: Settings) -> bytes:
    # Uvicorn may use a Selector loop on Windows; Playwright needs subprocess support.
    factory = asyncio.ProactorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(_screenshot(html, settings))


async def render_png(report: Report, settings: Settings) -> bytes:
    return await asyncio.to_thread(_render_in_thread, render_html(report), settings)
