from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

import httpx

from .client import BackendError

API = "https://prts.wiki/api.php"


class PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "br", "tr", "li", "h2", "h3", "h4"} and not self.hidden:
            self.parts.append("\n")
        elif tag in {"td", "th"} and not self.hidden:
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def text(self) -> str:
        return "\n".join(
            line.strip(" |\t") for line in "".join(self.parts).splitlines() if line.strip(" |\t")
        )


class Wiki:
    def __init__(self, *, transport=None) -> None:
        self.transport = transport

    async def request(self, **params) -> dict:
        try:
            async with httpx.AsyncClient(
                timeout=30,
                transport=self.transport,
                trust_env=False,
                headers={"User-Agent": "KanamiBot Arknights query"},
            ) as client:
                response = await client.get(API, params={"format": "json", **params})
                response.raise_for_status()
                value = response.json()
                if "error" in value:
                    raise BackendError("PRTS Wiki 暂时无法查询这条资料。")
                return value
        except (httpx.HTTPError, ValueError) as exc:
            raise BackendError("PRTS Wiki 暂时不可用，请稍后重试。") from exc

    async def image(self, url: str) -> tuple[bytes, str]:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "media.prts.wiki"
            or parsed.port not in {None, 443}
            or parsed.username
        ):
            raise BackendError("PRTS 图片地址无效。")
        try:
            async with (
                httpx.AsyncClient(timeout=20, transport=self.transport, trust_env=False) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                mime = response.headers.get("content-type", "").split(";", 1)[0]
                if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                    raise BackendError("PRTS 返回的头像不是受支持的图片。")
                result = bytearray()
                async for chunk in response.aiter_bytes():
                    result.extend(chunk)
                    if len(result) > 5 * 1024 * 1024:
                        raise BackendError("PRTS 图片过大。")
                return bytes(result), mime
        except httpx.HTTPError as exc:
            raise BackendError("PRTS 头像暂时无法下载，请稍后重试。") from exc

    async def search(self, name: str, *, reclamation: bool = False) -> list[dict]:
        title = "生息演算" if reclamation and not name else name
        query = name
        scope = ("生息演算", "沙洲遗闻", "重启锚点", "沙中之火")
        # Resolve an exact title first, including MediaWiki redirects.
        exact = await self.request(action="query", titles=title, redirects=1)
        pages = exact.get("query", {}).get("pages", {})
        result = []
        for row in pages.values():
            if "missing" not in row and (not reclamation or row["title"].startswith(scope)):
                result.append({"id": row["title"], "name": row["title"], "type": "Wiki"})
        if result:
            return result
        data = await self.request(action="query", list="search", srsearch=query, srlimit=30)
        for row in data.get("query", {}).get("search", []):
            if reclamation and not row["title"].startswith(scope):
                continue
            if row["title"].endswith("/sandbox"):
                continue
            result.append(
                {
                    "id": row["title"],
                    "name": row["title"],
                    "type": "Wiki",
                    "snippet": html.unescape(re.sub(r"<[^>]*>", "", row.get("snippet", ""))),
                }
            )
        return result

    async def detail(self, title: str, keyword: str = "") -> str:
        payload = await self.request(
            action="parse", page=title, prop="text", redirects=1, disableeditsection=1
        )
        parser = PlainText()
        parser.feed(payload["parse"]["text"]["*"])
        lines = parser.text().splitlines()
        if keyword and keyword not in title:
            hits = [index for index, line in enumerate(lines) if keyword in line]
            if hits:
                selected = sorted(
                    {
                        index
                        for hit in hits[:6]
                        for index in range(max(hit - 1, 0), min(hit + 4, len(lines)))
                    }
                )
                lines = [lines[index] for index in selected]
        text = "\n".join(lines)
        if len(text) > 2200:
            text = text[:2200] + "\n……完整内容见原文。"
        return (
            f"{title}\n{text}\n\n来源：PRTS Wiki\nhttps://prts.wiki/w/{quote(title)}"
            "\nPRTS 内容许可：CC BY-SA 3.0"
        )
