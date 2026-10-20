from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .query import QueryError, numeric_id

DEFAULT_API_URL = "http://10.38.20.220:19080"
HEALTH_CHECK_ATTEMPTS = 5
HEALTH_RETRY_DELAY_SECONDS = 5
DEFAULT_AVATAR_URL_TEMPLATE = (
    "https://cdn.jsdelivr.net/gh/Celest151/StrinovaData@main/"
    "PaperMan/UI/Atlas/DynamicResource/IdCard/Appearance/T_Dynamic_IdCard_{icon}_icon.png"
)


class APIError(QueryError):
    pass


class ServiceUnavailable(APIError):
    def __init__(self) -> None:
        super().__init__("卡拉彼丘战绩服务暂不可用，全部查询功能已暂停，恢复后自动可用。")


@dataclass(frozen=True)
class Settings:
    base_url: str = DEFAULT_API_URL
    timeout: float = 35.0
    browser_channel: str | None = None
    browser_executable: str | None = None
    avatar_url_template: str = DEFAULT_AVATAR_URL_TEMPLATE

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise QueryError("STRINOVA_API_URL 必须是 http(s) 地址。")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise QueryError("API 地址不能包含凭据、查询参数或片段。")
        if not math.isfinite(self.timeout) or not 1 <= self.timeout <= 120:
            raise QueryError("API 超时必须为 1–120 秒。")

    @classmethod
    def from_env(cls) -> Settings:
        try:
            return cls(
                base_url=os.getenv("STRINOVA_API_URL", DEFAULT_API_URL).rstrip("/"),
                timeout=float(os.getenv("STRINOVA_API_TIMEOUT", "35")),
                browser_channel=os.getenv("STRINOVA_BROWSER_CHANNEL") or None,
                browser_executable=os.getenv("STRINOVA_BROWSER_EXECUTABLE") or None,
                avatar_url_template=os.getenv("STRINOVA_AVATAR_URL_TEMPLATE")
                or DEFAULT_AVATAR_URL_TEMPLATE,
            )
        except ValueError as exc:
            raise QueryError(f"战绩查询配置无效：{exc}") from exc


class StrinovaClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        # The supplied API is on the LAN: do not inherit HTTP_PROXY/HTTPS_PROXY.
        self.http = httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/") + "/",
            timeout=settings.timeout,
            trust_env=False,
            transport=transport,
        )
        self.health_timeout = min(settings.timeout, 5.0)

    async def __aenter__(self) -> StrinovaClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.http.aclose()

    async def check_health(self) -> None:
        # Four exponential waits (75s) plus five checks (at most 25s) stay under two minutes.
        for attempt in range(HEALTH_CHECK_ATTEMPTS):
            try:
                await self._check_health_once()
                return
            except ServiceUnavailable:
                if attempt == HEALTH_CHECK_ATTEMPTS - 1:
                    raise
            await asyncio.sleep(HEALTH_RETRY_DELAY_SECONDS * 2**attempt)

    async def _check_health_once(self) -> None:
        try:
            # Bound the entire check, not each separate HTTP timeout phase.
            async with asyncio.timeout(self.health_timeout):
                response = await self.http.get("health", timeout=self.health_timeout)
                response.raise_for_status()
                health = response.json()
        except (TimeoutError, httpx.HTTPError, ValueError) as exc:
            raise ServiceUnavailable() from exc
        if not isinstance(health, dict) or health.get("ok") is not True:
            raise ServiceUnavailable()
        # These fields describe the bridge needed by player queries, when exposed by /health.
        if any(health.get(flag) is False for flag in ("hooked", "lua_ready", "game_tick_seen")):
            raise ServiceUnavailable()

    async def get(self, path: str, **params: int | str) -> dict[str, Any]:
        await self.check_health()
        try:
            response = await self.http.get(path.lstrip("/"), params=params)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise APIError("API 请求超时，请稍后重试。") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 504:
                raise APIError("查询超时，后端服务无响应") from exc
            raise APIError(f"API 返回 HTTP {exc.response.status_code}。") from exc
        except httpx.RequestError as exc:
            raise APIError("无法连接战绩 API，请检查网络和 STRINOVA_API_URL。") from exc
        try:
            envelope = response.json()
        except ValueError as exc:
            raise APIError("API 返回了非 JSON 数据。") from exc
        if not isinstance(envelope, dict):
            raise APIError("API 数据结构不符合预期。")
        nested = envelope.get("data")
        raw_error = envelope.get("error") or (
            nested.get("error") if isinstance(nested, dict) else None
        )
        if isinstance(raw_error, str) and "career request is still pending" in raw_error:
            raise APIError("游戏内生涯请求仍在处理中，请稍后重试。")
        if envelope.get("ok") is not True:
            raise APIError("API 查询失败或服务端查询已过期，请稍后重试。")
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise APIError("API 数据结构不符合预期。")
        code = data.get("code")
        if code == 8603:
            raise APIError("开启了隐私保护")
        if code != 0:
            raise APIError(f"数据暂不可用或未公开（业务代码 {code}）。")
        return data

    async def search(self, *, nick: str | None = None, player_id: str | None = None) -> dict:
        if (nick is None) == (player_id is None):
            raise QueryError("搜索必须提供昵称或玩家 ID 中的一项。")
        params = (
            {"nick": nick}
            if nick is not None
            else {"player_id": numeric_id(player_id or "", "玩家 ID", 2**32 - 1)}
        )
        return await self.get("v1/friends/search", **params)

    async def player(self, player_id: str, suffix: str = "", **params: int) -> dict:
        player_id = numeric_id(player_id, "玩家 ID", 2**32 - 1)
        return await self.get(f"v1/players/{player_id}{suffix}", **params)

    async def profile(self, player_id: str) -> dict:
        return await self.player(player_id)

    async def status(self, player_id: str) -> dict:
        return await self.player(player_id, "/status")

    async def career(self, player_id: str) -> dict:
        return await self.player(player_id, "/career")

    async def battle_data(self, player_id: str, season_id: int, season_type: int) -> dict:
        return await self.player(
            player_id, "/career/battle-data", season_id=season_id, season_type=season_type
        )

    async def history(self, player_id: str, season_id: int) -> dict:
        return await self.player(player_id, "/career/history", season_id=season_id)

    async def standings(
        self, player_id: str, index: int, game_mode: int, room_mode_type: int, play_mode: int
    ) -> dict:
        return await self.player(
            player_id,
            "/standings",
            index=index,
            game_mode=game_mode,
            room_mode_type=room_mode_type,
            play_mode=play_mode,
        )

    async def match(self, player_id: str, room_id: str) -> dict:
        return await self.player(player_id, f"/standings/{numeric_id(room_id, '房间 ID')}")
