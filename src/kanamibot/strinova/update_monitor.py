from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from nonebot.adapters.onebot.v11 import Bot, MessageSegment

from kanamibot.core.paths import DATA_DIR
from kanamibot.core.timing import timed

MANIFEST_URL = (
    "https://klbqcp-client-cdn.gxpan.cn/Client/Win/GameDepot/Game_TYF/manifest.txt"
)
GROUP_ID = 1062499136
OWNER_ID = 2774133374
STATE_PATH = DATA_DIR / "strinova" / "tyf_manifest.json"


@dataclass(frozen=True)
class ManifestTarget:
    name: str
    depot: str
    url: str
    state_path: Path


TYF_TARGET = ManifestTarget("测试服", "Game_TYF", MANIFEST_URL, STATE_PATH)
RELEASE_TARGET = ManifestTarget(
    "正式服客户端",
    "Game_Release",
    "https://klbqcp-client-cdn.gxpan.cn/Client/Win/GameDepot/Game_Release/manifest.txt",
    DATA_DIR / "strinova" / "release_manifest.json",
)
MANIFEST_TARGETS = (TYF_TARGET, RELEASE_TARGET)


def parse_manifest(text: str, *, depot: str = "Game_TYF") -> dict[str, str]:
    normalized = "\n".join(line.strip() for line in text.lstrip("\ufeff").splitlines()).strip()
    match = re.match(r"--last_version--\n(\d+(?:\.\d+){3})\n--download_version--\n", normalized)
    if match is None or f"|/{depot}/" not in normalized:
        raise ValueError(f"{depot} manifest 格式无效")
    return {
        "version": match[1],
        "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def save_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


@timed()
async def check_update(
    bot: Bot,
    *,
    target: ManifestTarget = TYF_TARGET,
    state_path: Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    state_path = target.state_path if state_path is None else state_path
    # Corrupt/unreadable state must not silently erase a pending notification.
    previous = None
    if state_path.exists():
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or not all(
            isinstance(previous.get(key), str) and previous[key] for key in ("version", "sha256")
        ):
            raise ValueError(f"{target.name}监测基线无效")
    async with httpx.AsyncClient(timeout=20, trust_env=False, transport=transport) as client:
        response = await client.get(target.url, headers={"Cache-Control": "no-cache"})
        response.raise_for_status()
        current = parse_manifest(response.text, depot=target.depot)
    if previous is None:
        save_state(state_path, current)
        return
    if previous == current:
        return
    detail = (
        f"版本：{previous['version']} → {current['version']}"
        if previous["version"] != current["version"]
        else f"版本：{current['version']}（版本号未变，资源清单有更新）"
    )
    message = MessageSegment.at(OWNER_ID) + MessageSegment.text(
        f"\n卡拉彼丘{target.name}发现更新！\n{detail}\n{target.url}"
    )
    await bot.send_group_msg(group_id=GROUP_ID, message=message)
    # Advance only after successful delivery; failures retry on the next minute.
    save_state(state_path, current)
