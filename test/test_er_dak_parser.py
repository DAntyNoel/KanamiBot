import re

import nonebot
import pytest

nonebot.init()

from kanamibot.plugins.er_dak import COMMAND_PATTERN  # noqa: E402
from kanamibot.plugins.er_dak.parser import parse_command, parse_match_args  # noqa: E402


def test_parse_alias_and_multi_names() -> None:
    command = parse_command("/永轮 多查 A，B C")
    assert command.action == "多查"
    assert command.args == ("A", "B", "C")


def test_parse_default_help_and_matches() -> None:
    assert parse_command("/er").action == "帮助"
    assert parse_match_args(("B站丨咕咕禽OC", "5")) == ("B站丨咕咕禽OC", 5)
    assert parse_match_args(("8",)) == (None, 8)


def test_quoted_names_and_direct_queries() -> None:
    assert parse_command('/er 查询玩家 "Player Name" 普通').args == ("Player Name", "普通")
    assert parse_command('/查询装备 "Item Name"').action == "查询装备"
    assert parse_command("/ER radar Player").action == "雷达"
    assert re.fullmatch(COMMAND_PATTERN, "/ER 查询 Player", re.IGNORECASE)
    assert re.fullmatch(COMMAND_PATTERN, "/查询角色装备 席琳 排位 灭钻")
    assert re.fullmatch(COMMAND_PATTERN, "/永恒多少分 国服")
    assert not re.fullmatch(COMMAND_PATTERN, "/帮助")


def test_unmatched_quotes_are_reported() -> None:
    with pytest.raises(ValueError, match="引号不匹配"):
        parse_command('/er 查询玩家 "unfinished')
