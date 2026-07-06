from __future__ import annotations

import json
import os
import uuid
from typing import Any

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import (
    GROUP_ADMIN,
    GROUP_OWNER,
    Bot,
    GroupMessageEvent,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from .paths import DATA_DIR

DATA_PATH = DATA_DIR / "group_manager.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)


class GroupConfig:
    def __init__(self) -> None:
        self.config: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        if DATA_PATH.exists():
            try:
                with DATA_PATH.open("r", encoding="utf-8") as file:
                    loaded = json.load(file)
                if isinstance(loaded, dict):
                    self.config = loaded
                    return
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(f"Failed to load group manager config: {exc}")
        self.config = {}

    def save(self) -> None:
        temp_file = DATA_PATH.with_suffix(f".tmp.{uuid.uuid4()}")
        with temp_file.open("w", encoding="utf-8") as file:
            json.dump(self.config, file, indent=4, ensure_ascii=False)
        os.replace(temp_file, DATA_PATH)

    def get_group_data(self, group_id: str) -> dict:
        if group_id not in self.config:
            self.config[group_id] = {"modules": {}, "blacklist": []}
            self.save()
        return self.config[group_id]

    def set_module_state(self, group_id: str, module_name: str, state: bool) -> None:
        data = self.get_group_data(group_id)
        data["modules"][module_name] = state
        self.save()

    def is_module_enabled(self, group_id: str, module_name: str) -> bool:
        """检查模块是否开启，默认为开启 (True)"""
        data = self.get_group_data(group_id)
        return data["modules"].get(module_name, True)

    def ban_user(self, group_id: str, user_id: int) -> None:
        data = self.get_group_data(group_id)
        if user_id not in data["blacklist"]:
            data["blacklist"].append(user_id)
            self.save()

    def unban_user(self, group_id: str, user_id: int) -> None:
        data = self.get_group_data(group_id)
        if user_id in data["blacklist"]:
            data["blacklist"].remove(user_id)
            self.save()

    def is_user_banned(self, group_id: str, user_id: int) -> bool:
        data = self.get_group_data(group_id)
        return user_id in data["blacklist"]

# 初始化全局配置单例
group_config = GroupConfig()
__all_modules__: set[str] = set()


def ModuleRule(module_name: str) -> Rule:
    __all_modules__.add(module_name)

    async def _check(bot: Bot, event: MessageEvent) -> bool:
        user_id = event.user_id

        if str(user_id) in get_driver().config.superusers:
            return True

        if isinstance(event, PrivateMessageEvent):
            return True

        if isinstance(event, GroupMessageEvent):
            group_id = str(event.group_id)
            if group_config.is_user_banned(group_id, user_id):
                return False
            return group_config.is_module_enabled(group_id, module_name)

        return False

    return Rule(_check)


ADMIN_PERMISSION = SUPERUSER | GROUP_ADMIN | GROUP_OWNER
