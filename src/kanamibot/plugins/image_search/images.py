from __future__ import annotations

import asyncio
import base64
import warnings
from io import BytesIO

import httpx
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from PIL import Image, ImageOps

from kanamibot.core.utils.image import unwrap_images

from .network import MAX_IMAGE_BYTES, fetch
from .results import Result, reply_entries


def prepare_image(data: bytes, *, max_side: int = 1600) -> tuple[bytes, str]:
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片不能超过 15 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if original.width * original.height > 40_000_000:
                    raise ValueError("图片分辨率不能超过 4000 万像素")
                image = ImageOps.exif_transpose(original)
                image.thumbnail((max_side, max_side))
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                output = BytesIO()
                background.save(output, format="JPEG", quality=90)
                return output.getvalue(), "image/jpeg"
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("图片分辨率过大") from exc
    except OSError as exc:
        raise ValueError("无法读取这张图片，请发送常见格式的原图") from exc


async def read_image(bot: Bot, event: MessageEvent, argument: str) -> tuple[bytes, str] | None:
    """Quoted image first, then current image; only explicit URL arguments become public URLs."""
    public_url = ""
    if argument:
        source = argument
        public_url = argument
    else:
        segments = list(unwrap_images(event))
        if not segments:
            reply_id = next((s.data.get("id") for s in event.message if s.type == "reply"), None)
            if reply_id:
                reply = await bot.get_msg(message_id=int(reply_id))
                from nonebot.adapters.onebot.v11 import Message, MessageSegment

                content = reply.get("message", [])
                if isinstance(content, str):
                    content = Message(content)
                segments = [MessageSegment(**s) if isinstance(s, dict) else s for s in content]
                segments = [s for s in segments if s.type == "image"]
        if not segments:
            return None
        segment = segments[0]
        source = str(segment.data.get("url") or segment.data.get("file") or "")
        if not source.startswith(("http://", "https://", "base64://")):
            info = await bot.get_image(file=str(segment.data.get("file", "")))
            source = str(info.get("url", ""))
    if source.startswith("base64://"):
        if len(source) > MAX_IMAGE_BYTES * 4 // 3 + 20:
            raise ValueError("图片不能超过 15 MB")
        data = base64.b64decode(source[9:], validate=True)
    else:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await fetch(client, source, limit=MAX_IMAGE_BYTES)
            data = response.content
    return data, public_url


async def result_previews(result: Result) -> list[tuple[str, bytes]]:
    """Fetch a thumbnail per displayed source; keep source keys for correct pairing."""
    sources = {source for _, source in reply_entries(result)}
    pool = sorted(result.candidates, key=lambda c: -(c.similarity or 0))
    selected, seen = [], set()
    for candidate in pool:
        if candidate.source in sources and candidate.preview and candidate.source not in seen:
            seen.add(candidate.source)
            selected.append(candidate)
    async with httpx.AsyncClient(timeout=8) as client:

        async def download(candidate):
            try:
                async with asyncio.timeout(10):
                    url = candidate.preview
                    if url.startswith("data:image/") and ";base64," in url:
                        encoded = url.split(";base64,", 1)[1]
                        if len(encoded) > 400_000:
                            return None
                        data = base64.b64decode(encoded, validate=True)
                    else:
                        response = await fetch(client, url, limit=2 * 1024 * 1024)
                        data = response.content
                    data = await asyncio.to_thread(prepare_preview, data)
                    return candidate.source, data
            except Exception:
                return None

        return [row for row in await asyncio.gather(*(download(c) for c in selected)) if row]


def prepare_preview(data: bytes) -> bytes:
    return prepare_image(data, max_side=512)[0]
