from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .results import Result


class Store:
    def __init__(self, path: Path, config: Config):
        self.path, self.config = path, config
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(self.data, dict):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def day(self) -> dict:
        today = datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).date().isoformat()
        if self.data.get("day", {}).get("date") != today:
            self.data["day"] = {"date": today, "users": {}, "total": 0}
        return self.data["day"]

    def quota_reason(self, user: str) -> str:
        day = self.day()
        per_user = self.config.image_search_per_user_day
        total = self.config.image_search_global_day
        if per_user and day["users"].get(user, 0) >= per_user:
            return f"今天的 {per_user} 次搜图已用完，明天再来。"
        if total and day["total"] >= total:
            return f"今天全站 {total} 次搜图已用完，明天再来。"
        return ""

    def charge(self, user: str) -> None:
        day = self.day()
        day["users"][user] = day["users"].get(user, 0) + 1
        day["total"] += 1
        self.save()

    def cache_key(self, image: bytes) -> str:
        options = {
            key: value
            for key, value in self.config.model_dump().items()
            if key
            in {
                "image_search_engines",
                "image_search_strict_consensus",
                "image_search_max_accounts",
                "image_search_infer_accounts",
                "image_search_public_upload",
                "image_search_proxy",
            }
        }
        digest = hashlib.sha256(image + json.dumps(options, sort_keys=True).encode()).hexdigest()
        return f"v2:{digest}"

    def cached(self, key: str) -> Result | None:
        if not self.config.image_search_cache_days:
            return None
        entry = self.data.get("cache", {}).get(key)
        if entry and time.time() - entry.get("at", 0) < self.config.image_search_cache_days * 86400:
            try:
                return Result.from_dict(entry["result"])
            except (KeyError, TypeError, ValueError):
                pass
        return None

    def put(self, key: str, result: Result) -> None:
        # Transient failures must not hide recovery behind a 30-day cached result.
        if result.failures or not result.used or not self.config.image_search_cache_days:
            return
        cache = self.data.setdefault("cache", {})
        cache[key] = {"at": time.time(), "result": result.to_dict()}
        oldest = sorted(cache, key=lambda k: cache[k]["at"])
        for stale in oldest[: -self.config.image_search_cache_max]:
            del cache[stale]
        self.save()
