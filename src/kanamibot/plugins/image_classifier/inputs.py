from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

import httpx
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.log import logger

from .exclusions import bot_authored, bot_ids, is_command_message


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
    bot: Bot,
    message: Message,
    client: httpx.AsyncClient,
    max_bytes: int,
    local_roots: list[Path] | None = None,
) -> AsyncIterator[tuple[bytes, dict]]:
    """Visit images and merged forwards. Quoted old messages are not new images."""
    seen_forwards: set[str] = set()
    image_index = 0
    visited = 0
    ids = bot_ids(bot)

    def node_source(value: dict) -> dict:
        sender = value.get("sender") or {}
        if not isinstance(sender, dict):
            sender = {}
        return {
            "user_id": sender.get("user_id")
            or sender.get("uin")
            or value.get("user_id")
            or value.get("uin"),
            "sender_name": sender.get("card")
            or sender.get("nickname")
            or value.get("nickname")
            or value.get("name"),
            "message_time": value.get("time"),
        }

    async def visit(
        value: Any, depth: int = 0, origin: dict | None = None
    ) -> AsyncIterator[tuple[bytes, dict]]:
        nonlocal image_index, visited
        if depth > 16:
            logger.warning("[image_classifier] skipped excessively nested forwarded message")
            return
        if isinstance(value, str):
            value = Message(value)
        if isinstance(value, list | tuple | Message):
            if is_command_message(value):
                return
            for item in value:
                async for image in visit(item, depth, origin):
                    yield image
            return
        if isinstance(value, MessageSegment):
            kind, data = value.type, value.data
        elif isinstance(value, dict):
            if bot_authored(value, ids):
                return
            kind = value.get("type")
            data = value.get("data", {})
            if kind is None:
                for key in ("messages", "message", "content"):
                    if key in value:
                        context = node_source(value) if key != "messages" else origin
                        async for image in visit(value[key], depth + 1, context):
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
                info = {}
                if data.get("file") and not str(data["file"]).startswith(("http://", "https://")):
                    try:
                        info = await bot.get_image(file=str(data["file"]))
                    except Exception:
                        pass  # A valid segment URL can still work.
                local = await asyncio.to_thread(
                    read_local_image,
                    [info.get("file"), info.get("path"), data.get("file")],
                    local_roots or [],
                    max_bytes,
                )
                details = {"image_index": image_index, "forwarded": depth > 0}
                if depth > 0 and origin:
                    details["forwarded_source"] = origin.copy()
                if local:
                    content, path = local
                    yield content, details | {"local_path": str(path)}
                    return
                direct = str(data.get("file") or "")
                url = str(
                    data.get("url")
                    or (direct if direct.startswith(("http://", "https://")) else info.get("url"))
                    or ""
                )
                content = await download_image(client, url, max_bytes)
                yield content, details
            except Exception as exc:
                # Do not log signed QQ URLs or response bodies.
                logger.warning(
                    "[image_classifier] image {} download failed ({})",
                    image_index,
                    type(exc).__name__,
                )
        elif kind == "node":
            if bot_authored(data, ids):
                return
            async for image in visit(data.get("content"), depth + 1, node_source(data)):
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


def read_local_image(candidates: list, roots: list[Path], max_bytes: int):
    """Only configured, resolved local roots are trusted, including symlink targets."""
    allowed = [root.resolve() for root in roots]
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate:
            continue
        if candidate.startswith("file:///"):
            candidate = url2pathname(unquote(urlsplit(candidate).path))
        path = Path(candidate)
        if not path.is_absolute():
            continue
        try:
            path = path.resolve(strict=True)
            if not any(path.is_relative_to(root) for root in allowed) or not path.is_file():
                continue
            with path.open("rb") as file:
                content = file.read(max_bytes + 1)
            if 0 < len(content) <= max_bytes:
                return content, path
        except OSError:
            continue
    return None
