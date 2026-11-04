from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import nonebot
import pytest
from erbs_plugin import (
    AsyncERBSClient,
    ERBSConfig,
    PlayerNotFound,
    RateLimited,
    UpstreamUnavailable,
)

nonebot.init(_env_file=None)
assert nonebot.load_plugin("kanamibot.plugins.er_dak") is not None

from kanamibot.plugins.er_dak import pet_jobs  # noqa: E402
from kanamibot.plugins.er_dak.group_pet import PetTracker, Snapshot, interval  # noqa: E402
from kanamibot.plugins.er_dak.match_detail import (  # noqa: E402
    MatchDetailRenderer,
    PetSource,
    build_detail_card,
)
from kanamibot.plugins.er_dak.parser import parse_command  # noqa: E402
from kanamibot.plugins.er_dak.pet_store import DAY, PetStore  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def rig(tmp_path):
    now = [10 * DAY]
    store = PetStore(tmp_path / "pets.sqlite3")
    source = AsyncMock(return_value=Snapshot("Player", 10, now[0] - 60, 41))
    render, send = AsyncMock(return_value=b"image"), AsyncMock()

    def enabled(bot, group):
        return True

    tracker = PetTracker(
        store, source, render, send, enabled, clock=lambda: now[0], send_interval=0
    )
    return SimpleNamespace(
        now=now, store=store, source=source, render=render, send=send, tracker=tracker
    )


def row(rig, group=1):
    return rig.store.rows("bot", group)[0]


def due(rig):
    for item in rig.store.rows("bot"):
        rig.store.update(item, next_check=rig.now[0])


def test_baseline_duplicate_limit_and_restart(rig):
    async def scenario():
        assert await rig.tracker.subscribe("bot", 1, 7, "Player")
        expiry = row(rig)["expires"]
        assert not await rig.tracker.subscribe("bot", 1, 7, "PLAYER")
        assert row(rig)["expires"] == expiry
        await rig.tracker.tick("bot")
        rig.send.assert_not_called()
        for name in ("Other", "Third"):
            rig.source.return_value = Snapshot(name)
            await rig.tracker.subscribe("bot", 1, 7, name)
        with pytest.raises(ValueError, match="3 位"):
            await rig.tracker.subscribe("bot", 1, 7, "Fourth")
        restored = PetStore(rig.store.path)
        assert (
            next(r for r in restored.rows("bot", 1) if r["player"] == "player")["last_match"] == 10
        )
        assert restored.rows("another-bot") == []

    run(scenario())


def test_shared_fetch_render_and_per_group_retry(rig):
    async def scenario():
        for group in (1, 2):
            await rig.tracker.subscribe("bot", group, 7, "Player")
        rig.source.reset_mock()
        rig.source.return_value = Snapshot("Player", 15, rig.now[0], 41)
        due(rig)
        rig.send.side_effect = [None, RuntimeError("send failed")]
        await rig.tracker.tick("bot")
        assert rig.source.await_count == rig.render.await_count == 1
        assert row(rig, 1)["last_match"] == 15
        assert row(rig, 2)["last_match"] == 10
        assert row(rig, 2)["failures"] == 1
        rig.send.side_effect = None
        rig.now[0] += 600
        await rig.tracker.tick("bot")
        assert rig.send.await_count == 3  # No replay to successful group.
        assert row(rig, 2)["last_match"] == 15
        assert row(rig, 1)["pushes"] == row(rig, 2)["pushes"] == 1

    run(scenario())


def test_disabled_expiry_and_resubscription(rig):
    async def scenario():
        await rig.tracker.subscribe("bot", 1, 7, "Player")
        rig.source.reset_mock()
        rig.tracker.enabled = lambda *_: False
        rig.now[0] += 8 * DAY
        await rig.tracker.tick("bot")
        rig.source.assert_not_called()
        rig.send.assert_not_called()
        rig.tracker.enabled = lambda *_: True
        due(rig)
        await rig.tracker.tick("bot")
        assert row(rig)["state"] == "expired"
        assert rig.send.await_count == 1
        await rig.tracker.tick("bot")
        assert rig.send.await_count == 1
        await rig.tracker.subscribe("bot", 1, 7, "Player")
        assert row(rig)["state"] == "active"
        assert row(rig)["pushes"] == 1
        assert row(rig)["expires"] == rig.now[0] + 7 * DAY

    run(scenario())


