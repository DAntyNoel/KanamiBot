from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class Config(BaseModel):
    arknights_enabled: bool = True
    arknights_mcp_url: str = "http://127.0.0.1:19090/mcp"
    arknights_timeout: float = Field(default=90, ge=5, le=180)
    arknights_nickname_admin_only: bool = False
    arknights_browser_channel: str = ""

    @field_validator("arknights_mcp_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("ARKNIGHTS_MCP_URL 必须是 HTTP(S) 地址，且不能带账号密码")
        return value
