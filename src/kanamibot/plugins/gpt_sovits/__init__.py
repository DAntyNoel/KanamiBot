from __future__ import annotations

import os

from nonebot.log import logger
from nonebot.plugin import PluginMetadata

TRUE_VALUES = {"1", "true", "yes", "on", "enable", "enabled", "开启", "启用"}


def _tts_enabled() -> bool:
    raw_value = os.getenv("KANAMIBOT_TTS_ENABLED") or os.getenv("GPT_SOVITS_ENABLED") or ""
    return raw_value.strip().lower() in TRUE_VALUES


if _tts_enabled():
    from .commands import __plugin_meta__ as __plugin_meta__
else:
    __plugin_meta__ = PluginMetadata(
        name="GPT-SoVITS TTS",
        description="GPT-SoVITS 语音合成插件，默认禁用。",
        usage="设置 KANAMIBOT_TTS_ENABLED=1 后启用 #tts。",
        type="library",
    )
    logger.info("[gpt_sovits] TTS plugin is migrated but disabled by default.")
