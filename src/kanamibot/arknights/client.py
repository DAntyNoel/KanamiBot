from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx


class BackendError(RuntimeError):
    """An unavailable service or an invalid MCP response."""


def decode_tool(result: dict) -> dict:
    if result.get("isError"):
        raise BackendError("Amiya MCP 查询失败，请稍后重试。")
    payload = result.get("structuredContent")
    if payload is None:
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    payload = json.loads(block["text"])
                    break
                except (ValueError, KeyError):
                    continue
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError as exc:
            raise BackendError("Amiya MCP 返回了无法读取的资料。") from exc
    if not isinstance(payload, dict):
        raise BackendError("Amiya MCP 返回了无法读取的资料。")
    if payload.get("message") and not payload.get("data"):
        raise BackendError(str(payload["message"])[:200])
    return payload


class MCP:
    """One bounded Streamable HTTP session per query, with JSON and SSE support."""

    def __init__(self, url: str, timeout: float = 90, *, transport=None) -> None:
        self.url = url
        self.timeout = timeout
        self.transport = transport

    @asynccontextmanager
    async def session(self):
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            trust_env=False,
            headers={"Accept": "application/json, text/event-stream"},
        ) as client:
            session = Session(client, self.url)
            try:
                init = await session.rpc(
                    "initialize",
                    {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "KanamiBot-Arknights", "version": "0.1.0"},
                    },
                )
                client.headers["MCP-Protocol-Version"] = init["protocolVersion"]
                await session.rpc("notifications/initialized", notification=True)
                yield session
            except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
                raise BackendError("Amiya MCP 暂时不可用，请稍后重试。") from exc
            finally:
                if client.headers.get("Mcp-Session-Id"):
                    try:
                        await client.delete(self.url, timeout=3)
                    except httpx.HTTPError:
                        pass

    async def image(self, url: str) -> bytes:
        url = urljoin(self.url, url)
        base, target = urlsplit(self.url), urlsplit(url)
        # Only fetch cards served by the configured backend. No remote arbitrary URLs.
        if (base.scheme, base.hostname, base.port) != (target.scheme, target.hostname, target.port):
            raise BackendError("后端图片地址与配置地址不一致，请管理员检查 BaseUrl。")
        async with httpx.AsyncClient(
            timeout=self.timeout, trust_env=False, transport=self.transport
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                if not response.headers.get("content-type", "").startswith("image/"):
                    raise BackendError("后端返回的卡片不是图片。")
                result = bytearray()
                async for chunk in response.aiter_bytes():
                    result.extend(chunk)
                    if len(result) > 20 * 1024 * 1024:
                        raise BackendError("查询卡片过大，请缩小查询范围。")
                return bytes(result)


class Session:
    def __init__(self, client: httpx.AsyncClient, url: str) -> None:
        self.client, self.url = client, url
        self.index = 0

    async def rpc(self, method: str, params: dict | None = None, *, notification=False) -> Any:
        self.index += 1
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notification:
            body["id"] = self.index
        async with self.client.stream("POST", self.url, json=body) as response:
            response.raise_for_status()
            if session_id := response.headers.get("Mcp-Session-Id"):
                self.client.headers["Mcp-Session-Id"] = session_id
            if notification:
                return None
            size = 0
            if "text/event-stream" in response.headers.get("content-type", ""):
                lines = []
                async for line in response.aiter_lines():
                    size += len(line.encode())
                    if size > 8 * 1024 * 1024:
                        raise BackendError("查询结果过大。")
                    if line.startswith("data:"):
                        lines.append(line[5:].lstrip())
                    elif not line and lines:
                        message = json.loads("\n".join(lines))
                        lines = []
                        if message.get("id") == self.index:
                            return self._result(message)
                if lines:
                    message = json.loads("\n".join(lines))
                    if message.get("id") == self.index:
                        return self._result(message)
            else:
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 8 * 1024 * 1024:
                        raise BackendError("查询结果过大。")
                message = json.loads(chunks)
                if message.get("id") == self.index:
                    return self._result(message)
        raise BackendError("Amiya MCP 未返回本次查询结果。")

    @staticmethod
    def _result(message: dict) -> dict:
        if "error" in message:
            raise BackendError("Amiya MCP 不支持本次查询或服务尚未就绪。")
        return message["result"]

    async def call(self, tool: str, arguments: dict) -> dict:
        return decode_tool(await self.rpc("tools/call", {"name": tool, "arguments": arguments}))
