from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import httpx
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.log import logger


async def download_image(client: httpx.AsyncClient, url: str, max_bytes: int) -> bytes:
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("Image has no HTTP(S) URL")
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        length = response.headers.get("content-length")
        if length and int(length) > max_bytes:
            raise ValueError("Image exceeds download limit")
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > max_bytes:
                raise ValueError("Image exceeds download limit")
        if not content:
            raise ValueError("Empty image response")
        return bytes(content)


async def message_images(
    bot: Bot, message: Message, client: httpx.AsyncClient, max_bytes: int
) -> AsyncIterator[tuple[bytes, dict]]:
    """Visit images and merged forwards. Quoted old messages are not new images."""
    seen_forwards: set[str] = set()
    image_index = 0
    visited = 0

    async def visit(value: Any, depth: int = 0) -> AsyncIterator[tuple[bytes, dict]]:
        nonlocal image_index, visited
        if depth > 16:
            logger.warning("[image_classifier] skipped excessively nested forwarded message")
            return
        if isinstance(value, str):
            value = Message(value)
        if isinstance(value, list | tuple | Message):
            for item in value:
                async for image in visit(item, depth):
                    yield image
            return
        if isinstance(value, MessageSegment):
            kind, data = value.type, value.data
        elif isinstance(value, dict):
            kind = value.get("type")
            data = value.get("data", {})
            if kind is None:
                for key in ("messages", "message", "content"):
                    if key in value:
                        async for image in visit(value[key], depth + 1):
                            yield image
                return
        else:
            return
        visited += 1
        if visited > 10_000:
            if visited == 10_001:
                logger.warning("[image_classifier] forwarded message segment limit reached")
            return
        if not isinstance(data, dict):
            return
        if kind == "image":
            image_index += 1
            try:
                url = str(data.get("url") or data.get("file") or "")
                if not url.startswith(("http://", "https://")) and data.get("file"):
                    info = await bot.get_image(file=str(data["file"]))
                    url = str(info.get("url") or "")
                content = await download_image(client, url, max_bytes)
                yield content, {"image_index": image_index, "forwarded": depth > 0}
            except Exception as exc:
                # Do not log signed QQ URLs or response bodies.
                logger.warning(
                    "[image_classifier] image {} download failed ({})",
                    image_index,
                    type(exc).__name__,
                )
        elif kind == "node":
            async for image in visit(data.get("content"), depth + 1):
                yield image
        elif kind == "forward":
            content = data.get("content")
            if content:
                async for image in visit(content, depth + 1):
                    yield image
                return
            forward_id = str(data.get("id") or data.get("resid") or "")
            if not forward_id or forward_id in seen_forwards:
                return
            seen_forwards.add(forward_id)
            try:
                forwarded = await bot.get_forward_msg(id=forward_id)
            except Exception as exc:
                logger.warning("[image_classifier] forward lookup failed ({})", type(exc).__name__)
                return
            async for image in visit(forwarded, depth + 1):
                yield image

    async for item in visit(message):
        yield item