def test_inactivity_once_resets_after_new_match_and_ignores_regression(rig):
    async def scenario():
        await rig.tracker.subscribe("bot", 1, 7, "Player")
        rig.now[0] += 3 * DAY
        await rig.tracker.tick("bot")
        assert rig.send.await_count == 1
        assert row(rig)["inactivity_match"] == 10
        due(rig)
        await rig.tracker.tick("bot")
        assert rig.send.await_count == 1
        rig.source.return_value = Snapshot("Player", 9, 1, 41)
        due(rig)
        await rig.tracker.tick("bot")
        assert row(rig)["last_match"] == 10
        assert rig.send.await_count == 1
        rig.source.return_value = Snapshot("Player", 11, rig.now[0], 41)
        due(rig)
        await rig.tracker.tick("bot")
        rig.now[0] += 3 * DAY
        await rig.tracker.tick("bot")
        assert rig.send.await_count == 3
        assert row(rig)["inactivity_match"] == 11

    run(scenario())


def test_cancel_or_disable_during_render_prevents_send(rig):
    async def scenario():
        await rig.tracker.subscribe("bot", 1, 7, "Player")
        old = row(rig)
        rig.source.return_value = Snapshot("Player", 11, rig.now[0], 41)
        due(rig)

        async def cancel(snapshot):
            rig.store.remove("bot", 1, "Player")
            rig.store.add("bot", 1, 7, Snapshot("Player", 11, rig.now[0], 41), rig.now[0])
            return b"image"

        rig.render.side_effect = cancel
        await rig.tracker.tick("bot")
        rig.send.assert_not_called()
        assert rig.store.current(old) is None
        assert row(rig)["last_match"] == 11
        rig.source.return_value = Snapshot("Player", 12, rig.now[0], 41)

        async def disable(snapshot):
            rig.tracker.enabled = lambda *_: False
            return b"image"

        rig.render.side_effect = disable
        await rig.tracker.tick("bot")
        rig.send.assert_not_called()

    run(scenario())


def test_only_explicit_lookup_not_found_stops_tracking(rig):
    async def scenario():
        await rig.tracker.subscribe("bot", 1, 7, "Player")
        rig.source.side_effect = UpstreamUnavailable("temporary")
        due(rig)
        await rig.tracker.tick("bot")
        assert row(rig)["state"] == "active"
        rig.source.side_effect = PlayerNotFound("Player")
        due(rig)
        await rig.tracker.tick("bot")
        assert row(rig)["state"] == "missing"
        assert rig.store.remove("bot", 1, "Player")

    run(scenario())


@pytest.mark.parametrize(
    "age,delay", [(60, 120), (7200, 600), (DAY, 1800), (7 * DAY, 7200), (30 * DAY, 21600)]
)
def test_poll_intervals(age, delay):
    assert interval(100, 100 + age) == delay
    assert interval(None, 100) == 21600


def test_source_sync_and_fresh_reads(tmp_path):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if "player-sync" in request.url.path:
            return httpx.Response(200, json={"player_name": "Player"})
        return httpx.Response(
            200,
            json={
                "matches": [
                    {
                        "nickname": "Player",
                        "gameId": len(calls),
                        "seasonId": 41,
                        "matchingMode": 6,
                        "startDtm": "2026-11-03T12:00:00+0900",
                    }
                ]
            },
        )

    async def scenario():
        async with AsyncERBSClient(
            ERBSConfig(private_database_path=tmp_path / "cache.db"),
            transport=httpx.MockTransport(handle),
        ) as client:
            source = PetSource(client)
            first, second = await source("Player"), await source("Player")
            assert first.match_id != second.match_id
            assert first.season == 0 and first.started_at is not None
            assert len(calls) == 4

    run(scenario())


@pytest.mark.parametrize(
    "payload,exception", [({"not_found": True}, PlayerNotFound), ({}, UpstreamUnavailable)]
)
def test_source_rejects_unconfirmed_identity(tmp_path, payload, exception):
    async def scenario():
        async with AsyncERBSClient(
            ERBSConfig(private_database_path=tmp_path / "cache.db"),
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
        ) as client:
            with pytest.raises(exception):
                await PetSource(client)("Player")

    run(scenario())


