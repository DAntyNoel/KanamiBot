from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from typing import Annotated

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.qq import Bot, Message, MessageSegment
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent
from nonebot.exception import ActionFailed, NetworkError
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from PIL import Image, ImageDraw

__plugin_meta__ = PluginMetadata(
    name="QQBot 图片收发测试",
    description="发送本地测试图，或接收并回传指令附带的第一张图片。",
    usage="@机器人 image；@机器人 image + 图片（同一条消息）；image help",
    supported_adapters={"~qq"},
)

IMAGE_HELP = (
    "QQBot 图片收发测试\n"
    "发送测试图：@机器人 image\n"
    "接收并回传：在同一条消息中发送 @机器人 image 和图片。\n"
    "多张图片只回传第一张；不自动回复没有 image 指令的图片。\n"
    "私聊直接发送 image；群内开启接收所有消息后可免艾特。\n"
    "也支持 /image、#image；此功能用于图片收发测试。"
)


async def is_qq_message(event: Event) -> bool:
    return isinstance(event, (C2CMessageCreateEvent, GroupMessageCreateEvent))


@lru_cache(maxsize=1)
def build_test_image() -> bytes:
    canvas = Image.new("RGB", (480, 240), "#eef2ff")
    drawing = ImageDraw.Draw(canvas)
    drawing.rounded_rectangle((20, 20, 460, 220), radius=24, fill="#25316d")
    drawing.text((48, 55), "QQBot IMAGE", fill="white", font_size=36)
    drawing.text((48, 115), "Send / receive test", fill="#c7d2fe", font_size=24)
    drawing.text((48, 164), "480 x 240 PNG", fill="#c7d2fe", font_size=20)
    buffer = BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


image_test = on_command("image", rule=is_qq_message, priority=5, block=True)


@image_test.handle()
async def handle_image(
    bot: Bot,
    event: C2CMessageCreateEvent | GroupMessageCreateEvent,
    args: Annotated[Message, CommandArg()],
) -> None:
    if args.extract_plain_text().strip():
        await bot.send(event, IMAGE_HELP)
        return
    attachments = event.attachments or []
    images = [
        attachment for attachment in attachments
        if attachment.content_type.startswith("image/") and attachment.url
    ]
    if attachments and not images:
        await bot.send(event, "未检测到图片，请将 image 指令和图片放在同一条消息中发送。")
        return
    if images:
        response = Message(f"收到 {len(images)} 张图片，回传第一张。\n")
        response += MessageSegment.image(images[0].url)
    else:
        response = Message("QQBot 图片发送测试\n")
        response += MessageSegment.file_image(build_test_image())
    try:
        await bot.send(event, response)
    except (ActionFailed, NetworkError):
        logger.warning("QQBot 图片上传或发送失败")
        await bot.send(event, "图片上传或发送失败，请稍后重试。")
