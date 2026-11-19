from __future__ import annotations

import json

from kanamibot.core.media_storage import _atomic_write_json
from kanamibot.core.paths import DATA_DIR
from kanamibot.core.storage_lock import storage_lock

PATH = DATA_DIR / "qqbot_query_bindings.json"


def _read() -> dict[str, str]:
    return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}


def get_binding(user_id: int | str) -> str | None:
    with storage_lock(PATH.parent):
        return _read().get(str(user_id))


def set_binding(user_id: int | str, nickname: str) -> None:
    with storage_lock(PATH.parent):
        data = _read()
        data[str(user_id)] = nickname.strip()
        _atomic_write_json(PATH, data)


def remove_binding(user_id: int | str) -> bool:
    with storage_lock(PATH.parent):
        data = _read()
        if str(user_id) not in data:
            return False
        data.pop(str(user_id))
        _atomic_write_json(PATH, data)
        return True
