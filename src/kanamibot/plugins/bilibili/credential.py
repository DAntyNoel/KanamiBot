from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

from bilibili_api import Credential
from bilibili_api import login_v2 as login
from nonebot.log import logger

from kanamibot.core.paths import DATA_DIR

CREDENTIAL_DIR = DATA_DIR / "bilibili"
CREDENTIAL_PATH = CREDENTIAL_DIR / "credential.json"


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = path.with_suffix(f".tmp.{uuid.uuid4()}")
    try:
        with temp_file.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(temp_file, path)
    except Exception:
        temp_file.unlink(missing_ok=True)
        raise


def save_credential(credential: Credential) -> None:
    _atomic_write_json(CREDENTIAL_PATH, credential.get_cookies())


def load_credential() -> Credential:
    with CREDENTIAL_PATH.open("r", encoding="utf-8") as file:
        cookies = json.load(file)
    return Credential.from_cookies(cookies)


async def get_credential() -> Credential | None:
    try:
        credential = load_credential()
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None

    try:
        if await credential.check_valid():
            return credential
    except Exception as exc:
        logger.warning("[Bilibili] Credential validation failed: %s", exc)
    return None


async def qrlogin_get_qrcode():
    qr = login.QrCodeLogin(platform=login.QrCodeLoginChannel.WEB)
    await qr.generate_qrcode()
    return qr.get_qrcode_picture(), qr


async def qrlogin_check(qr: login.QrCodeLogin) -> tuple[bool, str | Credential]:
    while not qr.has_done():
        event = await qr.check_state()
        if event == login.QrCodeLoginEvents.TIMEOUT:
            return False, "二维码过期，请重新获取！"
        if event == login.QrCodeLoginEvents.DONE:
            credential = qr.get_credential()
            save_credential(credential)
            return True, credential
        await asyncio.sleep(1)
    return False, "未知错误"
