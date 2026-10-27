"""Local defaults shared by the bot and the migration entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from nonebot import get_driver
from pydantic import BaseModel

from kanamibot.core.paths import DATA_DIR


class Config(BaseModel):
    kanamibot_skland_enabled: bool = True
    kanamibot_skland_auto_sign: bool = True


def configure_storage() -> None:
    # Keep new plugin data inside this checkout; explicit .env paths take precedence.
    config = get_driver().config
    defaults = {
        "localstore_data_dir": DATA_DIR,
        "localstore_cache_dir": DATA_DIR / "cache",
        "localstore_config_dir": DATA_DIR / "plugin_configs",
    }
    for name, path in defaults.items():
        if getattr(config, name, None) is None:
            setattr(config, name, path)


def configure_browser() -> None:
    config = get_driver().config
    if getattr(config, "render", None) is not None:
        return

    # htmlrender 0.8 otherwise uses a different browser directory from `playwright install`.
    if browser_path := os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
        if browser_path == "0":
            return
        cache = Path(browser_path)
    elif sys.platform == "win32":
        cache = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData/Local")) / "ms-playwright"
    elif sys.platform == "darwin":
        cache = Path.home() / "Library/Caches/ms-playwright"
    else:
        cache = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "ms-playwright"

    config.render = {"provider": "playwright", "provider_config": {"storage_path": cache}}