def test_match_validation_and_renderer(tmp_path):
    player = {
        "nickname": "Player",
        "gameId": 99,
        "teamNumber": 1,
        "gameRank": 1,
        "characterNum": 1,
        "equipment": [100],
        "traitFirstCore": 10,
        "tacticalSkillGroup": 60,
    }
    tables = {
        "characters": [{"id": 1, "name": "实验体"}],
        "items": [{"id": 100, "name": "武器"}],
        "trait-skills": {"traitSkills": [{"id": 10, "name": "潜能"}]},
        "tactical-skills": {"tacticalSkills": [{"id": 60, "name": "战术"}]},
    }

    async def metadata(name):
        return tables[name] if isinstance(tables[name], dict) else {name: tables[name]}

    async def scenario():
        client = SimpleNamespace(metadata=metadata)
        card = await build_detail_card(client, {"matches": [player]}, "Player", 99)
        item = card.sections[0]["items"][0]
        assert item["focused"] and item["tactical"]["name"] == "战术"
        renderer = MatchDetailRenderer(
            ERBSConfig(download_render_assets=False, private_database_path=tmp_path / "cache.db")
        )
        try:
            image = await renderer.render(card)
            assert image.startswith(b"\x89PNG")
        finally:
            await renderer.close()
        with pytest.raises(UpstreamUnavailable, match="不匹配"):
            await build_detail_card(client, {"matches": [player]}, "Player", 98)
        with pytest.raises(UpstreamUnavailable, match="未找到"):
            await build_detail_card(client, {"matches": [player]}, "Other", 99)

    run(scenario())


def test_command_parser_and_private_guard():
    assert parse_command('#BS 群宠 添加 "Player Name"').args == ("添加", "Player Name")
    assert parse_command("#BS 对局 99 Player 41").action == "对局"
    assert "群聊" in run(pet_jobs.command(None, object(), ("列表",)))


def test_pending_expiry_failure_is_cancelled_by_resubscription(rig):
    async def scenario():
        await rig.tracker.subscribe("bot", 1, 7, "Player")
        rig.now[0] += 7 * DAY
        rig.send.side_effect = RuntimeError("send failed")
        await rig.tracker.tick("bot")
        assert row(rig)["state"] == "active"
        assert row(rig)["failures"] == 1
        assert row(rig)["next_check"] > rig.now[0]
        rig.send.side_effect = None
        rig.source.return_value = Snapshot("Player", 15, rig.now[0], 41)
        await rig.tracker.subscribe("bot", 1, 7, "Player")
        await rig.tracker.tick("bot")
        assert rig.send.await_count == 1  # Old expiry message must never be sent.
        assert row(rig)["state"] == "active"

    run(scenario())


def test_regular_member_cannot_modify_subscriptions(monkeypatch):
    class Event:
        pass

    monkeypatch.setattr(pet_jobs, "GroupMessageEvent", Event)
    permission = AsyncMock(return_value=False)
    tracker = AsyncMock()
    monkeypatch.setattr(pet_jobs, "LevelAdmin", permission)
    monkeypatch.setattr(pet_jobs, "get_tracker", tracker)
    assert "群管理员" in run(pet_jobs.command(object(), Event(), ("添加", "Player")))
    assert "群管理员" in run(pet_jobs.command(object(), Event(), ("取消", "Player")))
    tracker.assert_not_called()


def test_success_reply_does_not_fall_through_to_normal_query(monkeypatch):
    from nonebot.exception import FinishedException

    from kanamibot.plugins import er_dak as plugin

    monkeypatch.setattr(plugin, "_check_cooldown", lambda event: None)
    monkeypatch.setattr(plugin, "_get_runtime", AsyncMock())
    normal_query = AsyncMock()
    monkeypatch.setattr(plugin, "_card_for", normal_query)
    monkeypatch.setattr(pet_jobs, "command", AsyncMock(return_value="success"))
    matcher = SimpleNamespace(finish=AsyncMock(side_effect=FinishedException))
    event = SimpleNamespace(get_plaintext=lambda: "#BS 群宠 列表")
    with pytest.raises(FinishedException):
        run(plugin.handle_er(object(), event, matcher))
    matcher.finish.assert_awaited_once_with("success")
    normal_query.assert_not_called()


