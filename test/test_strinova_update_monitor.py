from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from kanamibot.strinova.update_monitor import GROUP_ID, OWNER_ID, check_update, parse_manifest


def manifest(version="1.18.1.8004", checksum="abc"):
    return (
        f"--last_version--\n{version}\n--download_version--\n"
        f"{version}->{version}|/Game_TYF/{version}/full_zip/a.7z|{checksum}|123\n"
    )


def test_normalization_and_invalid_responses():
    assert parse_manifest(manifest()) == parse_manifest("\ufeff" + manifest().replace("\n", "\r\n"))
    for invalid in ("", "<html>error</html>", "--last_version--\n1.2.3.4", manifest().replace(
        "Game_TYF", "Game_Release"
    )):
        with pytest.raises(ValueError):
            parse_manifest(invalid)


def test_baseline_restart_changes_and_send_retry(tmp_path):
    bot = AsyncMock()
    state_path = tmp_path / "state.json"
    body = manifest()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))

    def poll():
        asyncio.run(check_update(bot, state_path=state_path, transport=transport))

    poll()
    poll()
    bot.send_group_msg.assert_not_called()
    original = state_path.read_text()
    body = manifest("1.18.1.8005")
    bot.send_group_msg.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError):
        poll()
    assert state_path.read_text() == original
    bot.send_group_msg.side_effect = None
    poll()
    call = bot.send_group_msg.call_args.kwargs
    assert call["group_id"] == GROUP_ID
    assert call["message"][0].type == "at"
    assert call["message"][0].data["qq"] == str(OWNER_ID)
    assert "1.18.1.8004 → 1.18.1.8005" in str(call["message"])
    bot.send_group_msg.reset_mock()
    poll()
    bot.send_group_msg.assert_not_called()
    body = manifest("1.18.1.8005", "def")
    poll()
    assert "版本号未变" in str(bot.send_group_msg.call_args.kwargs["message"])
    assert json.loads(state_path.read_text())["version"] == "1.18.1.8005"


@pytest.mark.parametrize("status,body", [(500, "error"), (200, "<html>error</html>")])
def test_fetch_failures_preserve_baseline(tmp_path, status, body):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(parse_manifest(manifest())))
    original = state_path.read_bytes()
    bot = AsyncMock()
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text=body))
    with pytest.raises((httpx.HTTPStatusError, ValueError)):
        asyncio.run(check_update(bot, state_path=state_path, transport=transport))
    assert state_path.read_bytes() == original
    bot.send_group_msg.assert_not_called()
