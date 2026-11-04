from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest

from kanamibot.strinova.update_monitor import (
    GROUP_ID,
    MANIFEST_TARGETS,
    OWNER_ID,
    check_update,
    parse_manifest,
)


def manifest(version="1.18.1.8004", checksum="abc", depot="Game_TYF"):
    return (
        f"--last_version--\n{version}\n--download_version--\n"
        f"{version}->{version}|/{depot}/{version}/full_zip/a.7z|{checksum}|123\n"
    )


@pytest.mark.parametrize("target", MANIFEST_TARGETS, ids=lambda target: target.depot)
def test_normalization_and_invalid_responses(target):
    body = manifest(depot=target.depot)
    assert parse_manifest(body, depot=target.depot) == parse_manifest(
        "\ufeff" + body.replace("\n", "\r\n"), depot=target.depot
    )
    for invalid in ("", "<html>error</html>", "--last_version--\n1.2.3.4", body.replace(
        target.depot, "Wrong_Depot"
    )):
        with pytest.raises(ValueError):
            parse_manifest(invalid, depot=target.depot)


@pytest.mark.parametrize("target", MANIFEST_TARGETS, ids=lambda target: target.depot)
def test_baseline_restart_changes_and_send_retry(tmp_path, target):
    bot = AsyncMock()
    state_path = tmp_path / "state.json"
    body = manifest(depot=target.depot)

    def respond(request):
        assert str(request.url) == target.url
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(respond)

    def poll():
        asyncio.run(check_update(bot, target=target, state_path=state_path, transport=transport))

    poll()
    poll()
    bot.send_group_msg.assert_not_called()

    original = state_path.read_text()
    body = manifest("1.18.1.8005", depot=target.depot)
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
    assert target.name in str(call["message"])
    assert target.url in str(call["message"])
    bot.send_group_msg.reset_mock()
    poll()
    bot.send_group_msg.assert_not_called()
    body = manifest("1.18.1.8005", "def", depot=target.depot)
    poll()
    assert "版本号未变" in str(bot.send_group_msg.call_args.kwargs["message"])
    assert json.loads(state_path.read_text())["version"] == "1.18.1.8005"


def test_targets_keep_independent_baselines(tmp_path):
    targets = [replace(target, state_path=tmp_path / target.state_path.name)
               for target in MANIFEST_TARGETS]
    bot = AsyncMock()
    version = "1.18.1.8004"
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, text=manifest(version, depot=next(
            target.depot for target in targets if target.url == str(request.url)
        ))
    ))
    for target in targets:
        asyncio.run(check_update(bot, target=target, transport=transport))
    bot.send_group_msg.assert_not_called()
    original = targets[0].state_path.read_bytes()
    version = "1.18.1.8005"
    asyncio.run(check_update(bot, target=targets[1], transport=transport))
    bot.send_group_msg.assert_awaited_once()
    assert targets[0].state_path.read_bytes() == original
    assert json.loads(targets[1].state_path.read_text())["version"] == version


@pytest.mark.parametrize("status,body", [(500, "error"), (200, "<html>error</html>")])
@pytest.mark.parametrize("target", MANIFEST_TARGETS, ids=lambda target: target.depot)
def test_fetch_failures_preserve_baseline(tmp_path, status, body, target):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(parse_manifest(
        manifest(depot=target.depot), depot=target.depot
    )))
    original = state_path.read_bytes()
    bot = AsyncMock()
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text=body))
    with pytest.raises((httpx.HTTPStatusError, ValueError)):
        asyncio.run(check_update(bot, target=target, state_path=state_path, transport=transport))
    assert state_path.read_bytes() == original
    bot.send_group_msg.assert_not_called()
