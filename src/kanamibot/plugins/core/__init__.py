from __future__ import annotations

import importlib
import sys

from nonebot import get_driver
from nonebot.plugin import PluginMetadata

from kanamibot.core import *  # noqa: F403

__plugin_meta__ = PluginMetadata(
    name="KanamiCore",
    description="KanamiBot 核心管理命令与共享能力入口。",
    usage="enable/disable/list module/ban user",
)

try:
    get_driver()
except ValueError:
    pass
else:
    from kanamibot.core.commands import __plugin_meta__ as __plugin_meta__

_ALIASES = (
    "chat_history",
    "check_perm",
    "config_storage",
    "group_manager",
    "image_wrappers",
    "media_storage",
    "utils",
    "utils.file",
    "utils.image",
    "utils.message_builder",
    "utils.text",
    "utils.text2image",
    "utils.vedio",
    "utils.video",
    "utils.voice",
)

for suffix in _ALIASES:
    sys.modules[f"{__name__}.{suffix}"] = importlib.import_module(f"kanamibot.core.{suffix}")
