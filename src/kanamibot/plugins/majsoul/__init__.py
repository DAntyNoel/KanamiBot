from __future__ import annotations

import re

from nonebot import on_command, on_regex
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from kanamibot.core.group_manager import ModuleRule

from .source import MajsoulQuery, db_init, link_account, query_pt_byid
from .unicode import convert_majong_unicode

__plugin_meta__ = PluginMetadata(
    name="雀魂助手",
    description="雀魂数据查询、绑定查询与牌画转换",
    usage="qhpt, qhpaipu, qhinfo, qhyb, qhbind, qhm*, qh <牌型>",
)

MATCH_TYPE_ALIASES = {
    "3": "3",
    "三": "3",
    "三麻": "3",
    "4": "4",
    "四": "4",
    "四麻": "4",
}
INFO_MODELS = {"基本", "更多", "立直", "血统", "all"}
INFO_LEVELS = {"all", "金", "金东", "金南", "玉", "玉东", "玉南", "王", "王座", "王座东", "王座南"}
DATE_RE = re.compile(r"^(\d{4})(?:[-/年](\d{1,2})月?)?$")

_cmd = {
    "qhpt": r"(qhpt|雀魂分数|雀魂pt)\s*(\S+)(?:\s+(.+?))?\s*$",
    "qhpaipu": r"(qhpaipu|雀魂最近对局)\s*(\S+)(?:\s+(.+?))?\s*$",
    "qhinfo": r"(qhinfo|雀魂玩家详情)\s*(\S+)(?:\s+(.+?))?\s*$",
    "qhyb": r"(qhyb|雀魂月报)\s*(\S+)(?:\s+(.+?))?\s*$",
    "qhbind": r"(qhbind|雀魂绑定)\s*(\S+)$",
    "qhm_operation": r"qhm(pt|yb|info|paipu)(?:\s+(.+?))?\s*$",
    "qh_tile": r"qh\s+([0-9mpszMPSZ]+)$",
}


def _split_args(raw: str | None) -> list[str]:
    return [arg for arg in (raw or "").split() if arg]


def _normalize_match_type(value: object, default: str | None = None) -> str | None:
    if value is None:
        return default
    return MATCH_TYPE_ALIASES.get(str(value).strip(), default)


def _pop_match_type(args: list[str], default: str | None = None) -> str | None:
    if args:
        match_type = _normalize_match_type(args[0])
        if match_type:
            args.pop(0)
            return match_type
    return default


def _optional_positive_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return number if number > 0 else None


def _parse_type_and_number(
    raw: str | None,
    default_type: str | None = None,
) -> tuple[str | None, int | None]:
    args = _split_args(raw)
    match_type = _pop_match_type(args, default_type)
    number = _optional_positive_int(args[0]) if args else None
    return match_type, number


def _parse_info_args(raw: str | None) -> tuple[str | None, str, str | None]:
    args = _split_args(raw)
    match_type = _pop_match_type(args)
    model = None
    level = "all"

    for arg in args[:2]:
        if arg in INFO_MODELS:
            model = arg
        elif arg in INFO_LEVELS:
            level = arg
        elif model is None:
            model = arg
        else:
            level = arg
    return match_type, level, model


def _parse_month_args(raw: str | None) -> tuple[str | None, str | None, str | None]:
    args = _split_args(raw)
    match_type = _pop_match_type(args)
    year = None
    month = None

    if args:
        date_match = DATE_RE.match(args[0])
        if date_match:
            year, month = date_match.group(1), date_match.group(2)
            if month is None and len(args) >= 2 and re.fullmatch(r"\d{1,2}", args[1]):
                month = args[1]
        elif (
            len(args) >= 2
            and re.fullmatch(r"\d{4}", args[0])
            and re.fullmatch(r"\d{1,2}", args[1])
        ):
            year, month = args[0], args[1]
    return match_type, year, month


def _with_at(user_id: int, msg: Message | MessageSegment | str) -> Message:
    result = MessageSegment.at(user_id) + MessageSegment.text(" ")
    if isinstance(msg, Message):
        return result + msg
    if isinstance(msg, MessageSegment):
        return result + Message(msg)
    return result + Message(str(msg))


def _search(pattern: str, text: str) -> re.Match[str] | None:
    return re.match(pattern, text.strip(), re.IGNORECASE)


qhpt_matcher = on_regex(_cmd['qhpt'], priority=10, block=True, rule=ModuleRule('majsoul'))
@qhpt_matcher.handle()
async def _(event: GroupMessageEvent):
    msg_text = event.get_plaintext().strip()
    m = _search(_cmd['qhpt'], msg_text)
    if not m:
        return

    username = m.group(2)
    raw_args = m.group(3)
    if raw_args:
        selecttype, selectindex = _parse_type_and_number(raw_args, default_type="4")
        msg = await MajsoulQuery.getcertaininfo(username, selecttype or "4", selectindex or 1)
    else:
        msg = await MajsoulQuery.query(username)
    await qhpt_matcher.finish(_with_at(event.user_id, msg))


