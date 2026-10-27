"""Load the pinned Skland package through KanamiBot's local plugin directory."""

from __future__ import annotations

from nonebot import get_plugin_config, require
from nonebot.plugin import PluginMetadata

from .config import Config, configure_browser, configure_storage

__plugin_meta__ = PluginMetadata(
    name="森空岛适配层",
    description="森空岛的 KanamiBot 权限、帮助与任务配置。",
    usage="",
    type="library",
    config=Config,
)

config = get_plugin_config(Config)

if config.kanamibot_skland_enabled:
    configure_storage()
    configure_browser()
    upstream = require("nonebot_plugin_skland")

    from .commands import SklandCommandExtension
    from .help import HELP_SUMMARY
    from .permissions import protect_matcher
    from .reactions import install_ok_reactions

    install_ok_reactions()
    upstream.skland.executor.extensions.append(SklandCommandExtension())
    protect_matcher(upstream.skland)
    upstream.__plugin_meta__.usage = HELP_SUMMARY
    upstream.__plugin_meta__.description = (
        "森空岛账号绑定与终末地角色卡、签到、抽卡、战争回响查询。"
    )

    if not config.kanamibot_skland_auto_sign:
        from nonebot_plugin_apscheduler import scheduler

        for job_id in ("daily_arksign", "daily_efsign"):
            if scheduler.get_job(job_id) is not None:
                scheduler.remove_job(job_id)
