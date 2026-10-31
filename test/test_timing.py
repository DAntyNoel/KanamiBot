from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from nonebot.adapters import Bot as BaseBot
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.dependencies import Dependent
from nonebot.exception import FinishedException
from nonebot.internal.params import EventParam

from kanamibot.core import timing


@pytest.fixture(autouse=True)
def isolated_timing(monkeypatch):
    monkeypatch.setattr(timing, "_enabled", False)
    monkeypatch.setattr(timing, "_normal_log_level", None)
    records = []
    monkeypatch.setattr(
        timing.logger, "debug", lambda template, *args: records.append(template.format(*args))
    )
    yield records
    assert timing._trace.get() is None
    assert timing._span.get() == "-"


def test_info_does_not_read_clock_or_log(monkeypatch, isolated_timing):
    timing.configure_timing("INFO")
    monkeypatch.setattr(timing, "perf_counter", lambda: pytest.fail("timing disabled"))

    @timing.timed()
    def component(value):
        return value

    value = object()
    assert component(value) is value
    with timing.timing_span("block"):
        pass
    assert isolated_timing == []


@pytest.mark.parametrize("level", ["DEBUG", "debug", "TRACE", 10, 5])
def test_debug_levels_enable_timing(level, isolated_timing):
    timing.configure_timing(level)
    with timing.timing_span("component"):
        pass
    assert "phase=begin" in isolated_timing[0]
    assert "phase=end" in isolated_timing[1]


def test_elapsed_uses_monotonic_clock(monkeypatch, isolated_timing):
    timing.configure_timing("DEBUG")
    ticks = iter([1.0, 1.0, 1.0, 1.125, 1.125])
    monkeypatch.setattr(timing, "perf_counter", lambda: next(ticks))
    with timing.timing_span("component"):
        pass
    assert "elapsed_ms=125.000" in isolated_timing[-1]


@pytest.mark.asyncio
async def test_nested_async_and_thread_spans_share_trace(isolated_timing):
    timing.configure_timing("DEBUG")

    @timing.timed("child")
    def child():
        return 7

    @timing.timed("parent")
    async def parent():
        return await asyncio.to_thread(child)

    assert await parent() == 7
    traces = {row.split("trace=")[1].split()[0] for row in isolated_timing}
    assert len(traces) == 1
    assert any("span=2 parent=1" in row and "component=child" in row for row in isolated_timing)


@pytest.mark.asyncio
async def test_concurrent_roots_do_not_mix_trace_ids(isolated_timing):
    timing.configure_timing("DEBUG")

    @timing.timed("request")
    async def request():
        await asyncio.sleep(0)

    await asyncio.gather(request(), request())
    begins = [row for row in isolated_timing if "phase=begin" in row]
    assert len({row.split("trace=")[1].split()[0] for row in begins}) == 2
    assert all("parent=-" in row for row in begins)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception", "status"),
    [
        (ValueError("secret"), "error:ValueError"),
        (TimeoutError(), "error:TimeoutError"),
        (asyncio.CancelledError(), "cancelled"),
        (FinishedException(), "finished"),
    ],
)
async def test_errors_and_matcher_control_flow_are_preserved(exception, status, isolated_timing):
    timing.configure_timing("DEBUG")

    @timing.timed("failure")
    async def failure():
        raise exception

    with pytest.raises(type(exception)) as caught:
        await failure()
    assert caught.value is exception
    assert f"status={status}" in isolated_timing[-1]
    assert "secret" not in " ".join(isolated_timing)


def test_nonebot_dependency_injection_with_postponed_annotations():
    @timing.timed()
    async def handler(event: MessageEvent) -> None:
        pass

    assert inspect.iscoroutinefunction(handler)
    assert inspect.signature(handler).parameters["event"].annotation is MessageEvent
    assert Dependent.parse(call=handler, allow_types=(EventParam,)).call is handler


def test_generator_requires_explicit_consumption_span():
    with pytest.raises(TypeError):

        @timing.timed()
        def generator():
            yield 1


def test_info_install_does_not_patch_framework(monkeypatch):
    original = BaseBot.call_api
    monkeypatch.setattr(timing, "_installed", False)
    timing.install_debug_timing("INFO")
    assert not timing._installed
    assert BaseBot.call_api is original


@pytest.mark.parametrize(
    "startup,restored",
    [("INFO", "INFO"), ("WARNING", "WARNING"), ("DEBUG", "INFO"), ("TRACE", "INFO"), (30, 30)],
)
def test_runtime_switch_updates_console_and_restores_normal_level(monkeypatch, startup, restored):
    import nonebot

    monkeypatch.setattr(timing, "_installed", True)
    driver = SimpleNamespace(config=SimpleNamespace(log_level=startup))
    monkeypatch.setattr(nonebot, "get_driver", lambda: driver)
    configured = []
    monkeypatch.setattr(timing.logger, "configure", lambda **kwargs: configured.append(kwargs))
    timing.install_debug_timing(startup)
    assert timing.set_debug_mode(True) == "DEBUG"
    assert timing.debug_enabled()
    assert driver.config.log_level == "DEBUG"
    assert configured[-1] == {"extra": {"nonebot_log_level": "DEBUG"}}
    assert timing.set_debug_mode(False) == restored
    assert not timing.debug_enabled()
    assert driver.config.log_level == restored
    assert configured[-1] == {"extra": {"nonebot_log_level": restored}}


@pytest.mark.asyncio
async def test_debug_boundaries_trace_http_and_send_without_sensitive_data(
    monkeypatch,
    isolated_timing,
):
    import nonebot.message as message
    from nonebot.matcher import Matcher

    # Save all installation targets so monkeypatch restores them on fixture teardown.
    for obj, name in [
        (Bot, "handle_event"),
        (Matcher, "run"),
        (message, "_apply_run_preprocessors"),
        (BaseBot, "call_api"),
        (httpx.AsyncClient, "send"),
        (httpx.Client, "send"),
    ]:
        monkeypatch.setattr(obj, name, getattr(obj, name))
    monkeypatch.setattr(timing, "_installed", False)
    bot = object.__new__(Bot)
    bot.adapter = SimpleNamespace(_call_api=AsyncMock(return_value={"message_id": 9}))
    event = SimpleNamespace(message_id=123, get_type=lambda: "message")

    async def fake_event(bot, event):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
        ) as client:
            await client.get("https://example.org/private-path?api_key=secret")
        await bot.call_api("send_msg", message="sensitive message")

    monkeypatch.setattr(Bot, "handle_event", fake_event)
    timing.install_debug_timing("DEBUG")
    installed = BaseBot.call_api
    timing.install_debug_timing("DEBUG")
    assert BaseBot.call_api is installed
    await bot.handle_event(event)
    records = "\n".join(isolated_timing)
    assert "component=event.total" in records
    assert "component=http.GET@example.org:None" in records
    assert "component=onebot.send_msg" in records
    assert "event_id=123" in records
    assert "secret" not in records
    assert "private-path" not in records
    assert "sensitive message" not in records
