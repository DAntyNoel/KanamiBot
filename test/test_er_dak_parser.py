import nonebot

nonebot.init()

from kanamibot.plugins.er_dak.parser import parse_command, parse_match_args  # noqa: E402


def test_parse_alias_and_multi_names() -> None:
    command = parse_command("/永轮 多查 A，B C")
    assert command.action == "多查"
    assert command.args == ("A", "B", "C")


def test_parse_default_help_and_matches() -> None:
    assert parse_command("/er").action == "帮助"
    assert parse_match_args(("B站丨咕咕禽OC", "5")) == ("B站丨咕咕禽OC", 5)
    assert parse_match_args(("8",)) == (None, 8)
