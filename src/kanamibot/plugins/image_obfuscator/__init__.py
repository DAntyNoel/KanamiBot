from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
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
_ARCHIVE = DATA_DIR / "image_obfuscator" / "source"
_INTERACTION_TIMEOUT = 60.0


@dataclass
class _Pending:
    action: str
    expires_at: float


_pending: dict[str, _Pending] = {}
_lock = asyncio.Lock()


async def _expire_pending(key: str, expires_at: float) -> None:
    await asyncio.sleep(max(0.0, expires_at - time.monotonic()))
    async with _lock:
        current = _pending.get(key)
        if current and current.expires_at <= time.monotonic() and current.expires_at == expires_at:
            _pending.pop(key, None)


def _session_key(event: MessageEvent) -> str:
    return f"{getattr(event, 'self_id', '')}:{event.user_id}"


def _private_event(event: MessageEvent) -> bool:
    return isinstance(event, PrivateMessageEvent) and getattr(event, "sub_type", "friend") in {
        "friend",
        "stranger",
        "other",
    }


def _command(event: MessageEvent) -> str | None:
    text = event.get_plaintext().strip()
    for command in _COMMANDS:
        if text == command or text.startswith(command):
            return command
    return None


async def _image_rule(event: MessageEvent) -> bool:
    if not _private_event(event):
        return False
    return _command(event) is not None or _session_key(event) in _pending


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
    if not _private_event(event):
        return
    key = _session_key(event)
    command = _command(event)
    async with _lock:
        pending = _pending.get(key)
        if pending and pending.expires_at <= time.monotonic():
            _pending.pop(key, None)
            pending = None
        if command:
            action = _COMMANDS[command]
            images = await download_all_images_from_event(event, bot)
            exact = event.get_plaintext().strip() == command
            if images:
                _pending.pop(key, None)
            elif exact:
                expires_at = time.monotonic() + _INTERACTION_TIMEOUT
                _pending[key] = _Pending(action, expires_at)
                asyncio.create_task(_expire_pending(key, expires_at))
                await bot.send(event, f"请发送要{command}的图片（可多张），60秒内有效。")
                return
            else:
                await bot.send(event, f"请在“{command}”后附图片，或直接回复/引用图片。")
                return
        elif pending:
            images = await download_all_images_from_event(event, bot)
            if not images:
                return
            action = pending.action
            _pending.pop(key, None)
        else:
            return

    await _transform_and_send(bot, event, action, images)
