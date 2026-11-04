from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from kanamibot.lens_session import (
    CookieFile,
    authorize,
    header_cookies,
    is_lens_result,
    normalize_cookies,
)


def cookie(value="test", domain=".google.com", **kwargs):
    return {"name": "NID", "value": value, "domain": domain, "path": "/", **kwargs}


def test_cookie_import_scope_expiration_and_storage_state():
    rows = normalize_cookies(
        {
            "cookies": [
                cookie(),
                cookie(domain="evilgoogle.com"),
                cookie(domain="google.com.evil.example"),
                cookie(expires=time.time() - 10),
                cookie("host", "www.google.com", name="__Host-Test", secure=True),
            ],
            "origins": [{"origin": "https://other.example", "localStorage": []}],
        }
    )
    assert len(rows) == 2
    assert rows[1]["domain"] == "www.google.com" and rows[1]["name"] == "__Host-Test"
    with pytest.raises(ValueError, match="__Host-"):
        normalize_cookies([cookie(name="__Host-Bad", secure=True)])
    with pytest.raises(ValueError):
        normalize_cookies([cookie("bad\r\nvalue")])
    with pytest.raises(ValueError):
        normalize_cookies({"wrong": []})


def test_header_import_preserves_equals_and_host_scope():
    rows = header_cookies("NID=value=with=equals; __Host-Test=host; badfragment")
    assert rows[0]["value"] == "value=with=equals"
    assert rows[0]["domain"] == ".google.com"
    assert rows[1]["domain"] == "www.google.com" and rows[1]["secure"]


def test_cookie_exchange_reload_and_newer_login_wins(tmp_path):
    async def scenario():
        path = tmp_path / "cookies.json"
        path.write_text(json.dumps([cookie("old")]), encoding="utf-8")
        store = CookieFile(path)
        context = AsyncMock()
        context.cookies.return_value = [cookie("stale"), cookie(domain="example.com")]
        assert await store.reload(context)
        context.clear_cookies.assert_awaited_once_with(domain=".google.com")
        assert context.add_cookies.call_args.args[0][0]["value"] == "old"
        assert not await store.reload(context)
        path.write_text(json.dumps({"cookies": [cookie("new-login")]}), encoding="utf-8")
        assert not await store.save(context, source="successful-query")
        assert json.loads(path.read_text())["cookies"][0]["value"] == "new-login"
        assert await store.reload(context)
        context.cookies.return_value = [cookie("renewed"), cookie(domain="example.com")]
        assert await store.save(context, source="successful-query")
        saved = json.loads(path.read_text())
        assert len(saved["cookies"]) == 1 and saved["cookies"][0]["value"] == "renewed"
        assert saved["source"] == "successful-query"
        assert not list(tmp_path.glob("*.tmp"))

    asyncio.run(scenario())


def test_cookie_header_seeds_only_missing_file(tmp_path):
    async def scenario():
        store = CookieFile(tmp_path / "cookies.json")
        context = AsyncMock()
        context.cookies.return_value = []
        assert await store.reload(context, "NID=seed")
        assert context.add_cookies.call_args.args[0][0]["value"] == "seed"
        context.cookies.return_value = [cookie("updated")]
        assert await store.save(context, source="successful-query")
        context.add_cookies.reset_mock()
        assert not await store.reload(context, "NID=seed")
        context.add_cookies.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "url,snapshot,expected",
    [
        ("https://www.google.com/search?vsrid=test", {"results": True}, True),
        ("https://www.google.com/search?udm=48", {"results": True}, True),
        ("https://www.google.com/search?vsrid=test", {"results": True, "blocked": True}, False),
        ("https://www.google.com/search?vsrid=test", {"results": False}, False),
        ("https://www.google.com/search?q=normal", {"results": True}, False),
        ("https://google.com.evil.example/search?vsrid=test", {"results": True}, False),
        ("https://lens.google.com/", {"results": True}, False),
    ],
)
def test_success_requires_lens_results_not_just_cookies(url, snapshot, expected):
    assert is_lens_result(url, snapshot) is expected


@pytest.mark.parametrize("solve", [False, True])
def test_manual_verification_browser_flow_and_automatic_save(tmp_path, solve):
    from playwright.async_api import async_playwright

    async def scenario():
        async with async_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Playwright Chromium is not installed")
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            store = CookieFile(tmp_path / "cookies.json")
            await context.add_cookies([cookie("previous")])
            assert await store.save(context, source="old-session")
            uploads = []
            submitted = asyncio.Event()

            async def serve(route):
                if "/v3/upload" in route.request.url:
                    uploads.append(route.request.post_data_buffer)
                    submitted.set()
                    await route.fulfill(
                        content_type="text/html",
                        body=(
                            "<script>history.replaceState({},'', '/search?vsrid=fixture')</script>"
                            '<form action="/sorry/">Unusual traffic</form>'
                        ),
                    )
                elif "/search" in route.request.url:
                    await route.fulfill(
                        content_type="text/html",
                        body=(
                            '<form action="/sorry/">'
                            "Our systems have detected unusual traffic</form>"
                        ),
                    )
                else:
                    await route.fulfill(content_type="text/html", body="<body>Lens</body>")

            await context.route("**/*", serve)

            async def manual_solve():
                await submitted.wait()
                page = context.pages[0]
                await page.wait_for_url("**/search?vsrid=fixture", wait_until="domcontentloaded")
                # Simulate the administrator finishing the challenge in the same page.
                await context.add_cookies([cookie("verified")])
                await page.evaluate("""() => {
                    document.body.innerHTML = '<a href="https://x.com/artist">Lens result</a>';
                }""")

            task = asyncio.create_task(manual_solve()) if solve else None
            try:
                result = await authorize(context, store, timeout=5, poll_interval=0.05)
                assert result is solve
                saved = json.loads(store.path.read_text())
                assert saved["source"] == ("manual-verification" if solve else "old-session")
                assert saved["cookies"][0]["value"] == ("verified" if solve else "previous")
                assert len(uploads) == 1 and b'name="encoded_image"' in uploads[0]
            finally:
                if task:
                    await task
                await context.close()
                await browser.close()

    asyncio.run(scenario())
