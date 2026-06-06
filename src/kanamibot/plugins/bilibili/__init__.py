from __future__ import annotations

from nonebot import require
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="Bilibili",
    description="B站动态/直播推送及订阅管理。",
    usage=(
        "bili_login\n"
        "关注 <UID|UP主名> / add sub <UID|UP主名>\n"
        "取关 <UID|UP主名> / del sub <UID|UP主名>\n"
        "关注列表 / sub list\n"
        "更新动态\n"
        "查看动态 <UID|订阅名> [序号] / dynamic <UID|订阅名> [序号]"
    ),
)

require("nonebot_plugin_apscheduler")

from . import commands as commands  # noqa: E402,F401
from . import jobs as jobs  # noqa: E402,F401