def test_sync_rate_limit_retries_then_fails_without_reading_matches(tmp_path, monkeypatch):
    from kanamibot.plugins.er_dak import match_detail

    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"retry_after": 1000})

    monkeypatch.setattr(match_detail.asyncio, "sleep", AsyncMock())

    async def scenario():
        async with AsyncERBSClient(
            ERBSConfig(private_database_path=tmp_path / "cache.db"),
            transport=httpx.MockTransport(handle),
        ) as client:
            with pytest.raises(RateLimited):
                await PetSource(client)("Player")
        assert len(calls) == 3 and all("player-sync" in path for path in calls)

    run(scenario())


@pytest.mark.parametrize("http_limited", [False, True])
def test_subscribe_uses_verified_baseline_when_sync_is_limited(tmp_path, monkeypatch, http_limited):
    from kanamibot.plugins.er_dak import match_detail

    calls = []

    def handle(request):
        calls.append(request.url.path)
        if "player-sync" in request.url.path:
            return httpx.Response(429 if http_limited else 200, json={"retry_after": 1000})
        if request.url.path.endswith("/profile"):
            return httpx.Response(200, json={"player": {"name": "Player"}})
        return httpx.Response(
            200,
            json={
                "matches": [
                    {
                        "nickname": "Player",
                        "gameId": 99,
                        "seasonId": 41,
                        "startDtm": "2026-11-04T12:00:00+0800",
                    }
                ]
            },
        )

    monkeypatch.setattr(match_detail.asyncio, "sleep", AsyncMock())

    async def scenario():
        config = ERBSConfig(private_database_path=tmp_path / "cache.db", retry_count=0)
        async with AsyncERBSClient(config, transport=httpx.MockTransport(handle)) as client:
            source = PetSource(client)
            store = PetStore(tmp_path / "pets.db")
            tracker = PetTracker(
                store,
                source,
                AsyncMock(),
                AsyncMock(),
                lambda *_: True,
                subscription_source=source.for_subscription,
            )
            assert await tracker.subscribe("bot", 1, 7, "Player")
            baseline = store.rows("bot", 1)[0]
            assert baseline["last_match"] == 99
            assert calls[-2:] == [
                "/api/v1/players/Player/profile",
                "/api/v1/players/Player/matches",
            ]
            # Polling still requires sync success; registration fallback is never used here.
            calls.clear()
            with pytest.raises(RateLimited):
                await source("Player")
            assert all("player-sync" in path for path in calls)

    run(scenario())


@pytest.mark.parametrize(
    "profile,matches",
    [({}, []), ({"player": {"name": "Player"}}, [{"nickname": "Other", "gameId": 99}])],
)
def test_sync_limited_registration_rejects_unverified_data(tmp_path, monkeypatch, profile, matches):
    from kanamibot.plugins.er_dak import match_detail

    def handle(request):
        if "player-sync" in request.url.path:
            return httpx.Response(200, json={"retry_after": 1000})
        return httpx.Response(
            200, json=profile if request.url.path.endswith("/profile") else {"matches": matches}
        )

    monkeypatch.setattr(match_detail.asyncio, "sleep", AsyncMock())

    async def scenario():
        async with AsyncERBSClient(
            ERBSConfig(private_database_path=tmp_path / "cache.db"),
            transport=httpx.MockTransport(handle),
        ) as client:
            source = PetSource(client)
            store = PetStore(tmp_path / "pets.db")
            tracker = PetTracker(
                store,
                source,
                AsyncMock(),
                AsyncMock(),
                lambda *_: True,
                subscription_source=source.for_subscription,
            )
            with pytest.raises(UpstreamUnavailable):
                await tracker.subscribe("bot", 1, 7, "Player")
            assert store.rows("bot") == []

    run(scenario())


def test_group_pet_help_uses_neutral_wording():
    assert "我" not in pet_jobs.HELP
    assert "群宠订阅" in pet_jobs.HELP
