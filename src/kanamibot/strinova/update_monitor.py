from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from nonebot.adapters.onebot.v11 import Bot, MessageSegment
from nonebot.log import logger

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


def save_state(path: Path, state: dict[str, str] | list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def same_manifest(left: dict[str, str], right: dict[str, str]) -> bool:
    return all(left.get(key) == right.get(key) for key in ("version", "sha256"))


def load_history(state_path: Path) -> list[dict[str, str]]:
    path = state_path.with_suffix(".history.json")
    if not path.exists():
        return []
    history = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(history, list) or not all(
        isinstance(item, dict) and all(
            isinstance(item.get(key), str) and item[key]
            for key in ("version", "sha256", "first_seen_at")
        ) for item in history
    ):
        raise ValueError("客户端更新历史无效")
    return history


def record_observation(state_path: Path, current: dict[str, str]) -> dict[str, str]:
    history = load_history(state_path)
    if history and same_manifest(history[-1], current):
        return history[-1]
    observed = {**current, "first_seen_at": datetime.now(UTC).isoformat(timespec="seconds")}
    history.append(observed)
    save_state(state_path.with_suffix(".history.json"), history)
    return observed


def time_details(current: dict[str, str], observed: dict[str, str] | None = None) -> str:
    def display(value: str | None) -> str:
        if not value:
            return "未提供"
        try:
            moment = datetime.fromisoformat(value)
            if moment.tzinfo is None:
                return "未提供"
            return moment.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return "未提供"

    first_seen = (observed or {}).get("first_seen_at")
    return (
        f"远端清单修改时间：{display(current.get('last_modified'))}\n"
        f"本地首次发现时间：{display(first_seen) if first_seen else '尚无记录'}\n"
        "（北京时间，以上时间不等于官方发布时间）"
    )


async def fetch_manifest(client: httpx.AsyncClient, target: ManifestTarget) -> dict[str, str]:
    response = await client.get(target.url, headers={"Cache-Control": "no-cache"})
    response.raise_for_status()
    current = parse_manifest(response.text, depot=target.depot)
    try:
        modified = parsedate_to_datetime(response.headers.get("Last-Modified", ""))
        if modified.tzinfo is not None:
            current["last_modified"] = modified.astimezone(UTC).isoformat(timespec="seconds")
    except (ValueError, TypeError, OverflowError):
        pass
    return current


async def query_client_versions(*, transport: httpx.AsyncBaseTransport | None = None) -> str:
    # Read live manifests without advancing notification baselines.
    async with httpx.AsyncClient(timeout=20, trust_env=False, transport=transport) as client:
        results = await asyncio.gather(
            *(fetch_manifest(client, target) for target in MANIFEST_TARGETS),
            return_exceptions=True,
        )
    lines = []
    for target, result in zip(MANIFEST_TARGETS, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("[strinova] {}版本查询失败：{}", target.name, type(result).__name__)
            lines.append(f"{target.name}：查询失败，请稍后重试")
        else:
            lines.append(f"{target.name}：{result['version']}")
            observed = None
            try:
                history = load_history(target.state_path)
                if history and same_manifest(history[-1], result):
                    observed = history[-1]
            except (OSError, ValueError):
                logger.warning("[strinova] {}更新历史读取失败", target.name)
            lines.append(time_details(result, observed))
    return "\n".join(lines)


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
        current = await fetch_manifest(client, target)
    # Persist discovery before sending, so delivery retries retain the original time.
    observed = record_observation(state_path, current)
    if previous is None:
        save_state(state_path, current)
        return
    if same_manifest(previous, current):
        return
    detail = (
        f"版本：{previous['version']} → {current['version']}"
        if previous["version"] != current["version"]
        else f"版本：{current['version']}（版本号未变，资源清单有更新）"
    )
    message = MessageSegment.at(OWNER_ID) + MessageSegment.text(
        f"\n卡拉彼丘{target.name}发现更新！\n{detail}\n"
        f"{time_details(current, observed)}\n{target.url}"
    )
    await bot.send_group_msg(group_id=GROUP_ID, message=message)
    # Advance only after successful delivery; failures retry on the next minute.
    save_state(state_path, current)
