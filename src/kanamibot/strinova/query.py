from __future__ import annotations

import argparse
import shlex
from dataclasses import dataclass

VIEWS = ("overview", "profile", "status", "career", "history", "standings", "match")


class QueryError(ValueError):
    """A query that can be explained directly to the user."""


class QueryParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise QueryError(message)


def add_query_arguments(parser: argparse.ArgumentParser, *, view: str = "overview") -> None:
    parser.add_argument("nickname", nargs="*", help="玩家昵称；纯数字默认按 ID 查询")
    identity = parser.add_mutually_exclusive_group()
    identity.add_argument("--player-id", help="明确按 player_id 查询")
    identity.add_argument("--nick", help="明确按昵称查询，包括纯数字昵称")
    parser.add_argument("--view", choices=VIEWS, default=view)
    parser.add_argument("--season-id", type=int, default=0)
    parser.add_argument("--season-type", type=int, default=1)
    parser.add_argument("--index", type=int, default=1, help="战绩页码，从 1 开始")
    parser.add_argument("--game-mode", type=int, default=0)
    parser.add_argument("--room-mode-type", type=int, default=0)
    parser.add_argument("--play-mode", type=int, default=0)
    parser.add_argument("--room-id", help="单局房间 ID，必须保留完整数字")
    parser.add_argument("--limit", type=int, default=8, help="图片最多展示 1–20 条战绩")


def numeric_id(value: str, label: str, maximum: int = 2**64 - 1) -> str:
    if not value.isascii() or not value.isdecimal() or not 0 < int(value) <= maximum:
        raise QueryError(f"{label} 必须为有效的正整数。")
    return str(int(value))


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
            not self.target.strip()
            or len(self.target) > 64
            or any(ord(char) < 32 for char in self.target)
        ):
            raise QueryError("请输入 1–64 个字符的玩家昵称或 ID。")
        if self.by_id:
            numeric_id(self.target, "玩家 ID", 2**32 - 1)
        if self.view not in VIEWS:
            raise QueryError("不支持的查询视图。")
        if self.index < 1 or self.index > 1000 or not 1 <= self.limit <= 20:
            raise QueryError("页码必须为 1–1000，展示条数必须为 1–20。")
        if any(
            not 0 <= value <= 2**31 - 1
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
            numeric_id(self.room_id, "房间 ID")
        if self.view == "match" and self.room_id is None:
            raise QueryError("单局查询需要 --room-id {房间ID}。")


def query_from_args(args: argparse.Namespace) -> Query:
    positional = " ".join(args.nickname).strip()
    if positional and (args.player_id is not None or args.nick is not None):
        raise QueryError("昵称和 --player-id / --nick 只需填写一项。")
    target = (args.player_id or args.nick or positional).strip()
    by_id = args.player_id is not None or (args.nick is None and target.isdecimal())
    return Query(
        target=target,
        by_id=by_id,
        view=args.view,
        season_id=args.season_id,
        season_type=args.season_type,
        index=args.index,
        game_mode=args.game_mode,
        room_mode_type=args.room_mode_type,
        play_mode=args.play_mode,
        room_id=args.room_id,
        limit=args.limit,
    )


def parse_query(text: str, *, view: str = "overview") -> Query:
    parser = QueryParser(add_help=False)
    add_query_arguments(parser, view=view)
    try:
        return query_from_args(parser.parse_args(shlex.split(text)))
    except ValueError as exc:
        raise QueryError(str(exc)) from exc
