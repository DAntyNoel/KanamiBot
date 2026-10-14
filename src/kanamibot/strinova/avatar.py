from __future__ import annotations

import base64
import io
import os
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from PIL import Image, UnidentifiedImageError

from .client import DEFAULT_AVATAR_URL_TEMPLATE
from .resources import CACHE_DIR, resource_pack

MAX_AVATAR_BYTES = 2 * 1024 * 1024
DEFAULT_CACHE_DIR = CACHE_DIR / "avatars"


class AvatarError(ValueError):
    pass


def image_data_uri(payload: bytes) -> str:
    if not payload or len(payload) > MAX_AVATAR_BYTES:
        raise AvatarError("头像资源大小不符合预期。")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
                image.format
            )
            if mime is None or max(image.size) > 2048:
                raise AvatarError("头像资源格式不符合预期。")
            image.verify()
    except (OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise AvatarError("头像资源无法解码。") from exc
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


async def load_avatar(
    icon: int,
    *,
    url_template: str = DEFAULT_AVATAR_URL_TEMPLATE,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    if not isinstance(icon, int) or isinstance(icon, bool) or not 0 < icon <= 2**32 - 1:
        raise AvatarError("API 未提供有效的头像编号。")
    local = resource_pack().avatar_path(icon)
    if local is not None:
        try:
            return image_data_uri(local.read_bytes())
        except (OSError, AvatarError):
            pass
    try:
        url = url_template.format(icon=icon)
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise AvatarError("头像资源地址无效。")
        if parsed.username or parsed.password:
            raise AvatarError("头像资源地址不能包含凭据。")
    except (KeyError, ValueError) as exc:
        raise AvatarError("头像资源地址无效。") from exc
    # Include the source in the cache key so changing the configured provider takes effect.
    cached = cache_dir / f"{icon}-{sha256(url.encode()).hexdigest()[:12]}.image"
    try:
        if cached.stat().st_size <= MAX_AVATAR_BYTES:
            return image_data_uri(cached.read_bytes())
    except (OSError, AvatarError):
        pass
    try:
        async with httpx.AsyncClient(
            timeout=5, follow_redirects=True, transport=transport
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                payload = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    payload.extend(chunk)
                    if len(payload) > MAX_AVATAR_BYTES:
                        raise AvatarError("头像资源超过大小限制。")
    except httpx.HTTPError as exc:
        raise AvatarError("头像资源暂不可用。") from exc
    content = bytes(payload)
    data_uri = image_data_uri(content)
    temporary = cached.with_suffix(f".tmp.{uuid4().hex}")
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(content)
        os.replace(temporary, cached)
    except OSError:
        # A read-only cache must not prevent displaying an already downloaded avatar.
        pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return data_uri
