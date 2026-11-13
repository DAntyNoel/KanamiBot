from __future__ import annotations

import asyncio

from nonebot import get_plugin_config, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from kanamibot.arknights.client import MCP, BackendError
from kanamibot.arknights.commands import command_body, parse
from kanamibot.arknights.records import Records
from kanamibot.arknights.render import render_png
from kanamibot.arknights.service import Queries, Result
from kanamibot.core import ModuleRule
from kanamibot.core.check_perm import LevelAdmin, LevelSuper
from kanamibot.core.message_reactions import react_ok
from kanamibot.core.paths import DATA_DIR
from kanamibot.core.timing import timed

from .config import Config

HELP = """明日方舟查询 · #粥 / #方舟 / #ark
先写查询对象，再写分项：
#粥 银灰　　#粥 银灰 技能
#粥 德克萨斯 异格 技能
#粥 国度 敌人　　#粥 银灰 模组
#粥 固源岩 材料　　#粥 1-7 关卡
分项：技能、天赋、特性、属性、档案、基建、模组、材料、专精、皮肤、召唤物、敌人、关卡、术语

#粥 卫 银灰　　#粥 卫 炎 盟约
#粥 卫 <名称> 策略
卫 = 卫戍 = 卫戍协议
#卫 = #粥 卫，例如 #卫 炎 盟约
#卫 boss（头像编号列表）
#卫 boss 1　　#卫 boss 1 隐秘（PRTS 原文）
#粥 盐酸 <关键词>（PRTS Wiki）
盐酸 = 演算 = 生息演算
#粥 <关键词> wiki

#粥 绰号 缄默德克萨斯 异德
#粥 绰号 易得 删除
#粥 绰号 列表（按阵营合并聊天记录）
绰号记录全局共享；同名冲突需先删除。
多个候选：#粥 选择 1（本人、本群，5 分钟有效）"""

__plugin_meta__ = PluginMetadata(
    name="明日方舟查询",
    description="Amiya MCP 干员与卫戍查询、异格和本地绰号。",
    usage=HELP,
    config=Config,
)
config = get_plugin_config(Config)
queries = Queries(
    MCP(config.arknights_mcp_url, config.arknights_timeout),
    Records(DATA_DIR / "arknights" / "nicknames.json"),
)
_busy: set[str] = set()


async def matches(event: MessageEvent) -> bool:
    return config.arknights_enabled and command_body(event.get_plaintext()) is not None


query = on_message(rule=Rule(matches) & ModuleRule("arknights"), priority=5, block=True)


def split_pages(text: str, limit: int = 2200) -> list[str]:
    return [text[index : index + limit] for index in range(0, len(text), limit)]


async def send_pages(bot: Bot, event: MessageEvent, pages: list[str]) -> None:
    if len(pages) == 1:
        await bot.send(event, MessageSegment.text(pages[0]))
        return
    for start in range(0, len(pages), 35):
        nodes = [
            MessageSegment.node_custom(
                user_id=int(bot.self_id),
                nickname="明日方舟资料",
                content=Message(MessageSegment.text(page)),
            )
            for page in pages[start : start + 35]
        ]
        if isinstance(event, GroupMessageEvent):
            await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=nodes)
        else:
            await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=nodes)


async def send_result(bot: Bot, event: MessageEvent, result: Result) -> None:
    if result.pages:
        pages = [piece for page in result.pages for piece in split_pages(page)]
        await send_pages(bot, event, pages)
        return
    if result.card_url or result.card_html:
        try:
            card = (
                await render_png(result.card_html, config.arknights_browser_channel)
                if result.card_html
                else await queries.mcp.image(result.card_url)
            )
            await bot.send(
                event,
                MessageSegment.reply(event.message_id)
                + (MessageSegment.text(result.card_text + "\n") if result.card_text else Message())
                + MessageSegment.image(card),
            )
            return
        except Exception:
            logger.exception("明日方舟卡片发送失败，改发文字")
            if not result.choices:
                result.text = (
                    result.fallback or result.text
                ) + "\n卡片暂时无法发送，以上为文字资料。"
    await send_pages(bot, event, split_pages(result.text or "暂无查询结果。"))


@query.handle()
@timed()
async def handle_query(bot: Bot, event: MessageEvent) -> None:
    key = f"{bot.self_id}:{getattr(event, 'group_id', 'private')}:{event.user_id}"
    try:
        command = parse(event.get_plaintext())
    except ValueError as exc:
        await bot.send(event, MessageSegment.text(str(exc)))
        return
    if command.action == "help":
        await bot.send(event, MessageSegment.text(HELP))
        return
    if config.arknights_nickname_admin_only and command.action in {"alias_add", "alias_delete"}:
        permission = LevelAdmin if isinstance(event, GroupMessageEvent) else LevelSuper
        if not await permission(bot, event):
            await bot.send(event, "绰号管理当前仅限群管理员和超级用户。")
            return
    if key in _busy:
        await bot.send(event, "上一条明日方舟查询还在处理中。")
        return
    if len(_busy) >= 4:
        await bot.send(event, "明日方舟查询任务较多，请稍后重试。")
        return
    _busy.add(key)
    try:
        await react_ok(bot, event)
        async with asyncio.timeout(config.arknights_timeout + 30):
            result = await queries.execute(command, key)
            await send_result(bot, event, result)
    except (ValueError, BackendError) as exc:
        await bot.send(event, MessageSegment.text(str(exc)))
    except TimeoutError:
        await bot.send(event, "明日方舟查询超时，请稍后重试。")
    except Exception:
        logger.exception("明日方舟查询处理失败")
        await bot.send(event, "明日方舟查询或聊天记录发送失败，请稍后重试。")
    finally:
        _busy.discard(key)
