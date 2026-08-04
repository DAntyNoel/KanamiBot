from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

from kanamibot.core.paths import DATA_DIR
from kanamibot.core.utils.image import download_all_images_from_event

from .backend import decode, encode, image_extension, save_bytes

__plugin_meta__ = PluginMetadata(
    name="Image Obfuscator",
    description="使用小番茄 Gilbert 曲线混淆/解混淆图片。",
    usage="混淆 [图片...] / 解混淆 [图片...]；精确发送命令后可在 60 秒内继续发送图片。",
)

_COMMANDS = {"混淆": "encode", "解混淆": "decode"}
_END_COMMAND = "结束"
_ARCHIVE = DATA_DIR / "image_obfuscator" / "source"
_INTERACTION_TIMEOUT = 60.0


@dataclass
class _Pending:
    action: str
    expires_at: float


_pending: dict[str, _Pending] = {}
_lock = asyncio.Lock()


async def _expire_pending(
    key: str, expires_at: float, bot: Bot, event: MessageEvent
) -> None:
    await asyncio.sleep(max(0.0, expires_at - time.monotonic()))
    expired = False
    async with _lock:
        current = _pending.get(key)
        if current and current.expires_at <= time.monotonic() and current.expires_at == expires_at:
            _pending.pop(key, None)
            expired = True
    if expired:
        try:
            await bot.send(
                event, "图片交互已超时，当前操作已结束；如需继续请重新发送混淆或解混淆命令。"
            )
        except Exception:
            logger.debug("发送图片交互超时提醒失败", exc_info=True)


def _session_key(event: MessageEvent) -> str:
    return f"{getattr(event, 'self_id', '')}:{event.user_id}"


def _private_event(event: MessageEvent) -> bool:
    return isinstance(event, PrivateMessageEvent) and getattr(event, "sub_type", "friend") in {
        "friend",
        "stranger",
        "other",
    }


def _temporary_private_event(event: MessageEvent) -> bool:
    return isinstance(event, PrivateMessageEvent) and getattr(event, "sub_type", "") == "group"


def _group_event(event: MessageEvent) -> bool:
    return isinstance(event, GroupMessageEvent)


def _command(event: MessageEvent) -> str | None:
    text = event.get_plaintext().strip()
    if text == _END_COMMAND:
        return _END_COMMAND
    for command in _COMMANDS:
        if text == command or text.startswith(f"{command} "):
            return command
    return None


async def _image_rule(event: MessageEvent) -> bool:
    command = _command(event)
    if _private_event(event) or _temporary_private_event(event):
        return command is not None or _session_key(event) in _pending
    if _group_event(event):
        return command is not None
    return False


def _is_supported_event(event: MessageEvent) -> bool:
    if _private_event(event) or _temporary_private_event(event):
        return True
    if _group_event(event):
        return True
    return False


async def _send_group_decode_notice(bot: Bot, event: MessageEvent) -> None:
    await bot.send(event, "群聊不支持解混淆，请私聊机器人后发送“解混淆”命令。")


def _private_interaction(event: MessageEvent) -> bool:
    return _private_event(event) or _temporary_private_event(event)


def _schedule_pending(key: str, pending: _Pending, bot: Bot, event: MessageEvent) -> None:
    asyncio.create_task(_expire_pending(key, pending.expires_at, bot, event))


async def _start_pending(
    key: str, action: str, command: str, bot: Bot, event: MessageEvent
) -> _Pending:
    expires_at = time.monotonic() + _INTERACTION_TIMEOUT
    pending = _Pending(action, expires_at)
    _pending[key] = pending
    _schedule_pending(key, pending, bot, event)
    await bot.send(event, f"请发送要{command}的图片（可多张），60秒内有效。")
    return pending


async def _transform_and_send(
    bot: Bot,
    event: MessageEvent,
    action: str,
    images: list[bytes],
) -> None:
    if not images:
        return
    transform = encode if action == "encode" else decode
    now = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
    archive_dir = _ARCHIVE / action / now
    outputs: list[bytes] = []
    for index, image in enumerate(images, 1):
        try:
            output = transform(image)
        except Exception as exc:
            logger.warning("图片%s处理失败: %s", index, exc)
            continue
        # encode archives its input; decode archives the decoded result as requested.
        archive_data = image if action == "encode" else output
        extension = "jpg" if action == "decode" else image_extension(archive_data)
        save_bytes(archive_data, archive_dir / f"{index:03d}.{extension}")
        outputs.append(output)
    if not outputs:
        await bot.send(event, "图片处理失败，请确认图片格式有效。")
        return
    message = Message(f"处理完成，共 {len(outputs)} 张：\n")
    for output in outputs:
        message += MessageSegment.image(output)
    await bot.send(event, message)


image_obfuscator = on_message(rule=_image_rule, priority=8, block=True)


@image_obfuscator.handle()
async def handle_image_obfuscator(bot: Bot, event: MessageEvent) -> None:
    if not _is_supported_event(event):
        return
    key = _session_key(event)
    command = _command(event)
    action: str | None = None
    images: list[bytes] = []
    async with _lock:
        pending = _pending.get(key)
        if pending and pending.expires_at <= time.monotonic():
            _pending.pop(key, None)
            pending = None

        if command == _END_COMMAND:
            if pending:
                _pending.pop(key, None)
                await bot.send(event, "本轮图片交互已结束；如需继续请重新发送混淆或解混淆命令。")
            else:
                await bot.send(event, "当前没有进行中的图片交互。")
            return

        if command:
            if pending:
                _pending.pop(key, None)
                await bot.send(event, "上一轮图片交互已结束，开始处理新的命令。")
            if _group_event(event) and command == "解混淆":
                await _send_group_decode_notice(bot, event)
                return

            action = _COMMANDS[command]
            images = await download_all_images_from_event(event, bot)
            if not images:
                if _group_event(event):
                    await bot.send(event, "群聊混淆请直接附带图片，不支持后续交互式发送图片。")
                else:
                    await _start_pending(key, action, command, bot, event)
                return

            if _private_interaction(event):
                pending = _Pending(action, time.monotonic() + _INTERACTION_TIMEOUT)
                _pending[key] = pending
                _schedule_pending(key, pending, bot, event)
        elif pending:
            images = await download_all_images_from_event(event, bot)
            if not images:
                return
            action = pending.action
            pending.expires_at = time.monotonic() + _INTERACTION_TIMEOUT
            _schedule_pending(key, pending, bot, event)
        else:
            return

    if action:
        await _transform_and_send(bot, event, action, images)
