from __future__ import annotations

import imghdr
import io
import re
from typing import Any

import httpx
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from PIL import Image as PILImage

# 调试开关
DEBUG = False

CQ_IMAGE_RE = re.compile(r"\[CQ:image,([^\]]+)\]")
CQ_FORWARD_RE = re.compile(r"\[CQ:forward,([^\]]+)\]")
CQ_KV_RE = re.compile(r"([a-zA-Z_][\w-]*)=([^,\]]*)")


def _segment_type_and_data(segment: Any) -> tuple[str, dict[str, Any]] | None:
    if isinstance(segment, MessageSegment):
        return segment.type, segment.data
    if isinstance(segment, dict):
        segment_type = str(segment.get("type", ""))
        data = segment.get("data", {})
        return segment_type, data if isinstance(data, dict) else {}
    return None


def _extract_cq_params(raw: str) -> dict[str, str]:
    return {key: value for key, value in CQ_KV_RE.findall(raw)}


def _extract_reply_message_id(event: MessageEvent) -> int | None:
    for segment in event.message:
        parsed = _segment_type_and_data(segment)
        if not parsed:
            continue
        segment_type, data = parsed
        if segment_type not in {"reply", "quote"}:
            continue
        raw_id = data.get("id") or data.get("message_id")
        if raw_id is None:
            continue
        try:
            return int(raw_id)
        except (TypeError, ValueError):
            logger.warning("Cannot parse reply message id: %s", raw_id)

    reply = getattr(event, "reply", None)
    raw_reply_id = getattr(reply, "message_id", None) or getattr(reply, "id", None)
    if raw_reply_id is None and isinstance(reply, dict):
        raw_reply_id = reply.get("message_id") or reply.get("id")
    if raw_reply_id is None:
        return None
    try:
        return int(raw_reply_id)
    except (TypeError, ValueError):
        return None


def _image_url_from_data(data: dict[str, Any]) -> str | None:
    raw_url = data.get("url") or data.get("file")
    if not raw_url:
        return None
    url = str(raw_url)
    if url.startswith(("http://", "https://")):
        return url
    return None


async def download_all_images_from_event(
    event: MessageEvent,
    bot: Bot | None = None,
) -> list[bytes]:
    """Extract and download images from current, replied, and forwarded messages.

    Passing ``bot`` enables OneBot ``get_msg`` and ``get_forward_msg`` lookups, which is
    required for merged-forward chat records that only contain a forward id/resid.
    """
    image_list: list[bytes] = []
    seen_urls: set[str] = set()
    seen_forward_ids: set[str] = set()
    seen_reply_ids: set[int] = set()

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        async def download_url(url: str) -> None:
            if url in seen_urls:
                return
            seen_urls.add(url)
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("下载图片失败: %s", exc)
                return
            if resp.content:
                image_list.append(resp.content)

        async def visit_forward_id(forward_id: str) -> None:
            if not bot or not forward_id or forward_id in seen_forward_ids:
                return
            seen_forward_ids.add(forward_id)
            try:
                forward_data = await bot.get_forward_msg(id=forward_id)
            except Exception as exc:
                logger.warning("读取合并转发失败: %s", exc)
                return
            await visit_any(forward_data)

        async def visit_reply_id(message_id: int | None) -> None:
            if not bot or message_id is None or message_id in seen_reply_ids:
                return
            seen_reply_ids.add(message_id)
            try:
                message_data = await bot.get_msg(message_id=message_id)
            except Exception as exc:
                logger.debug("读取回复消息失败: %s", exc)
                return
            await visit_any(message_data)

        async def visit_cq_text(text: str) -> None:
            for match in CQ_IMAGE_RE.finditer(text):
                params = _extract_cq_params(match.group(1))
                url = _image_url_from_data(params)
                if url:
                    await download_url(url)
            for match in CQ_FORWARD_RE.finditer(text):
                params = _extract_cq_params(match.group(1))
                forward_id = params.get("id") or params.get("resid")
                if forward_id:
                    await visit_forward_id(forward_id)

        async def visit_segment(segment: Any) -> None:
            parsed = _segment_type_and_data(segment)
            if not parsed:
                if isinstance(segment, str):
                    await visit_cq_text(segment)
                elif isinstance(segment, dict):
                    await visit_any(segment)
                return

            segment_type, data = parsed
            if segment_type == "image":
                url = _image_url_from_data(data)
                if url:
                    await download_url(url)
                return

            if segment_type == "forward":
                await visit_any(data.get("content"))
                forward_id = data.get("id") or data.get("resid")
                if forward_id:
                    await visit_forward_id(str(forward_id))
                return

            if segment_type in {"reply", "quote"}:
                raw_id = data.get("id") or data.get("message_id")
                try:
                    await visit_reply_id(int(raw_id)) if raw_id is not None else None
                except (TypeError, ValueError):
                    logger.debug("Cannot parse reply segment id: %s", raw_id)
                return

            if segment_type == "node":
                await visit_any(data.get("content"))

        async def visit_any(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, Message):
                for segment in value:
                    await visit_segment(segment)
                return
            if isinstance(value, MessageSegment):
                await visit_segment(value)
                return
            if isinstance(value, str):
                await visit_cq_text(value)
                return
            if isinstance(value, list | tuple):
                for item in value:
                    await visit_any(item)
                return
            if isinstance(value, dict):
                if "messages" in value:
                    await visit_any(value.get("messages"))
                if "message" in value:
                    await visit_any(value.get("message"))
                if "content" in value:
                    await visit_any(value.get("content"))
                if "type" in value:
                    await visit_segment(value)
                return

        if event.reply:
            await visit_any(event.reply.message)
        await visit_reply_id(_extract_reply_message_id(event))

        await visit_any(event.message)
        original_message = getattr(event, "original_message", None)
        if original_message is not None and original_message is not event.message:
            await visit_any(original_message)

    return image_list

