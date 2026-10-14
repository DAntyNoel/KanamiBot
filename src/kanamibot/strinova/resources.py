"""Read the user-supplied game resource pack without importing the NoneBot plugin."""

from __future__ import annotations

import base64
import json
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "strinova"
RESOURCE_DIR = PLUGIN_DIR / "resources"
CACHE_DIR = PLUGIN_DIR / "cache"


class ResourcePack:
    def __init__(self, root: Path = RESOURCE_DIR) -> None:
        self.root = root.resolve()
        self._mappings: dict[str, Any] = {}
        self._records: dict[tuple[str, str | None], dict[int, dict]] = {}
        self._images: OrderedDict[Path, str] = OrderedDict()
        self._image_bytes = 0
        self._lock = RLock()

    def mapping(self, name: str) -> Any:
        if not name.replace("_", "").isalnum():
            return {}
        with self._lock:
            if name not in self._mappings:
                try:
                    value = json.loads(
                        (self.root / "mappings" / f"{name}.json").read_text(encoding="utf-8")
                    )
                except (OSError, ValueError):
                    value = {}
                if len(self._mappings) >= 16:
                    self._mappings.pop(next(iter(self._mappings)))
                self._mappings[name] = value
            return self._mappings[name]

    def records(self, name: str, group: str | None = None) -> dict[int, dict]:
        with self._lock:
            key = (name, group)
            if key not in self._records:
                entries = self.mapping(name)
                if group is not None and isinstance(entries, dict):
                    entries = entries.get(group, [])
                if not isinstance(entries, list):
                    return {}
                if len(self._records) >= 16:
                    self._records.pop(next(iter(self._records)))
                self._records[key] = {
                    row["id"]: row
                    for row in entries
                    if isinstance(row, dict)
                    and isinstance(row.get("id"), int)
                    and not isinstance(row["id"], bool)
                    and row.get("verified") is True
                }
            return self._records[key]

    def resolve(self, value: str | None) -> Path | None:
        if not isinstance(value, str):
            return None
        value = value.replace("\\", "/")
        if value.startswith("strinova_resources/"):
            value = value.removeprefix("strinova_resources/")
        elif value.startswith("/Game/"):
            value = "images/" + value.removeprefix("/Game/").split(".", 1)[0] + ".png"
        candidate = (self.root / value).resolve()
        return candidate if candidate.is_relative_to(self.root) and candidate.is_file() else None

    def image_path(self, row: dict, *fields: str) -> Path | None:
        extracted = row.get("extracted_paths") or {}
        game_paths = row.get("game_paths") or {}
        extracted = extracted if isinstance(extracted, dict) else {}
        game_paths = game_paths if isinstance(game_paths, dict) else {}
        for field in fields:
            value = extracted.get(field, {})
            path = self.resolve(value.get("png") if isinstance(value, dict) else value)
            path = path or self.resolve(game_paths.get(field))
            if path is not None:
                return path
        return None

    def data_uri(self, path: Path | None) -> str | None:
        if path is None:
            return None
        path = path.resolve()
        if not path.is_relative_to(self.root):
            return None
        with self._lock:
            if path in self._images:
                self._images.move_to_end(path)
                return self._images[path]
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".webp": "image/webp",
            ".ttf": "font/ttf",
            ".otf": "font/otf",
        }.get(path.suffix.lower())
        try:
            if mime is None or path.stat().st_size > 24 * 1024 * 1024:
                return None
            value = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        except OSError:
            return None
        # Keep fonts and frequently used artwork hot without retaining a whole resource pack.
        with self._lock:
            existing = self._images.pop(path, None)
            self._image_bytes -= len(existing) if existing else 0
            self._images[path] = value
            self._image_bytes += len(value)
            while self._image_bytes > 32 * 1024 * 1024 or len(self._images) > 128:
                _, removed = self._images.popitem(last=False)
                self._image_bytes -= len(removed)
        return value

    def image(self, row: dict, *fields: str) -> str | None:
        return self.data_uri(self.image_path(row, *fields))

    def avatar_path(self, icon: int) -> Path | None:
        if not isinstance(icon, int) or isinstance(icon, bool) or icon <= 0:
            return None
        row = self.records("avatars").get(icon, {})
        return self.image_path(row, "IconItem", "IconDisplayItem") or self.resolve(
            f"images/PaperMan/UI/Atlas/DynamicResource/IdCard/Appearance/"
            f"T_Dynamic_IdCard_{icon}_icon.png"
        )

    def role_image(self, role_id: int, *, portrait: bool = False) -> str | None:
        row = self.records("characters").get(role_id, {})
        return self.image(row, "CareerIcon" if portrait else "IconRoleHud")

    def rank_image(self, rank_id: int) -> str | None:
        return self.image(self.records("ranks", "rank").get(rank_id, {}), "IconDivision")

    def map_name(self, map_id: int | None) -> str:
        return self.records("maps").get(map_id, {}).get("name") or (
            f"地图 #{map_id}" if map_id else "地图未提供"
        )

    def map_image(self, map_id: int | None, *, background: bool = False) -> str | None:
        fields = (
            ("IconMapIntro", "IconMapCareer", "IconMapCareerTitle")
            if background
            else ("IconMapCareerTitle", "IconMapCareer", "IconMapIntro")
        )
        return self.image(self.records("maps").get(map_id, {}), *fields)

    def mode_name(self, game_mode: int | None) -> str | None:
        modes = self.mapping("game_modes")
        modes = modes.get("room_mode_display", {}) if isinstance(modes, dict) else {}
        row = modes.get(str(game_mode), {}) if isinstance(modes, dict) else {}
        row = row if isinstance(row, dict) else {}
        return row.get("name_cn") if row.get("verified") is True else None

    def status_name(self, code: int | None) -> str:
        if code is None:
            return "状态未提供"
        enums = self.mapping("enums")
        enum = enums.get("ncmd_cs.EOnlineStatus", {}) if isinstance(enums, dict) else {}
        enum = enum if isinstance(enum, dict) else {}
        labels = {
            "OnlineStatus_ONLINE": "在线",
            "OnlineStatus_LOST": "连接中断",
            "OnlineStatus_OFFLINE": "离线",
            "OnlineStatus_LEAVE": "离开",
            "OnlineStatus_INVISIBLE": "隐身",
            "OnlineStatus_NOT_DISTURB": "勿扰",
        }
        return next(
            (label for name, label in labels.items() if enum.get(name) == code),
            f"状态码 {code}" if code is not None else "状态未提供",
        )


@lru_cache(maxsize=1)
def resource_pack() -> ResourcePack:
    return ResourcePack()
