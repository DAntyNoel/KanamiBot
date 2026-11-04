from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_PAGE_BYTES = 4 * 1024 * 1024
UPLOAD_DOMAINS = (
    "saucenao.com",
    "iqdb.org",
    "trace.moe",
    "google.com",
    "baidu.com",
    "bing.com",
    "yandex.com",
    "yandex.ru",
    "catbox.moe",
)


def upload_allowed(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == domain or host.endswith("." + domain) for domain in UPLOAD_DOMAINS)


async def validate_url(url: str) -> None:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.port not in {None, 80, 443}
    ):
        raise ValueError("仅支持公开 HTTP(S) 地址和标准端口")
    addresses = await asyncio.get_running_loop().getaddrinfo(
        parts.hostname,
        parts.port or (443 if parts.scheme == "https" else 80),
        type=socket.SOCK_STREAM,
    )
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("不支持本机、内网或保留地址")


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    method: str = "GET",
    limit: int = MAX_PAGE_BYTES,
    **kwargs,
) -> httpx.Response:
    """Bound downloads; validate every redirect and never replay uploads to another host."""
    for _ in range(6):
        await validate_url(url)
        if method != "GET" and not upload_allowed(url):
            raise ValueError("图片上传目标不在引擎白名单")
        async with client.stream(method, url, follow_redirects=False, **kwargs) as response:
            if response.is_redirect:
                target = str(response.url.join(response.headers["location"]))
                if response.status_code in {301, 302, 303}:
                    method, kwargs = "GET", {}
                elif urlsplit(target).hostname != urlsplit(url).hostname:
                    raise ValueError("禁止跨站重发上传内容")
                url = target
                continue
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > limit:
                    raise ValueError("响应内容超过大小限制")
            headers = dict(response.headers)
            # aiter_bytes already decompresses; retaining Content-Encoding decodes twice.
            headers.pop("content-encoding", None)
            headers.pop("content-length", None)
            return httpx.Response(
                response.status_code,
                headers=headers,
                content=bytes(body),
                request=response.request,
            )
    raise ValueError("跳转次数过多")
