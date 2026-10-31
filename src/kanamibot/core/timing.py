"""Opt-in DEBUG timing shared by plugins, workers and framework boundaries."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from itertools import count
from time import perf_counter
from typing import Any
from uuid import uuid4

from nonebot.log import logger

from .debug_log import configure_debug_file

_enabled = False
_installed = False
_normal_log_level: str | int | None = None


@dataclass
class _Trace:
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    started: float = field(default_factory=lambda: perf_counter())
    spans: Iterator[int] = field(default_factory=lambda: count(1))


_trace: ContextVar[_Trace | None] = ContextVar("kanamibot_timing_trace", default=None)
_span: ContextVar[str] = ContextVar("kanamibot_timing_span", default="-")


def configure_timing(log_level: str | int) -> None:
    """Use the effective NoneBot startup log level, never a separate debug switch."""
    global _enabled
    _enabled = (
        log_level <= 10 if isinstance(log_level, int) else log_level.upper() in {"DEBUG", "TRACE"}
    )


def debug_enabled() -> bool:
    return _enabled


def set_debug_mode(enabled: bool) -> str | int:
    """Switch console verbosity and timing for this process, leaving .env untouched."""
    from nonebot import get_driver

    driver = get_driver()
    # Bootstrap may be bypassed by another launcher; capture its ordinary level first.
    if _normal_log_level is None:
        install_debug_timing(driver.config.log_level)
    level = "DEBUG" if enabled else _normal_log_level
    assert level is not None
    driver.config.log_level = level
    # Same mechanism used by nonebot.init; keep existing sinks and the plugin patcher.
    logger.configure(extra={"nonebot_log_level": level})
    install_debug_timing(level)
    return level


def _outcome(exc: BaseException) -> str:
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    # Matcher.finish/reject/pause are control flow, not plugin failures.
    from nonebot.exception import FinishedException, PausedException, RejectedException

    if isinstance(exc, (FinishedException, PausedException, RejectedException)):
        return type(exc).__name__.removesuffix("Exception").lower()
    return f"error:{type(exc).__name__}"


@contextmanager
def timing_span(component: str, *, new_trace: bool = False) -> Iterator[None]:
    """Inclusive wall time; ContextVars propagate through tasks and asyncio.to_thread.

    Labels must be static component names: never pass message text, API keys or URLs.
    A new root is created automatically outside an existing trace (e.g. scheduled jobs).
    """
    if not _enabled:
        yield
        return
    trace = None if new_trace else _trace.get()
    root = trace is None
    if root:
        trace = _Trace()
    assert trace is not None
    trace_token = _trace.set(trace)
    parent = "-" if root else _span.get()
    span = str(next(trace.spans))
    span_token = _span.set(span)
    started = perf_counter()
    status = "ok"

    def emit(phase: str, elapsed: float) -> None:
        if not _enabled:
            return
        logger.debug(
            "[timing] trace={} span={} parent={} phase={} component={} "
            "elapsed_ms={:.3f} since_start_ms={:.3f} status={}",
            trace.id,
            span,
            parent,
            phase,
            component,
            elapsed * 1000,
            (perf_counter() - trace.started) * 1000,
            status,
        )

    try:
        emit("begin", 0)
        try:
            yield
        except BaseException as exc:
            status = _outcome(exc)
            raise
        finally:
            emit("end", perf_counter() - started)
    finally:
        _span.reset(span_token)
        _trace.reset(trace_token)


def timed(component: str | None = None) -> Callable:
    """Decorate a normal/async function without changing returns or exception behavior.

    Put @timed() immediately above def, below Matcher/Depends/scheduler decorators.
    Async generators are intentionally unsupported; time their consumer with timing_span.
    """

    def decorate(func: Callable) -> Callable:
        if inspect.isgeneratorfunction(func) or inspect.isasyncgenfunction(func):
            raise TypeError("Use timing_span around generator consumption instead")
        label = component or f"{func.__module__}.{func.__qualname__}"
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if not _enabled:
                    return await func(*args, **kwargs)
                with timing_span(label):
                    return await func(*args, **kwargs)

            wrapper = async_wrapper
        else:

            @wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                if not _enabled:
                    return func(*args, **kwargs)
                with timing_span(label):
                    return func(*args, **kwargs)

            wrapper = sync_wrapper

        # NoneBot evaluates postponed parameter annotations against wrapper.__globals__.
        # Resolve them in the original namespace so dependency injection still works.
        signature = inspect.signature(func)
        parameters = []
        for param in signature.parameters.values():
            annotation = param.annotation
            if isinstance(annotation, str):
                try:
                    annotation = eval(annotation, func.__globals__)
                except (NameError, TypeError):
                    pass  # Forward references to an enclosing class can remain unresolved.
            parameters.append(param.replace(annotation=annotation))
        wrapper.__signature__ = signature.replace(parameters=parameters)
        return wrapper

    return decorate


def install_debug_timing(log_level: str | int) -> None:
    """Install process-local DEBUG boundaries before plugins load; safe to call twice.

    Normal startup does not patch framework or HTTP clients. No upstream files change.
    """
    global _installed, _normal_log_level
    configure_timing(log_level)
    configure_debug_file(_enabled)
    if _normal_log_level is None:
        _normal_log_level = "INFO" if _enabled else log_level
    if not _enabled or _installed:
        return

    import httpx
    import nonebot.message as message
    from nonebot.adapters import Bot
    from nonebot.adapters.onebot.v11 import Bot as OneBot
    from nonebot.matcher import Matcher

    event_handler = OneBot.handle_event
    matcher_run = Matcher.run
    preprocess = message._apply_run_preprocessors
    call_api = Bot.call_api
    async_send = httpx.AsyncClient.send
    sync_send = httpx.Client.send

    @wraps(event_handler)
    async def handle_event(bot, event):
        if not _enabled:
            return await event_handler(bot, event)
        with timing_span("event.total", new_trace=True):
            if _enabled:
                logger.debug(
                    "[timing] trace={} event_id={} event_type={}",
                    _trace.get().id,
                    getattr(event, "message_id", "-"),
                    event.get_type(),
                )
            await event_handler(bot, event)

    @wraps(matcher_run)
    async def run(matcher, *args, **kwargs):
        if not _enabled:
            return await matcher_run(matcher, *args, **kwargs)
        with timing_span(f"matcher.{matcher.plugin_name or 'unknown'}"):
            return await matcher_run(matcher, *args, **kwargs)

    @wraps(preprocess)
    async def before_run(*args, **kwargs):
        if not _enabled:
            return await preprocess(*args, **kwargs)
        plugin = getattr(kwargs.get("matcher"), "plugin_name", None) or "unknown"
        with timing_span(f"matcher.preprocess.{plugin}"):
            return await preprocess(*args, **kwargs)

    @wraps(call_api)
    async def api(bot, api, **data):
        if not _enabled:
            return await call_api(bot, api, **data)
        with timing_span(f"onebot.{api}"):
            return await call_api(bot, api, **data)

    def http_label(request: httpx.Request, stream: bool) -> str:
        # No credentials, path, query string, headers or body in timing records.
        suffix = ".headers" if stream else ""
        return f"http.{request.method}@{request.url.host}:{request.url.port}{suffix}"

    @wraps(async_send)
    async def send_async(client, request, *args, **kwargs):
        if not _enabled:
            return await async_send(client, request, *args, **kwargs)
        with timing_span(http_label(request, kwargs.get("stream", False))):
            return await async_send(client, request, *args, **kwargs)

    @wraps(sync_send)
    def send_sync(client, request, *args, **kwargs):
        if not _enabled:
            return sync_send(client, request, *args, **kwargs)
        with timing_span(http_label(request, kwargs.get("stream", False))):
            return sync_send(client, request, *args, **kwargs)

    OneBot.handle_event = handle_event
    Matcher.run = run
    message._apply_run_preprocessors = before_run
    Bot.call_api = api
    httpx.AsyncClient.send = send_async
    httpx.Client.send = send_sync
    _installed = True
    logger.debug("[timing] DEBUG component timing enabled")
