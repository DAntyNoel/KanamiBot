from __future__ import annotations

import argparse
import re
import shlex
from dataclasses import dataclass

VIEWS = (
    "overview",
    "profile",
    "status",
    "career",
    "history",
    "standings",
    "match",
    "card",
    "seasons",
    "replay",
)
VIEW_LABELS = {
    "玩家概览": "overview",
    "引航者资料": "profile",
    "玩家资料": "profile",
    "当前状态": "status",
    "生涯数据": "career",
    "历史赛季": "history",
    "对局战绩": "standings",
    "单局详情": "match",
    "玩家名片": "card",
    "赛季列表": "seasons",
    "对局录像": "replay",
}
GAME_MODES = {
    "无限团竞": 1,
    "一般爆破": 2,
    "自定义": 3,
    "赛事对战": 4,
    "排位爆破": 5,
    "芯核争夺": 6,
    "团队乱斗": 7,
    "极限推进": 9,
    "喷漆潜行": 10,
    "晶矿抢攻": 11,
    "晶源感染": 13,
    "晶能冲突": 14,
    "超弦爆破": 17,
    "大头乱斗": 18,
    "多人建造": 20,
    "推塔射击": 21,
    "炸弹派对": 22,
    "弦区争夺": 25,
    "极限刀战": 28,
    "枪王乱斗": 30,
    "排位团竞": 31,
    "排位推进": 32,
    "极限推进2": 33,
    "晶源追击": 35,
    "团队跑酷": 39,
    "水球狂欢": 40,
}
GAME_MODE_NAMES = {code: name for name, code in GAME_MODES.items()}
GAME_MODE_ALIASES = {"生化": "晶源感染", "排位": "排位爆破"}


class QueryError(ValueError):
    """A query that can be explained directly to the user."""


class QueryParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise QueryError(message)


def add_query_arguments(
    parser: argparse.ArgumentParser,
    *,
    view: str = "overview",
    allowed_views: tuple[str, ...] = VIEWS,
) -> None:
    parser.add_argument("nickname", nargs="*", help="玩家昵称；纯数字默认按 ID 查询")
    identity = parser.add_mutually_exclusive_group()
    identity.add_argument("--player-id", help="明确按 player_id 查询")
    identity.add_argument("--nick", help="明确按昵称查询，包括纯数字昵称")
    parser.set_defaults(default_view=view, allowed_views=allowed_views)
    parser.add_argument("--view", choices=allowed_views, help="也可在玩家名后追加中文界面名称")
    parser.add_argument("--season-id", type=int, default=0)
    parser.add_argument("--season-type", type=int, default=1)
    parser.add_argument("--index", "--page", type=int, help="最近战绩的图片页码，从 1 开始")
    parser.add_argument("--game-mode", type=int, help="模式编号；0 表示全部模式")
    parser.add_argument("--mode", help="最近战绩的中文模式名、简称或唯一前缀")
    parser.add_argument("--room-mode-type", type=int, default=0)
    parser.add_argument("--play-mode", type=int, default=0)
    parser.add_argument("--room-id", help="单局房间 ID，必须保留完整数字")
    parser.add_argument("--limit", type=int, default=8, help="图片最多展示 1–20 条战绩")


def numeric_id(value: str, label: str, maximum: int = 2**64 - 1) -> str:
    if not value.isascii() or not value.isdecimal() or not 0 < int(value) <= maximum:
        raise QueryError(f"{label} 必须为有效的正整数。")
    return str(int(value))


def resolve_game_mode(value: str) -> int:
    name = GAME_MODE_ALIASES.get(value, value)
    if name in GAME_MODES:
        return GAME_MODES[name]
    matches = [item for item in GAME_MODES if item.startswith(name)] if name else []
    if len(matches) == 1:
        return GAME_MODES[matches[0]]
    if matches:
        raise QueryError(f"模式「{value}」可指：{'、'.join(matches)}，请写得更完整。")
    raise QueryError(f"未知战绩模式「{value}」，请使用完整模式名或 --game-mode 编号。")


