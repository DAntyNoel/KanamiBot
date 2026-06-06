from __future__ import annotations

import asyncio
from typing import Any

from bilibili_api import Credential, user
from nonebot.log import logger

from .dynamic_parser import parse_raw_dynamic


async def get_dynamic_by_uids(
    credential: Credential,
    uids: list[int],
    update_baselines: list[int] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    for index, uid in enumerate(uids):
        try:
            account = user.User(uid=uid, credential=credential)
            payload = await account.get_dynamics_new()
            items = payload.get("items") or []
            items.sort(key=lambda item: int(item.get("id_str") or 0), reverse=True)

            if not items:
                continue

            if update_baselines is None:
                parsed = parse_raw_dynamic(items[0])
                if parsed:
                    result.append(parsed)
                continue

            baseline = int(update_baselines[index])
            for item in items:
                dynamic_id = int(item.get("id_str") or 0)
                if dynamic_id <= baseline:
                    continue
                parsed = parse_raw_dynamic(item)
                if parsed:
                    result.append(parsed)
        except Exception as exc:
            logger.warning("[Bilibili] Failed to fetch dynamics for uid %s: %s", uid, exc)
            if "352" in str(exc):
                await asyncio.sleep(1)

    return result


async def query_dynamic(
    uid: int,
    credential: Credential,
    index: int,
    *,
    max_pages: int,
) -> dict[str, Any] | None:
    if index <= 0:
        return None

    offset = ""
    account = user.User(uid=uid, credential=credential)

    for _page in range(max_pages):
        payload = await account.get_dynamics_new(offset=offset)
        items = payload.get("items") if isinstance(payload, dict) else None
        if not items:
            return None

        items.sort(key=lambda item: int(item.get("id_str") or 0), reverse=True)
        if len(items) >= index:
            return items[index - 1]

        index -= len(items)
        offset = payload.get("offset") or ""
        if not offset:
            return None

    return None