qhpaipu_matcher = on_regex(_cmd['qhpaipu'], priority=10, block=True, rule=ModuleRule('majsoul'))
@qhpaipu_matcher.handle()
async def _(event: GroupMessageEvent):
    msg_text = event.get_plaintext().strip()
    m = _search(_cmd['qhpaipu'], msg_text)
    if not m:
        return

    username = m.group(2)
    type_, cnt = _parse_type_and_number(m.group(3))
    msg = await MajsoulQuery.getsomeqhpaipu(username, type_, cnt)
    await qhpaipu_matcher.finish(_with_at(event.user_id, msg))


qhinfo_matcher = on_regex(_cmd['qhinfo'], priority=10, block=True, rule=ModuleRule('majsoul'))
@qhinfo_matcher.handle()
async def _(event: GroupMessageEvent):
    msg_text = event.get_plaintext().strip()
    m = _search(_cmd['qhinfo'], msg_text)
    if not m:
        return

    username = m.group(2)
    type_, level, model = _parse_info_args(m.group(3))
    msg = await MajsoulQuery.getplayerdetail(username, type_, level, model)
    await qhinfo_matcher.finish(_with_at(event.user_id, msg))


qhyb_matcher = on_regex(_cmd['qhyb'], priority=10, block=True, rule=ModuleRule('majsoul'))
@qhyb_matcher.handle()
async def _(event: GroupMessageEvent):
    msg_text = event.get_plaintext().strip()
    m = _search(_cmd['qhyb'], msg_text)
    if not m:
        return

    username = m.group(2)
    type_, year, month = _parse_month_args(m.group(3))
    msg = await MajsoulQuery.getmonthreport(username, type_, year, month)
    await qhyb_matcher.finish(_with_at(event.user_id, msg))


qhbind_matcher = on_regex(_cmd['qhbind'], priority=10, block=True, rule=ModuleRule('majsoul'))
@qhbind_matcher.handle()
async def _(event: GroupMessageEvent):
    msg_text = event.get_plaintext().strip()
    m = _search(_cmd['qhbind'], msg_text)
    if not m:
        return

    username = m.group(2)
    msg = await MajsoulQuery.bind_account(event.user_id, username)
    await qhbind_matcher.finish(_with_at(event.user_id, msg))


qhm_matcher = on_regex(_cmd['qhm_operation'], priority=10, block=True, rule=ModuleRule('majsoul'))
@qhm_matcher.handle()
async def _(event: GroupMessageEvent):
    msg_text = event.get_plaintext().strip()
    m = _search(_cmd['qhm_operation'], msg_text)
    if not m:
        return

    operation = m.group(1)
    raw_args = m.group(2)

    player_info = link_account(event.user_id)
    if not player_info.get("bind"):
        await qhm_matcher.finish(_with_at(event.user_id, player_info["msg"]))
        return

    username = player_info.get("playername")
    if operation == "pt":
        msg = await query_pt_byid(player_info.get("account"))
    elif operation == "yb":
        type_, year, month = _parse_month_args(raw_args)
        type_ = type_ or "4"
        msg = await MajsoulQuery.getmonthreport(username, type_, year, month)
    elif operation == "info":
        type_, level, model = _parse_info_args(raw_args)
        msg = await MajsoulQuery.getplayerdetail(username, type_ or "4", level, model)
    elif operation == "paipu":
        type_, cnt = _parse_type_and_number(raw_args, default_type="4")
        msg = await MajsoulQuery.getsomeqhpaipu(username, type_, cnt)
    else:
        msg = "无此方法"
    await qhm_matcher.finish(_with_at(event.user_id, msg))


# --- 管理员命令 ---
qhinit_matcher = on_command(
    "qhinit",
    permission=SUPERUSER,
    priority=1,
    block=True,
    rule=ModuleRule("majsoul"),
)
@qhinit_matcher.handle()
async def _(event: GroupMessageEvent):
    db_init()
    await qhinit_matcher.finish(MessageSegment.at(event.user_id) + " 雀魂查询模块已初始化")


# --- 牌画转换 ---
qh_tile_matcher = on_regex(_cmd['qh_tile'], priority=10, block=True, rule=ModuleRule('majsoul'))
@qh_tile_matcher.handle()
async def _(event: GroupMessageEvent):
    msg_text = event.get_plaintext().strip()
    m = _search(_cmd['qh_tile'], msg_text)
    if m:
        tiles = m.group(1).lower()
        output = convert_majong_unicode(tiles)
        await qh_tile_matcher.finish(output or "牌型格式有误")