def unwrap_images(event: MessageEvent):
    '''
    从消息事件中解包 Image Segment。
    优先检查回复的消息(Reply)，然后检查当前消息。
    '''
    # 1. 检查是否有引用回复 (Reply)
    if event.reply:
        if DEBUG:
            logger.debug('[unwrap_images]: Found Reply')
        # event.reply.message 是一个 Message 对象
        for seg in event.reply.message:
            if seg.type == 'image':
                yield seg

    # 2. 检查当前消息链
    if DEBUG:
        logger.debug('[unwrap_images]: %s', event.message)
    
    for seg in event.message:
        if seg.type == 'image':
            yield seg

async def judge_type(img: MessageSegment | bytes) -> tuple[str, bytes]:
    '''
    判断图片类型，返回 (type, bytes)
    
    Args:
        img: 可以是 OneBot V11 的图片 MessageSegment，或者是已经下载好的 bytes
    '''
    imagebytes = b""

    # 情况1: 输入是 MessageSegment (OneBot V11 图片)
    if isinstance(img, MessageSegment):
        if img.type != 'image':
            raise ValueError("传入的 Segment 不是图片类型")
            
        url = img.data.get("url")
        if not url:
            raise ValueError("无法获取图片 URL")

        # 使用 httpx 异步下载图片
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=20)
            resp.raise_for_status()
            imagebytes = resp.content
            
    # 情况2: 输入已经是 bytes
    elif isinstance(img, bytes):
        imagebytes = img
    else:
        raise TypeError(f"不支持的输入类型: {type(img)}")

    # 使用 PIL 判断格式
    try:
        with PILImage.open(io.BytesIO(imagebytes)) as im:
            # 获取格式 (如 JPEG, PNG, GIF)
            imagetype = im.format.lower()
            # 统一 jpeg 为 jpg
            if imagetype == 'jpeg':
                imagetype = 'jpg'
            return imagetype, imagebytes
    except Exception as e:
        if DEBUG:
            logger.debug("[judge_type] Error: %s", e)
        # 如果 PIL 无法识别，默认回退到 jpg 或抛出异常，视需求而定
        return "jpg", imagebytes

def guess_extension(data: bytes) -> str:
    """简单判断图片类型，返回不带点的后缀名 (如 jpg, png)"""
    ext = imghdr.what(None, data)
    if ext == 'jpeg':
        return 'jpg'
    if ext:
        return ext
    return 'jpg' # 默认回退
