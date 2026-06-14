from __future__ import annotations

from typing import Any

from kanamibot.core import ConfigManager

DEFAULT_GROUP_CONFIG = {
    "show_total": True,
    "show_talker": True,
    "show_repeater": True,
    "show_wordcloud": True,
}

KEY_MAPPING = {
    "统计": "show_total",
    "总量": "show_total",
    "消息": "show_total",
    "龙王": "show_talker",
    "发言": "show_talker",
    "复读": "show_repeater",
    "复读机": "show_repeater",
    "词云": "show_wordcloud",
    "热词": "show_wordcloud",
}

config_unit = ConfigManager.get_config(
    module_name="daily_report",
    default_config={},
    auto_clear_strategy=None,
)


def get_group_config(group_id: str | int) -> dict[str, Any]:
    group_id = str(group_id)
    all_data = config_unit.get()
    raw_config = all_data.get(group_id)
    if not isinstance(raw_config, dict):
        return DEFAULT_GROUP_CONFIG.copy()

    merged = DEFAULT_GROUP_CONFIG.copy()
    merged.update({key: bool(value) for key, value in raw_config.items() if key in merged})
    return merged


def update_group_switch(group_id: str | int, key_cn: str, enable: bool) -> tuple[bool, str]:
    group_id = str(group_id)
    config_key = KEY_MAPPING.get(key_cn)
    if not config_key:
        return False, f"未知配置项：{key_cn}。支持项：统计、龙王、复读、词云。"

    current_group_conf = get_group_config(group_id)
    current_group_conf[config_key] = enable
    config_unit.update({group_id: current_group_conf})

    action = "开启" if enable else "关闭"
    return True, f"已{action}本群日报的【{key_cn}】。"


def get_config_status_text(group_id: str | int) -> str:
    cfg = get_group_config(group_id)

    def status_icon(key: str) -> str:
        return "ON" if cfg.get(key, True) else "OFF"

    return "\n".join(
        [
            "本群日报配置：",
            f"{status_icon('show_total')} [统计] 消息总量",
            f"{status_icon('show_talker')} [龙王] 发言最多",
            f"{status_icon('show_repeater')} [复读] 人类本质",
            f"{status_icon('show_wordcloud')} [词云] 每日热词",
        ]
    )
