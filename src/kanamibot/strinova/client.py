from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from kanamibot.core.timing import timed

from .query import QueryError, numeric_id

DEFAULT_API_URL = "http://127.0.0.1:19080"
LAN_API_URL = "http://10.38.20.220:19080"
HEALTH_CHECK_ATTEMPTS = 5
HEALTH_RETRY_DELAY_SECONDS = 5
DEFAULT_AVATAR_URL_TEMPLATE = (
    "https://cdn.jsdelivr.net/gh/Celest151/StrinovaData@main/"
    "PaperMan/UI/Atlas/DynamicResource/IdCard/Appearance/T_Dynamic_IdCard_{icon}_icon.png"
)


class APIError(QueryError):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class ServiceUnavailable(APIError):
    def __init__(self) -> None:
        super().__init__("卡拉彼丘战绩服务暂不可用，全部查询功能已暂停，恢复后自动可用。")


class IdentityMismatch(APIError):
    pass


@dataclass(frozen=True)
class Settings:
    base_url: str = DEFAULT_API_URL
    timeout: float = 90.0
    browser_channel: str | None = None
    browser_executable: str | None = None
    avatar_url_template: str = DEFAULT_AVATAR_URL_TEMPLATE
    api_key: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise QueryError("STRINOVA_API_URL 必须是 http(s) 地址。")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise QueryError("API 地址不能包含凭据、查询参数或片段。")
        if not math.isfinite(self.timeout) or not 1 <= self.timeout <= 120:
            raise QueryError("API 超时必须为 1–120 秒。")
        if any(ord(char) < 32 or ord(char) > 126 for char in self.api_key):
            raise QueryError("STRINOVA_API_KEY 必须为不含控制字符的 ASCII 文本。")

    @classmethod
    def from_env(cls) -> Settings:
        try:
            return cls(
                base_url=os.getenv("STRINOVA_API_URL", DEFAULT_API_URL).rstrip("/"),
                timeout=float(os.getenv("STRINOVA_API_TIMEOUT", "90")),
                api_key=os.getenv("STRINOVA_API_KEY", ""),
                browser_channel=os.getenv("STRINOVA_BROWSER_CHANNEL") or None,
                browser_executable=os.getenv("STRINOVA_BROWSER_EXECUTABLE") or None,
                avatar_url_template=os.getenv("STRINOVA_AVATAR_URL_TEMPLATE")
                or DEFAULT_AVATAR_URL_TEMPLATE,
            )
        except ValueError as exc:
            raise QueryError(f"战绩查询配置无效：{exc}") from exc


class StrinovaClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        base_url = settings.base_url.rstrip("/")
        self.api_urls = (
            (DEFAULT_API_URL, LAN_API_URL)
            if base_url in (DEFAULT_API_URL, LAN_API_URL)
            else (base_url,)
        )
        # Local/LAN API requests must not inherit HTTP_PROXY/HTTPS_PROXY.
        self.http = httpx.AsyncClient(
            base_url=self.api_urls[0] + "/",
            timeout=settings.timeout,
            trust_env=False,
            transport=transport,
            headers={"X-API-Key": settings.api_key} if settings.api_key else None,
        )
        self.health_timeout = min(settings.timeout, 5.0)

    async def __aenter__(self) -> StrinovaClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.http.aclose()

    @timed()
    async def check_health(self) -> None:
        # Four exponential waits (75s) plus five checks (at most 25s) stay under two minutes.
        for attempt in range(HEALTH_CHECK_ATTEMPTS):
            try:
                for index, base_url in enumerate(self.api_urls):
                    try:
                        await self._check_health_once(base_url)
                    except ServiceUnavailable:
                        if index == len(self.api_urls) - 1:
                            raise
                    else:
                        self.http.base_url = base_url + "/"
                        return
            except ServiceUnavailable:
                if attempt == HEALTH_CHECK_ATTEMPTS - 1:
                    raise
            await asyncio.sleep(HEALTH_RETRY_DELAY_SECONDS * 2**attempt)

    @timed()
    async def _check_health_once(self, base_url: str) -> None:
        timeout = self.health_timeout / len(self.api_urls)
        try:
            # Bound the entire check, not each separate HTTP timeout phase.
            async with asyncio.timeout(timeout):
                response = await self.http.get(base_url + "/health", timeout=timeout)
                if response.status_code == 401:
                    raise APIError("战绩 API 鉴权失败，请检查 STRINOVA_API_KEY。")
                response.raise_for_status()
                health = response.json()
        except APIError:
            raise
        except (TimeoutError, httpx.HTTPError, ValueError) as exc:
            raise ServiceUnavailable() from exc
        if not isinstance(health, dict) or health.get("ok") is not True:
            raise ServiceUnavailable()
        # These fields describe the bridge needed by player queries, when exposed by /health.
        if any(health.get(flag) is False for flag in ("hooked", "lua_ready", "game_tick_seen")):
            raise ServiceUnavailable()

    @timed()
    async def get(
        self, path: str, *, allow_missing_code: bool = False, **params: int | str
    ) -> dict[str, Any]:
        await self.check_health()
        try:
            response = await self.http.get(path.lstrip("/"), params=params)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise APIError("API 请求超时，请稍后重试。") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise APIError("战绩 API 鉴权失败，请检查 STRINOVA_API_KEY。") from exc
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
        errors = {
            11: "游戏大厅连接暂未就绪，请稍后重试（业务代码 11）。",
            8603: "开启了隐私保护",
            8609: "不属于同一战区，无法查看对方战绩（业务代码 8609）。",
            8610: "查看过于频繁，请稍后重试（业务代码 8610）。",
            8611: "查看他人历史赛季的当日次数已满（业务代码 8611）。",
            8704: "回放文件版本不匹配（业务代码 8704）。",
        }
        if type(code) is int and code in errors:
            raise APIError(errors[code], code=code)
        if not (allow_missing_code and "code" not in data) and (type(code) is not int or code != 0):
            raise APIError(f"数据暂不可用或未公开（业务代码 {code}）。", code=code)
        return data

    @timed()
    async def me(self) -> dict:
        return await self.get("v1/me", allow_missing_code=True)

    @timed()
    async def personalization(self) -> dict:
        return await self.get("v1/me/personalization", allow_missing_code=True)

    @timed()
    async def icons(self) -> dict:
        return await self.get("v1/career/icons")

    @timed()
    async def seasons(self) -> dict:
        return await self.get("v1/career/seasons", allow_missing_code=True)

    @timed()
    async def replay(self, room_id: str) -> dict:
        room_id = numeric_id(room_id, "房间 ID")
        data = await self.get(f"v1/replays/{room_id}")
        if str(data.get("room_id")) != room_id:
            raise IdentityMismatch("录像房间 ID 与请求不一致。")
        url = data.get("download_url")
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            valid = parsed and parsed.scheme in ("http", "https") and parsed.hostname
        except ValueError:
            valid = False
        if not valid or parsed.username or parsed.password or any(char.isspace() for char in url):
            raise APIError("该对局未返回有效的录像下载地址。")
        return data

    @timed()
    async def search(self, *, nick: str | None = None, player_id: str | None = None) -> dict:
        if (nick is None) == (player_id is None):
            raise QueryError("搜索必须提供昵称或玩家 ID 中的一项。")
        params = (
            {"nick": nick}
            if nick is not None
            else {"player_id": numeric_id(player_id or "", "玩家 ID")}
        )
        return await self.get("v1/friends/search", **params)

    @timed()
    async def player(self, player_id: str, suffix: str = "", **params: int) -> dict:
        player_id = numeric_id(player_id, "玩家 ID")
        return await self.get(f"v1/players/{player_id}{suffix}", **params)

    @timed()
    async def profile(self, player_id: str) -> dict:
        return await self.player(player_id)

    @timed()
    async def status(self, player_id: str) -> dict:
        return await self.player(player_id, "/status")

    @timed()
    async def career(self, player_id: str) -> dict:
        return await self.player(player_id, "/career")

    @timed()
    async def card(self, player_id: str) -> dict:
        return await self.player(player_id, "/card")

    @timed()
    async def battle_data(self, player_id: str, season_id: int, season_type: int) -> dict:
        return await self.player(
            player_id, "/career/battle-data", season_id=season_id, season_type=season_type
        )

    @timed()
    async def history(self, player_id: str, season_id: int) -> dict:
        return await self.player(player_id, "/career/history", season_id=season_id)

    @timed()
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

    @timed()
    async def match(self, player_id: str, room_id: str) -> dict:
        return await self.player(player_id, f"/standings/{numeric_id(room_id, '房间 ID')}")
