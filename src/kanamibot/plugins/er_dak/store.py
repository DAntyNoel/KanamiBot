from __future__ import annotations

from kanamibot.core import ConfigManager

CONFIG = ConfigManager.get_config(
    module_name="er_dak",
    default_config={"bindings": {}},
    filename="er_dak.json",
    auto_clear_strategy=None,
    max_backups=10,
)


def get_binding(user_id: int | str) -> str | None:
    bindings = CONFIG.get("bindings", {})
    if not isinstance(bindings, dict):
        return None
    value = bindings.get(str(user_id))
    return str(value) if value else None


def set_binding(user_id: int | str, nickname: str) -> None:
    data = CONFIG.get()
    bindings = data.get("bindings") if isinstance(data, dict) else None
    normalized = dict(bindings) if isinstance(bindings, dict) else {}
    normalized[str(user_id)] = nickname.strip()
    CONFIG.set("bindings", normalized)


def remove_binding(user_id: int | str) -> bool:
    bindings = CONFIG.get("bindings", {})
    if not isinstance(bindings, dict) or str(user_id) not in bindings:
        return False
    normalized = dict(bindings)
    normalized.pop(str(user_id), None)
    CONFIG.set("bindings", normalized)
    return True
