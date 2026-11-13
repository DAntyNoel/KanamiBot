from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .client import BackendError

TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).with_name("templates")),
    autoescape=select_autoescape(["html"]),
)


def render_html(**context) -> str:
    return TEMPLATES.get_template("boss.html").render(**context)


async def _screenshot(html: str, channel: str) -> bytes:
    from playwright.async_api import Error, async_playwright

    try:
        async with async_playwright() as playwright:
            options = {"headless": True}
            if channel:
                options["channel"] = channel
            browser = await playwright.chromium.launch(**options)
            try:
                page = await browser.new_page(
                    viewport={"width": 1280, "height": 900},
                    device_scale_factor=1,
                    java_script_enabled=False,
                )
                # All source images are inlined; never load scripts or remote URLs.
                await page.route("**/*", lambda route: route.abort())
                await page.set_content(html, wait_until="load", timeout=20000)
                height = await page.locator("#template").evaluate("el => el.scrollHeight")
                if height > 20000:
                    raise BackendError("PRTS 页面过长，已改用文字资料与原文链接。")
                return await page.locator("#template").screenshot(type="png", timeout=30000)
            finally:
                await browser.close()
    except Error as exc:
        raise BackendError("PRTS 卡片暂时无法渲染。") from exc


def _render_in_thread(html: str, channel: str) -> bytes:
    factory = asyncio.ProactorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(_screenshot(html, channel))


async def render_png(html: str, channel: str = "") -> bytes:
    # The existing Windows installation provides Chrome. Linux defaults to the
    # Playwright-managed Chromium unless a channel is explicitly configured.
    channel = channel or ("chrome" if sys.platform == "win32" else "")
    return await asyncio.to_thread(_render_in_thread, html, channel)
