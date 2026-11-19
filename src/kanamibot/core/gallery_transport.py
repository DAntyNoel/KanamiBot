from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from nonebot import get_driver
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.qq import Message as QQMessage
from nonebot.adapters.qq import MessageSegment as QQSegment
from nonebot.adapters.qq.event import GroupMessageCreateEvent
from nonebot.permission import Permission
from nonebot.rule import Rule
from PIL import Image, ImageOps

from .group_manager import ADMIN_PERMISSION as ONEBOT_ADMIN_PERMISSION
from .group_manager import DATA_PATH, __all_modules__, group_config
from .paths import DATA_DIR, PROJECT_ROOT
from .utils.image import download_all_images_from_event

CONFIG_PATH = DATA_DIR / "qqbot_gallery.json"
__all_modules__.add("image")


def settings() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def is_qq(event: Event) -> bool:
    return isinstance(event, GroupMessageCreateEvent)


def _identity(kind: str, raw: str) -> int:
    mapped = settings().get(f"{kind}_bindings", {}).get(raw)
    if mapped is not None:
        value = int(mapped)
        if value <= 0:
            raise ValueError("绑定的 QQ 号和群号必须为正整数")
        return value
    return -int.from_bytes(hashlib.sha256(f"qqbot:{kind}:{raw}".encode()).digest()[:16])


def group_id(event: Event) -> int:
    return _identity("group", event.group_openid) if is_qq(event) else event.group_id


def user_id(event: Event) -> int:
    return _identity("user", event.get_user_id()) if is_qq(event) else event.user_id


def scope(bot: Bot, event: Event) -> str:
    return f"qq:{bot.self_id}:{event.group_openid}" if is_qq(event) else ""


def superusers(event: Event) -> list[str]:
    if not is_qq(event):
        return list(get_driver().config.superusers)
    raw = dotenv_values(PROJECT_ROOT / ".env").get("SUPERUSERS") or "[]"
    configured = json.loads(raw)
    return [str(value) for value in (
        configured if isinstance(configured, list) else [configured]
    )]


def is_superuser(event: Event) -> bool:
    return (
        str(user_id(event)) in superusers(event)
        or is_qq(event) and event.get_user_id() in settings().get("superusers", [])
    )


def can_create(event: Event) -> bool:
    if is_qq(event):
        official = settings().get("superusers", [])
        if official and event.get_user_id() == official[0]:
            return True
    users = superusers(event)
    return bool(users) and str(user_id(event)) == users[0]


async def _enabled(bot: Bot, event: Event) -> bool:
    if not isinstance(event, (GroupMessageEvent, GroupMessageCreateEvent)):
        return False
    if is_superuser(event):
        return True
    if is_qq(event):
        config = json.loads(DATA_PATH.read_text(encoding="utf-8")) if DATA_PATH.exists() else {}
        data = config.get(str(group_id(event)), {})
        return (
            user_id(event) not in data.get("blacklist", [])
            and data.get("modules", {}).get("image", True)
        )
    return (
        not group_config.is_user_banned(str(event.group_id), event.user_id)
        and group_config.is_module_enabled(str(event.group_id), "image")
    )


async def _admin(bot: Bot, event: Event) -> bool:
    if not isinstance(event, (GroupMessageEvent, GroupMessageCreateEvent)):
        return False
    if not is_qq(event):
        return await ONEBOT_ADMIN_PERMISSION(bot, event)
    return is_superuser(event) or event.author.member_role in {"owner", "admin"}


GROUP_RULE = Rule(_enabled)
ADMIN_PERMISSION = Permission(_admin)


class QQSegments:
    text = staticmethod(QQSegment.text)
    at = staticmethod(QQSegment.mention_user)

    @staticmethod
    def image(value):
        if isinstance(value, Path):
            value = value.read_bytes()
        return QQSegment.file_image(value) if isinstance(value, bytes) else QQSegment.image(value)


def segments(event: Event):
    return QQSegments if is_qq(event) else MessageSegment


def message(event: Event, text: str):
    return QQMessage(text) if is_qq(event) else Message(text)


def receipt_id(receipt: Any) -> str | int | None:
    if isinstance(receipt, dict):
        return (
            (receipt.get("ext_info") or {}).get("ref_idx")
            or receipt.get("id") or receipt.get("message_id")
        )
    ext = getattr(receipt, "ext_info", None)
    return getattr(ext, "ref_idx", None) or getattr(receipt, "id", None)


def reply_id(event: Event) -> str | None:
    if not is_qq(event):
        return None
    if event.reply and event.reply.msg_idx:
        return event.reply.msg_idx
    if event.message_scene:
        return next((item.partition("=")[2] for item in event.message_scene.ext
                     if item.startswith("ref_msg_idx=")), None)
    return None


async def download_images(event: Event, bot: Bot) -> list[bytes]:
    if not is_qq(event):
        return await download_all_images_from_event(event, bot)
    attachments = list(event.attachments or [])
    if event.reply:
        attachments.extend(event.reply.attachments or [])
    images: list[bytes] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for attachment in attachments:
            if not attachment.content_type.startswith("image/") or not attachment.url:
                continue
            url = attachment.url
            if url in seen:
                continue
            seen.add(url)
            if url.startswith("//"):
                url = "https:" + url
            if not url.startswith(("https://", "http://")):
                raise ValueError("图片地址无效")
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 20 * 1024 * 1024:
                        raise ValueError("单张图片不能超过 20 MB")
                if content:
                    with Image.open(BytesIO(content)) as image:
                        image.verify()
                    images.append(bytes(content))
    return images


def writes_enabled(event: Event) -> bool:
    return not is_qq(event) or settings().get("enable_writes") is True


def duplicate_preview(messages: list[QQMessage], summary: str) -> QQMessage:
    images = [segment.data["content"] for msg in messages
              for segment in msg if segment.type == "file_image"][:12]
    if not images:
        return QQMessage(summary)
    columns = min(4, len(images))
    canvas = Image.new("RGB", (columns * 240, ((len(images) + columns - 1) // columns) * 240),
                       "#eeeeee")
    for index, content in enumerate(images):
        with Image.open(BytesIO(content)) as image:
            thumbnail = ImageOps.contain(ImageOps.exif_transpose(image).convert("RGB"), (224, 224))
            canvas.paste(thumbnail, ((index % columns) * 240 + 8, (index // columns) * 240 + 8))
    output = BytesIO()
    canvas.save(output, format="PNG")
    text = summary[:3500] + "\n预览按输入图、已存图顺序排列（最多12张）：\n"
    return QQMessage(text) + QQSegment.file_image(
        output.getvalue()
    )
