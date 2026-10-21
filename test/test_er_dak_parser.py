import re

import nonebot
import pytest

nonebot.init()

from kanamibot.plugins.er_dak.parser import (  # noqa: E402
    COMMAND_PATTERN,
    ParsedCommand,
    match_character,
    parse_command,
    parse_match_args,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("#永恒轮回战绩 Player", ParsedCommand("查询玩家", ("Player",))),
        ("#BS战绩 Player 普通", ParsedCommand("查询玩家", ("Player", "普通"))),
        ("#永恒轮回角色 席琳", ParsedCommand("查询角色", ("席琳",))),
        ("#bs角色 席琳 排位 星陨", ParsedCommand("查询角色", ("席琳", "排位", "星陨"))),
        ("#BS 席琳", ParsedCommand("自动查询", ("席琳",))),
        ("#BS B站丨咕咕禽OC", ParsedCommand("自动查询", ("B站丨咕咕禽OC",))),
        ('#BS战绩 "Player Name" 普通', ParsedCommand("查询玩家", ("Player Name", "普通"))),
        ("#BS　席琳", ParsedCommand("自动查询", ("席琳",))),
        ("#BS角色  席琳　排位", ParsedCommand("查询角色", ("席琳", "排位"))),
        ("#BS 多查 A，B C", ParsedCommand("多查", ("A", "B", "C"))),
        ("#BS 雷达 Player", ParsedCommand("雷达", ("Player",))),
        ("#BS radar Player", ParsedCommand("雷达", ("Player",))),
        ("#BS 角色 席琳", ParsedCommand("查询角色", ("席琳",))),
        ("#BS 帮助", ParsedCommand("帮助", ())),
        ("#BS ", ParsedCommand("帮助", ())),
        ("#BS战绩 ", ParsedCommand("查询玩家", ())),
    ],
)
def test_command_heads_and_arguments(message, expected) -> None:
    assert re.fullmatch(COMMAND_PATTERN, message, re.IGNORECASE)
    assert parse_command(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "#BS席琳",
        "#BS战绩Player",
        "#BS角色席琳",
        "#永恒轮回战绩Player",
        "#永恒轮回角色席琳",
        "#BS",
        "#BS角色",
        "#BS战绩",
        "#BS\t席琳",
        "#BS\n席琳",
        "#BS 席琳\n",
        "#BS 席琳\n排位",
        "#BS角色2 席琳",
        "#BS战绩表 Player",
        "#ER Player",
        "/er 查询 Player",
        "/查询玩家 Player",
        "er Player",
    ],
)
def test_requires_space_after_an_exact_header(message) -> None:
    assert not re.search(COMMAND_PATTERN, message, re.IGNORECASE)
    with pytest.raises(ValueError, match="命令头后必须有空格"):
        parse_command(message)


def test_match_count_and_quoted_names() -> None:
    assert parse_match_args(("B站丨咕咕禽OC", "5")) == ("B站丨咕咕禽OC", 5)
    assert parse_match_args(("8",)) == (None, 8)
    with pytest.raises(ValueError, match="引号不匹配"):
        parse_command('#BS战绩 "unfinished')


CHARACTERS = (
    {"id": 62, "name": "席琳", "key": "Celine"},
    {"id": 19, "name": "艾玛", "key": "Emma"},
)


@pytest.mark.parametrize("query", ["席琳", "CELINE", "62", "xilin", "xl"])
def test_auto_query_recognizes_character_names_and_pinyin(query) -> None:
    assert match_character(query, CHARACTERS) == "席琳"


@pytest.mark.parametrize("query", ["Player Name", "B站丨咕咕禽OC", "席琳123", "席"])
def test_auto_query_does_not_fuzzy_match_player_names(query) -> None:
    assert match_character(query, CHARACTERS) is None


def test_ambiguous_character_initials_require_full_name() -> None:
    with pytest.raises(ValueError, match="拼音匹配多个角色"):
        match_character("xl", (*CHARACTERS, {"id": 99, "name": "夏琳", "key": "XiaLin"}))
