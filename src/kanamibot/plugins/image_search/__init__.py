from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from nonebot import get_driver, get_plugin_config, on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from kanamibot.core import ModuleRule
from kanamibot.core.message_reactions import react_ok
from kanamibot.core.paths import DATA_DIR, PROJECT_ROOT
from kanamibot.core.timing import timed

from .config import Config
from .engines import Browser, infer_accounts, reverse_search
from .images import prepare_image, prepare_preview, read_image, result_previews
from .results import format_reply, reply_entries, select_accounts
from .storage import Store

__plugin_meta__ = PluginMetadata(
    name="搜图",
    description="多引擎反查图片来源、作者账号和动画截图。",
    usage="引用图片发送 #搜图，或 #搜图 [图片] / #搜图 图片URL；#搜图 帮助",
    config=Config,
)
config = get_plugin_config(Config)
browser = Browser(config)
store = Store(DATA_DIR / "image_search" / "state.json", config)
_busy: set[str] = set()
_last: dict[str, float] = {}
HELP = (
    "引用图片发送 #搜图，或把 #搜图 和图片一起发送。\n"
    "支持在命令前艾特，例如 @12345678 #搜图。\n"
    "也支持 #搜图 https://公开图片地址；每次查第一张图片。\n"
    "可查询图源、作者社交账号和动画番名/集数。推断与相似图结果会注明。\n"
    "搜图会将图片提交给配置的第三方识图引擎。"
)


def command_text(event: MessageEvent) -> str:
    # OneBot at segments are already excluded by get_plaintext(); also accept typed QQ mentions.
    return re.sub(r"^(?:@\d+\s+)+", "", event.get_plaintext().strip())


async def command_rule(event: MessageEvent) -> bool:
    if not config.image_search_enabled:
        return False
    text = command_text(event)
    return text == "#搜图" or text.startswith(("#搜图 ", "#搜图\n", "#搜图\u3000", "#搜图\t"))


search = on_message(rule=Rule(command_rule) & ModuleRule("image_search"), priority=5, block=True)
get_driver().on_shutdown(browser.close)


@search.handle()
@timed()
async def handle_search(bot: Bot, event: MessageEvent) -> None:
    argument = command_text(event)[3:].strip()
    if argument in {"帮助", "help"}:
        await search.finish(HELP)
    if argument and not argument.startswith(("http://", "https://")):
        await search.finish(HELP)
    user = str(event.user_id)
    key = f"{bot.self_id}:{getattr(event, 'group_id', 'private')}:{user}"
    if key in _busy:
        await search.finish("上一张图片还在搜索，请等待结果。")
    if len(_busy) >= 4:
        await search.finish("当前搜图任务较多，请稍后重试。")
    owner = user in get_driver().config.superusers
    now = time.monotonic()
    if not owner and now - _last.get(key, float("-inf")) < config.image_search_cooldown:
        remaining = int(config.image_search_cooldown - now + _last[key]) + 1
        await search.finish(f"刚查过，请 {remaining} 秒后再试。")
    _busy.add(key)
    try:
        try:
            image = await read_image(bot, event, argument)
            if image is None:
                await bot.send(event, "请引用有图片的消息发送 #搜图，或将命令和图片一起发送。")
                return
            data, public_url = image
            data, mime = await asyncio.to_thread(prepare_image, data)
        except Exception as exc:
            logger.warning("搜图读取图片失败：{}", type(exc).__name__)
            reason = str(exc) if isinstance(exc, ValueError) else "图片下载或读取失败，请重发原图。"
            await bot.send(event, reason)
            return
        cache_key = store.cache_key(data)
        result = store.cached(cache_key)
        cached = result is not None
        if not cached:
            if not owner and (reason := store.quota_reason(user)):
                await bot.send(event, reason)
                return
            if not owner:
                store.charge(user)
        _last[key] = time.monotonic()
        # Keep only live cooldown entries to bound memory in large groups.
        for stale in list(_last):
            if time.monotonic() - _last[stale] > max(config.image_search_cooldown, 60):
                del _last[stale]
        await react_ok(bot, event)
        if result is None:
            result = await reverse_search(data, mime, config, browser, public_url)
            select_accounts(
                result, config.image_search_strict_consensus, config.image_search_max_accounts
            )
            if config.image_search_infer_accounts:
                # Search enrichment is optional; it cannot hold up the primary result indefinitely.
                try:
                    async with asyncio.timeout(35):
                        await infer_accounts(result, browser, config.image_search_max_accounts)
                except TimeoutError:
                    pass
            store.put(cache_key, result)
        text = format_reply(result, cached, show_failures=owner)
        entries = reply_entries(result)
        summary = Message(
            MessageSegment.text(
                format_reply(result, cached, show_failures=owner, include_entries=False)
            )
        )
        previews = dict(await result_previews(result))
        messages = [summary]
        for entry, source in entries:
            body, separator, source_link = entry.partition("\n图片链接：\n")
            item = Message(MessageSegment.text(body))
            if preview := previews.get(source):
                item += MessageSegment.image(preview)
            if separator:
                item += MessageSegment.text(f"{separator}{source_link}")
            messages.append(item)
        if not previews:
            summary += MessageSegment.text("\n预览：查询原图（未获取到结果缩略图）\n")
            summary += MessageSegment.image(await asyncio.to_thread(prepare_preview, data))
        if config.image_search_found_image and (
            result.accounts or config.image_search_send_on_miss
        ):
            path = Path(config.image_search_found_image)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            if path.is_file():
                summary += MessageSegment.image(path)
        if len(entries) > 3:

            async def forward(parts):
                nodes = [
                    MessageSegment.node_custom(
                        user_id=bot.self_id,
                        nickname="搜图结果",
                        content=part,
                    )
                    for part in parts
                ]
                if group_id := getattr(event, "group_id", None):
                    await bot.call_api("send_group_forward_msg", group_id=group_id, messages=nodes)
                else:
                    await bot.call_api(
                        "send_private_forward_msg", user_id=event.user_id, messages=nodes
                    )

            try:
                await forward(messages)
            except Exception:
                # Preserve the forward layout and omit quotes even when rich media fails.
                await forward([Message(part.extract_plain_text()) for part in messages])
        else:
            message = Message()
            for index, part in enumerate(messages):
                if index:
                    message += MessageSegment.text("\n\n")
                message += part
            try:
                await bot.send(event, MessageSegment.reply(event.message_id) + message)
            except Exception:
                # Rich media failures must not swallow a completed text search result.
                await bot.send(event, MessageSegment.text(text))
    except Exception:
        logger.exception("搜图处理失败")
        await bot.send(event, "搜图暂时失败，请稍后重试。")
    finally:
        _busy.discard(key)
