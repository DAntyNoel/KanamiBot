from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

ENGINE_LABELS = {
    "lens": "谷歌 Lens",
    "saucenao": "SauceNAO",
    "tracemoe": "动漫截图溯源",
    "iqdb": "IQDB",
    "baidu": "百度识图",
    "bing": "必应",
    "yandex": "Yandex",
    "google": "谷歌识图",
}


class Config(BaseModel):
    image_search_enabled: bool = True
    image_search_engines: list[str] = ["lens", "saucenao", "tracemoe"]
    image_search_timeout: float = Field(45, ge=5, le=120)
    image_search_max_accounts: int = Field(3, ge=1, le=10)
    image_search_strict_consensus: bool = True
    image_search_infer_accounts: bool = True
    image_search_proxy: str = ""
    image_search_browser_channel: str = ""
    image_search_browser_executable: str = ""
    image_search_cookie_file: str = "data/image_search/google_cookies.json"
    image_search_google_cookie: str = ""
    image_search_found_image: str = ""
    image_search_send_on_miss: bool = False
    image_search_cooldown: int = Field(30, ge=0)
    image_search_per_user_day: int = Field(20, ge=0)
    image_search_global_day: int = Field(100, ge=0)
    image_search_cache_days: int = Field(30, ge=0)
    image_search_cache_max: int = Field(500, ge=1)
    # Explicit opt-in: QQ URLs cannot generally be fetched by URL-only engines.
    image_search_public_upload: bool = False

    @field_validator("image_search_engines")
    @classmethod
    def validate_engines(cls, engines: list[str]) -> list[str]:
        if not engines or any(engine not in ENGINE_LABELS for engine in engines):
            raise ValueError(f"搜图引擎必须来自 {', '.join(ENGINE_LABELS)}")
        return list(dict.fromkeys(engines))
