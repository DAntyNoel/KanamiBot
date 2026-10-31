from __future__ import annotations

import hashlib
import json
import re
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


def parse_manifest(text: str) -> dict[str, str]:
    normalized = "\n".join(line.strip() for line in text.lstrip("\ufeff").splitlines()).strip()
    match = re.match(r"--last_version--\n(\d+(?:\.\d+){3})\n--download_version--\n", normalized)
    if match is None or "|/Game_TYF/" not in normalized:
        raise ValueError("测试服 manifest 格式无效")
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
    bot: Bot, *, state_path: Path = STATE_PATH, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    # Corrupt/unreadable state must not silently erase a pending notification.
    previous = None
    if state_path.exists():
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or not all(
            isinstance(previous.get(key), str) and previous[key] for key in ("version", "sha256")
        ):
            raise ValueError("测试服监测基线无效")
    async with httpx.AsyncClient(timeout=20, trust_env=False, transport=transport) as client:
        response = await client.get(MANIFEST_URL, headers={"Cache-Control": "no-cache"})
        response.raise_for_status()
        current = parse_manifest(response.text)
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
        f"\n卡拉彼丘测试服发现更新！\n{detail}\n{MANIFEST_URL}"
    )
    await bot.send_group_msg(group_id=GROUP_ID, message=message)
    # Advance only after successful delivery; failures retry on the next minute.
    save_state(state_path, current)