@dataclass(frozen=True)
class Query:
    target: str
    by_id: bool = False
    view: str = "overview"
    season_id: int = 0
    season_type: int = 1
    index: int = 1
    game_mode: int = 0
    room_mode_type: int = 0
    play_mode: int = 0
    room_id: str | None = None
    limit: int = 8

    def __post_init__(self) -> None:
        if (
            (not self.target.strip() and self.view not in ("seasons", "replay"))
            or len(self.target) > 64
            or any(ord(char) < 32 for char in self.target)
        ):
            raise QueryError("请输入 1–64 个字符的玩家昵称或 ID。")
        if self.by_id:
            numeric_id(self.target, "玩家 ID")
        if self.view not in VIEWS:
            raise QueryError("不支持的查询视图。")
        if self.index < 1 or self.index > 1000 or not 1 <= self.limit <= 20:
            raise QueryError("页码必须为 1–1000，展示条数必须为 1–20。")
        if any(
            not 0 <= value <= 2**32 - 1
            for value in (
                self.season_id,
                self.season_type,
                self.game_mode,
                self.room_mode_type,
                self.play_mode,
            )
        ):
            raise QueryError("赛季和模式参数必须为非负整数。")
        if self.room_id is not None:
            object.__setattr__(self, "room_id", numeric_id(self.room_id, "房间 ID"))
        if self.view in ("match", "replay") and self.room_id is None:
            raise QueryError("单局或录像查询需要 --room-id {房间ID}。")
        if self.season_id and self.view not in ("career", "history"):
            raise QueryError("--season-id 仅适用于生涯数据和历史赛季；对局列表不支持赛季筛选。")
        if self.season_type != 1 and self.view in ("career", "history"):
            raise QueryError("当前 PC 接口仅支持主赛季（--season-type 1）。")
        if self.game_mode and self.view not in ("overview", "standings", "career", "history"):
            raise QueryError("模式筛选仅适用于玩家概览、对局战绩、生涯数据和历史赛季。")


def query_from_args(args: argparse.Namespace) -> Query:
    nickname = list(args.nickname)
    view = args.view
    mode = args.mode
    index = args.index
    while nickname and (len(nickname) > 1 or args.player_id is not None or args.nick is not None):
        suffix = nickname[-1]
        page = re.fullmatch(r"第(\d+)页|(\d+)", suffix)
        if page:
            selected = int(page.group(1) or page.group(2))
            if index is not None and index != selected:
                raise QueryError("页码不一致，请只指定一个页码。")
            index = selected
            nickname.pop()
        elif suffix in VIEW_LABELS:
            selected_view = VIEW_LABELS[nickname.pop()]
            if view is not None and view != selected_view:
                raise QueryError("中文界面名称与 --view 不一致，请只指定一种界面。")
            view = selected_view
        elif suffix in GAME_MODE_ALIASES or any(name.startswith(suffix) for name in GAME_MODES):
            if mode is not None:
                raise QueryError("战绩模式请只指定一次。")
            mode = nickname.pop()
        else:
            break
    resolved_view = view or args.default_view
    if resolved_view not in args.allowed_views:
        raise QueryError("不支持的查询视图。")
    if mode is not None and resolved_view not in ("overview", "standings", "career", "history"):
        raise QueryError("模式筛选仅适用于玩家概览、对局战绩、生涯数据和历史赛季。")
    game_mode = args.game_mode if args.game_mode is not None else 0
    if mode is not None:
        selected_mode = resolve_game_mode(mode)
        if args.game_mode is not None and args.game_mode != selected_mode:
            raise QueryError("--mode 与 --game-mode 不一致，请只指定一种模式。")
        game_mode = selected_mode
    positional = " ".join(nickname).strip()
    if positional and (args.player_id is not None or args.nick is not None):
        raise QueryError("昵称和 --player-id / --nick 只需填写一项。")
    target = (args.player_id or args.nick or positional).strip()
    by_id = args.player_id is not None or (args.nick is None and target.isdecimal())
    return Query(
        target=target,
        by_id=by_id,
        view=resolved_view,
        season_id=args.season_id,
        season_type=args.season_type,
        index=index if index is not None else 1,
        game_mode=game_mode,
        room_mode_type=args.room_mode_type,
        play_mode=args.play_mode,
        room_id=args.room_id,
        limit=args.limit,
    )


def parse_query(
    text: str, *, view: str = "overview", allowed_views: tuple[str, ...] = VIEWS
) -> Query:
    parser = QueryParser(add_help=False)
    add_query_arguments(parser, view=view, allowed_views=allowed_views)
    try:
        return query_from_args(parser.parse_args(shlex.split(text)))
    except ValueError as exc:
        raise QueryError(str(exc)) from exc
