from __future__ import annotations

import os


def env_bool(name: str, default: bool = False, *, fallback_name: str | None = None) -> bool:
    raw = os.getenv(name)
    if raw is None and fallback_name:
        raw = os.getenv(fallback_name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, *, min_value: int = 0, max_value: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default

    value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def env_float(
    name: str,
    default: float,
    *,
    min_value: float = 0.0,
    max_value: float | None = None,
) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default

    value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


LOGIN_COOLDOWN_SECONDS = env_int("BILIBILI_LOGIN_COOLDOWN", 3600, min_value=60)
USE_FORWARD = env_bool(
    "BILIBILI_USE_FORWARD",
    default=False,
    fallback_name="BILIBILI__USE_FORWARD",
)
DYNAMIC_INTERVAL_MINUTES = env_float("BILIBILI_DYNAMIC_INTERVAL_MINUTES", 3.0, min_value=1.0)
LIVE_INTERVAL_MINUTES = env_float("BILIBILI_LIVE_INTERVAL_MINUTES", 5.0, min_value=1.0)
LIVE_BATCH_SIZE = env_int("BILIBILI_LIVE_BATCH_SIZE", 50, min_value=1, max_value=50)
REQUEST_DELAY_SECONDS = env_float("BILIBILI_REQUEST_DELAY_SECONDS", 1.0, min_value=0.0)
MANUAL_MAX_PAGES = env_int("BILIBILI_MANUAL_MAX_PAGES", 5, min_value=1, max_value=20)
