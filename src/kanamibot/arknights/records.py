from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import defaultdict
from pathlib import Path

from .commands import RESERVED

ASSETS = Path(__file__).with_name("assets")


def normalized(value: str) -> str:
    return value.strip().casefold()


class Records:
    """Reviewed seed plus durable local overrides; deletions survive restarts."""

    def __init__(self, path: Path, *, assets: Path = ASSETS) -> None:
        self.path = path
        catalog = json.loads((assets / "operators.json").read_text("utf-8"))
        self.operators = {row["name"]: row for row in catalog["operators"]}
        self.names = {normalized(name): name for name in self.operators}
        self.groups = catalog["alter_groups"]
        seed = json.loads((assets / "nicknames.json").read_text("utf-8"))
        self.seed = {normalized(row["alias"]): row for row in seed["aliases"]}
        self._lock = threading.RLock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "aliases": {}, "deleted": []}
        value = json.loads(self.path.read_text("utf-8"))
        if not isinstance(value.get("aliases"), dict) or not isinstance(value.get("deleted"), list):
            raise ValueError("本地绰号记录格式有误，请管理员检查文件。")
        return value

    def _save(self, value: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                delete=False,
            ) as file:
                temp = Path(file.name)
                json.dump(value, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, self.path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def aliases(self) -> dict[str, dict]:
        with self._lock:
            local = self._read()
            rows = {key: row for key, row in self.seed.items() if key not in local["deleted"]}
            rows.update(local["aliases"])
            return rows

    def deleted(self, name: str) -> bool:
        return normalized(name) in self._read()["deleted"]

    def resolve(self, name: str) -> str:
        if canonical := self.names.get(normalized(name)):
            return canonical
        row = self.aliases().get(normalized(name))
        return row["name"] if row else name

    def alters(self, name: str) -> list[str]:
        name = self.resolve(name)
        for group in self.groups:
            if name in group:
                # An already-altered operator stays on that form.
                return [name] if name != group[0] else group[1:]
        return []

    def add(self, name: str, alias: str, *, operator: dict | None = None) -> None:
        name = self.resolve(name)
        if not 1 <= len(alias) <= 32 or any(char.isspace() or ord(char) < 32 for char in alias):
            raise ValueError("绰号需为 1～32 个字符，不能包含空白。")
        key = normalized(alias)
        if (
            key in RESERVED
            or key in self.names
            or key == normalized(name)
            or alias.startswith("#")
            or "[CQ:" in alias
        ):
            raise ValueError("绰号不能占用正式干员名、命令名或包含消息代码。")
        info = self.operators.get(name) or operator
        if not info:
            raise ValueError("未找到这名干员，请使用正式名称。")
        with self._lock:
            current = self.aliases().get(key)
            if current and current["name"] != name:
                raise ValueError(f"绰号「{alias}」已属于{current['name']}，请先删除原记录。")
            local = self._read()
            local["aliases"][key] = {
                "alias": alias,
                "name": name,
                "faction": info.get("faction") or "未分类",
                "source": "群聊添加",
            }
            local["deleted"] = [item for item in local["deleted"] if item != key]
            self._save(local)

    def delete(self, alias: str) -> bool:
        key = normalized(alias)
        with self._lock:
            exists = key in self.aliases()
            local = self._read()
            local["aliases"].pop(key, None)
            # Also suppress upstream aliases, including ones absent from the seed.
            local["deleted"] = sorted({*local["deleted"], key})
            self._save(local)
            return exists

    def list_messages(self, max_chars: int = 1800) -> list[str]:
        factions: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for row in self.aliases().values():
            faction = self.operators.get(row["name"], {}).get("faction") or row.get("faction")
            factions[faction or "未分类"][row["name"]].append(row["alias"])
        messages = []
        for faction, names in sorted(factions.items()):
            header = f"【{faction}】\n"
            page = header
            for name, aliases in sorted(names.items()):
                line = f"{name}：{'、'.join(sorted(aliases))}\n"
                if len(page) + len(line) > max_chars and page != header:
                    messages.append(page.rstrip())
                    page = header + "（续）\n"
                page += line
            messages.append(page.rstrip())
        return messages
