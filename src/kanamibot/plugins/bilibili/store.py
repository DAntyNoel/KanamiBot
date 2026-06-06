from __future__ import annotations

from copy import deepcopy
from typing import Any

from kanamibot.core import ConfigManager

CONFIG = ConfigManager.get_config(
    module_name="bilibili",
    default_config={},
    filename="bilibili.json",
    auto_clear_strategy=None,
    max_backups=10,
)


def _normalize_uid(uid: int | str) -> str:
    return str(int(uid)) if str(uid).isdigit() else str(uid)


def _normalize_groups(raw_groups: Any) -> list[int]:
    if not isinstance(raw_groups, list):
        return []

    groups: list[int] = []
    for group_id in raw_groups:
        try:
            normalized = int(group_id)
        except (TypeError, ValueError):
            continue
        if normalized not in groups:
            groups.append(normalized)
    return groups


def normalize_subscription(uid: str, info: Any) -> dict[str, Any]:
    if not isinstance(info, dict):
        info = {}

    return {
        "name": str(info.get("name") or uid),
        "groups": _normalize_groups(info.get("groups")),
        "live_status": int(info.get("live_status") or 0),
        "dynamic": int(info.get("dynamic") or 0),
    }


def get_subscriptions(*, reload: bool = False) -> dict[str, dict[str, Any]]:
    raw_data = CONFIG.get(_require_reload=reload)
    if not isinstance(raw_data, dict):
        return {}
    return {
        str(uid): normalize_subscription(str(uid), deepcopy(info))
        for uid, info in raw_data.items()
    }


def set_subscription(uid: int | str, info: dict[str, Any]) -> None:
    str_uid = _normalize_uid(uid)
    CONFIG.set(str_uid, normalize_subscription(str_uid, info))


def cleanup_unsubscribed() -> None:
    data = get_subscriptions(reload=True)
    cleaned = {uid: info for uid, info in data.items() if info["groups"]}
    if len(cleaned) != len(data):
        CONFIG.set_self(cleaned)


def find_subscription(target: str) -> tuple[str, dict[str, Any]] | None:
    target = target.strip()
    data = get_subscriptions()
    if target in data:
        return target, data[target]

    for uid, info in data.items():
        if info["name"] == target:
            return uid, info
    return None


def add_group_subscription(
    uid: int | str,
    *,
    name: str,
    group_id: int,
    dynamic_id: int,
    live_status: int = 0,
) -> bool:
    str_uid = _normalize_uid(uid)
    data = get_subscriptions()
    info = data.get(
        str_uid,
        {
            "name": name,
            "groups": [],
            "live_status": live_status,
            "dynamic": dynamic_id,
        },
    )

    info["name"] = name or info["name"]
    info["dynamic"] = int(info.get("dynamic") or dynamic_id or 0)
    info["live_status"] = int(info.get("live_status") or live_status or 0)

    group_id = int(group_id)
    if group_id in info["groups"]:
        set_subscription(str_uid, info)
        return False

    info["groups"].append(group_id)
    set_subscription(str_uid, info)
    return True


def remove_group_subscription(target_uid: int | str, group_id: int) -> dict[str, Any] | None:
    str_uid = _normalize_uid(target_uid)
    data = get_subscriptions()
    info = data.get(str_uid)
    if not info:
        return None

    group_id = int(group_id)
    if group_id not in info["groups"]:
        return None

    info["groups"].remove(group_id)
    set_subscription(str_uid, info)
    cleanup_unsubscribed()
    return info


def active_subscriptions(*, reload: bool = False) -> dict[str, dict[str, Any]]:
    return {uid: info for uid, info in get_subscriptions(reload=reload).items() if info["groups"]}


def update_dynamic_baseline(uid: int | str, dynamic_id: int) -> None:
    str_uid = _normalize_uid(uid)
    info = get_subscriptions().get(str_uid)
    if not info:
        return
    info["dynamic"] = int(dynamic_id)
    set_subscription(str_uid, info)


def update_live_status(uid: int | str, live_status: int, *, name: str | None = None) -> None:
    str_uid = _normalize_uid(uid)
    info = get_subscriptions().get(str_uid)
    if not info:
        return
    info["live_status"] = int(live_status)
    if name:
        info["name"] = name
    set_subscription(str_uid, info)
