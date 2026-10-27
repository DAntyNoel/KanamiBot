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

HELP_TEXT = """森空岛 / 终末地查询
/扫码绑定：使用森空岛 App 扫码，核对角色后回复「确认」保存
/森空岛角色：查看全部账号和角色序号
/切换终末地角色 2：设置默认终末地角色
/角色更新：同步本人账号的角色列表
/ef [-r 角色序号] [-a] [-s]：终末地角色卡，-a 展示全部干员，-s 简化背景
/终末地签到 [-r 角色序号]：签到本人全部或指定终末地角色
/终末地签到详情：查看本人签到结果
/终末地抽卡记录 [-r 角色序号]：同步并查询抽卡统计
/战争回响 [-s 赛季] [-w 轮换]：查询战争回响，-s -1 表示上一赛季
/森空岛解绑：交互选择账号；确认后删除关联角色和抽卡记录
/skland --help：查看完整命令（也支持明日方舟）
/enable skland、/disable skland：管理本群查询开关
群开关不停止后台自动签到；自动签到由 KANAMIBOT_SKLAND_AUTO_SIGN 控制。"""

config = get_plugin_config(Config)

if config.kanamibot_skland_enabled:
    configure_storage()
    configure_browser()
    upstream = require("nonebot_plugin_skland")

    from .permissions import protect_matcher

    protect_matcher(upstream.skland)
    upstream.__plugin_meta__.usage = HELP_TEXT
    upstream.__plugin_meta__.description = (
        "森空岛账号绑定与终末地角色卡、签到、抽卡、战争回响查询。"
    )

    if not config.kanamibot_skland_auto_sign:
        from nonebot_plugin_apscheduler import scheduler

        for job_id in ("daily_arksign", "daily_efsign"):
            if scheduler.get_job(job_id) is not None:
                scheduler.remove_job(job_id)
