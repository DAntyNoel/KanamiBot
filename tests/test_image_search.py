from __future__ import annotations

import asyncio
import base64
import gzip
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import nonebot
import pytest
from PIL import Image

nonebot.init(_env_file=None)
assert nonebot.load_plugin("kanamibot.plugins.image_search") is not None

from kanamibot.plugins import image_search as plugin  # noqa: E402
from kanamibot.plugins.image_search import engines, network  # noqa: E402
from kanamibot.plugins.image_search.config import Config  # noqa: E402
from kanamibot.plugins.image_search.images import prepare_image, read_image  # noqa: E402
from kanamibot.plugins.image_search.results import (  # noqa: E402
    Account,
    Candidate,
    Result,
    format_reply,
    match_account,
    parse_iqdb,
    parse_saucenao,
    parse_tracemoe,
    select_accounts,
    title_name,
    unwrap_url,
)
from kanamibot.plugins.image_search.storage import Store  # noqa: E402


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("#搜图", True),
        ("#搜图 帮助", True),
        ("#搜图\u3000https://example.com/a", True),
        ("@12345678 #搜图", True),
        ("@12345678\u3000#搜图 帮助", True),
        ("@12345678 @87654321 #搜图", True),
        ("@12345678 #搜图片", False),
        ("随便 @12345678 #搜图", False),
        ("#搜图片", False),
        ("/鱼鱼找鸡", False),
        ("随便 #搜图", False),
    ],
)
def test_command_boundaries(text, expected):
    assert run(plugin.command_rule(SimpleNamespace(get_plaintext=lambda: text))) is expected


@pytest.mark.parametrize("typed", [True, False])
def test_leading_mentions_preserve_command_arguments(typed):
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    message = Message(MessageSegment.text("@12345678") if typed else MessageSegment.at(12345678))
    message += MessageSegment.text(" #搜图 https://example.com/image.jpg")
    event = SimpleNamespace(get_plaintext=message.extract_plain_text)
    assert run(plugin.command_rule(event))
    assert plugin.command_text(event)[3:].strip() == "https://example.com/image.jpg"


@pytest.mark.parametrize(
    "url,platform,handle",
    [
        ("https://x.com/artist_123/status/42", "twitter", "artist_123"),
        ("https://www.pixiv.net/member.php?id=12345", "pixiv", "12345"),
        ("https://www.pixiv.net/en/users/12345", "pixiv", "12345"),
        ("https://weibo.com/u/12345678", "weibo", "12345678"),
        ("https://space.bilibili.com/123456", "bilibili", "123456"),
        ("https://www.bilibili.com/video/BV123?mid=123456", "bilibili", "123456"),
        ("https://instagram.com/artist_123/", "instagram", "artist_123"),
    ],
)
def test_account_urls(url, platform, handle):
    account = match_account(url)
    assert account and (account.platform, account.handle) == (platform, handle)


@pytest.mark.parametrize(
    "url",
    [
        "https://weibo.com/ttarticle/p/show?id=1234",
        "https://x.com/intent/tweet",
        "https://instagram.com/reel/1234",
        "https://x.com.evil.example/artist",
        "https://x.com@evil.example/artist",
        "https://www.pixiv.net/artworks/12345",
    ],
)
def test_not_account(url):
    assert match_account(url) is None


def test_saucenao_author_and_similarity():
    rows = parse_saucenao("""<div id="res0" class="result">
        <div class="resultsimilarityinfo">95.25%</div>
        <div class="resulttitle"><strong>作品名字</strong></div>
        <a href="https://www.pixiv.net/artworks/42">source</a>
        Member: <a href="https://www.pixiv.net/member.php?id=12345">画师名字</a></div>""")
    assert len(rows) == 1
    assert rows[0].similarity == 0.9525 and rows[0].author == "画师名字"
    result = Result(candidates=rows)
    select_accounts(result, True, 3)
    assert result.accounts[0].url == "https://www.pixiv.net/users/12345"
    assert "画师名字" in format_reply(result)


def test_iqdb_similarity_belongs_to_each_table():
    rows = parse_iqdb("""<table><a href="https://danbooru.donmai.us/posts/1">one</a>
        95% similarity</table><table><a href="https://yande.re/post/show/2">two</a>
        62% similarity</table><a href="https://irrelevant.example/help">help</a>""")
    assert [r.similarity for r in rows] == [0.95, 0.62]
    with pytest.raises(ValueError, match="排队"):
        parse_iqdb("Place in queue")


def test_trace_moe_threshold_dedupe_and_reply():
    rows = parse_tracemoe(
        {
            "result": [
                {
                    "similarity": 0.92,
                    "anilist": {"id": 42, "title": {"native": "动画名字"}},
                    "episode": 3,
                    "from": 135.6,
                },
                {"similarity": 0.91, "anilist": {"id": 42}},
                {"similarity": 0.86, "anilist": {"id": 43}},
            ]
        }
    )
    assert len(rows) == 1 and "02:15" in rows[0].title
    text = format_reply(Result(candidates=rows, used=["tracemoe"]))
    assert "动画名字 第3集" in text and "没有能确认" not in text


def test_consensus_filters_other_cosers_but_not_by_repeated_same_page():
    good = Candidate("lens", "https://x.com/good_123/status/1", "X阿薰kaOri (@good_123) / X")
    other = Candidate("lens", "https://x.com/other_123/status/2", "X其他coser (@other_123) / X")
    supporting = Candidate("lens", "https://example.com/post", "CN: 阿薰kaOri")
    result = Result(candidates=[good, other, supporting])
    select_accounts(result, True, 3)
    assert [a.handle for a in result.accounts] == ["good_123"] and result.name_dropped == 1
    loose = Result(candidates=[good, other, supporting])
    select_accounts(loose, False, 3)
    assert len(loose.accounts) == 2
    duplicate = Result(candidates=[good, good, other])
    select_accounts(duplicate, True, 3)
    assert len(duplicate.accounts) == 2


def test_artwork_filter_and_weibo_alias_dedupe():
    result = Result(
        candidates=[
            Candidate(
                "saucenao",
                "https://example.com/image",
                similarity=0.79,
                author_url="https://pixiv.net/users/123",
            ),
            Candidate(
                "saucenao",
                "https://weibo.com/12345678/abc",
                similarity=0.95,
                links=["https://weibo.com/n/person", "https://weibo.com/u/12345678"],
            ),
        ]
    )
    select_accounts(result, True, 3)
    assert result.artwork_dropped == 1
    assert len(result.accounts) == 1 and result.accounts[0].handle == "12345678"


def test_search_redirect_decode():
    target = "https://x.com/artist_123"
    token = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    assert unwrap_url("https://bing.com/ck/a?u=a1" + token) == target
    assert unwrap_url("https://www.google.com/url?q=https%3A%2F%2Fx.com%2Fartist_123") == target
    assert title_name('X吴二棒on X: "hello"') == "吴二棒"


def test_image_normalization_removes_metadata():
    output = BytesIO()
    image = Image.new("RGBA", (2000, 500), (255, 0, 0, 0))
    image.save(output, "PNG")
    data, mime = prepare_image(output.getvalue())
    with Image.open(BytesIO(data)) as prepared:
        assert prepared.size == (1600, 400) and prepared.mode == "RGB"
        assert prepared.getpixel((1, 1)) == (255, 255, 255)
        assert not prepared.getexif()
    assert mime == "image/jpeg"
    with pytest.raises(ValueError):
        prepare_image(b"not an image")


def test_cache_config_expiry_failure_and_quota(tmp_path):
    config = Config(image_search_cache_max=1, image_search_per_user_day=1)
    store = Store(tmp_path / "state.json", config)
    result = Result(used=["lens"], accounts=[Account("twitter", "abc", "https://x.com/abc")])
    store.put("first", result)
    store.put("second", result)
    assert store.cached("first") is None and store.cached("second").accounts == result.accounts
    store.data["cache"]["second"]["at"] = 0
    assert store.cached("second") is None
    store.put("broken", Result(used=["lens"], failures={"saucenao": "HTTP 429"}))
    assert store.cached("broken") is None
    key = store.cache_key(b"image")
    config.image_search_strict_consensus = False
    assert store.cache_key(b"image") != key
    store.charge("123")
    assert "用完" in Store(store.path, config).quota_reason("123")
    store.data["day"]["date"] = "2000-01-01"
    assert store.quota_reason("123") == ""


def test_download_blocks_private_and_redirect_upload(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop, "getaddrinfo", AsyncMock(return_value=[(2, 1, 6, "", ("127.0.0.1", 443))])
        )
        with pytest.raises(ValueError, match="内网"):
            await network.validate_url("https://example.com/a")
        monkeypatch.setattr(network, "validate_url", AsyncMock())
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    307, headers={"location": "https://evil.example/upload"}
                )
            )
        ) as client:
            with pytest.raises(ValueError, match="跨站"):
                await network.fetch(
                    client,
                    "https://saucenao.com/search.php",
                    method="POST",
                    files={"file": ("a.jpg", b"image", "image/jpeg")},
                )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"123456"))
        ) as client:
            with pytest.raises(ValueError, match="大小"):
                await network.fetch(client, "https://example.com/a", limit=5)

    run(scenario())


def test_partial_engine_failure_keeps_success(monkeypatch):
    async def request(client, engine, image, mime):
        if engine == "saucenao":
            raise ValueError("限流")
        return [Candidate(engine, "https://example.com/image")]

    monkeypatch.setattr(engines, "network_search", request)
    result = run(
        engines.reverse_search(
            b"image",
            "image/jpeg",
            Config(
                image_search_engines=["saucenao", "iqdb"],
            ),
            None,
        )
    )
    assert result.used == ["iqdb"] and result.failures == {"saucenao": "限流"}
    assert result.candidates[0].engine == "iqdb"


def test_compressed_response_not_decoded_twice(monkeypatch):
    monkeypatch.setattr(network, "validate_url", AsyncMock())

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=gzip.compress(b'{"result":[]}'),
                    headers={"Content-Encoding": "gzip"},
                )
            )
        ) as client:
            response = await network.fetch(client, "https://api.trace.moe/search")
            assert response.json() == {"result": []}

    run(scenario())


def test_reply_lookup_accepts_onebot_segment_dicts():
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    event = SimpleNamespace(reply=None, message=Message(MessageSegment.reply(123)))
    bot = SimpleNamespace(
        get_msg=AsyncMock(
            return_value={
                "message": [
                    {
                        "type": "image",
                        "data": {"file": "base64://" + base64.b64encode(b"quoted").decode()},
                    }
                ]
            }
        )
    )
    assert run(read_image(bot, event, "")) == (b"quoted", "")


def test_inference_requires_direct_and_same_name():
    browser = SimpleNamespace(
        web_search=AsyncMock(
            return_value=[
                Candidate("search", "https://weibo.com/u/12345678", "different person"),
                Candidate("search", "https://weibo.com/u/87654321", "阿薰kaOri 微博"),
            ]
        )
    )
    result = Result(accounts=[Account("twitter", "artist", "https://x.com/artist", "阿薰kaOri")])
    run(engines.infer_accounts(result, browser, 2))
    assert result.accounts[1].handle == "87654321"
    assert result.accounts[1].confidence == "inferred"
    assert "推断，未核实" in format_reply(result)
    browser.web_search.reset_mock()
    run(engines.infer_accounts(Result(), browser, 3))
    browser.web_search.assert_not_called()


def test_quote_image_priority():
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    event = SimpleNamespace(
        reply=SimpleNamespace(message=Message([MessageSegment.image(b"quoted")])),
        message=Message([MessageSegment.image(b"current")]),
    )
    assert run(read_image(None, event, "")) == (b"quoted", "")


def test_rich_media_failure_falls_back_to_text(monkeypatch, tmp_path):
    path = tmp_path / "found.png"
    path.write_bytes(b"image")
    config = Config(image_search_found_image=str(path))
    store = Store(tmp_path / "state.json", config)
    result = Result(used=["lens"], accounts=[Account("twitter", "artist", "https://x.com/artist")])
    store.put(store.cache_key(b"normalized"), result)
    monkeypatch.setattr(plugin, "store", store)
    monkeypatch.setattr(plugin, "config", config)
    monkeypatch.setattr(plugin, "read_image", AsyncMock(return_value=(b"raw", "")))
    monkeypatch.setattr(plugin, "prepare_image", lambda data: (b"normalized", "image/jpeg"))
    event = SimpleNamespace(user_id=765, message_id=123, get_plaintext=lambda: "#搜图")
    bot = SimpleNamespace(self_id="test", send=AsyncMock(side_effect=[None, RuntimeError(), None]))
    run(plugin.handle_search(bot, event))
    assert bot.send.await_count == 3
    assert bot.send.call_args.args[1].type == "text"
    assert "artist" in bot.send.call_args.args[1].data["text"]
    assert not plugin._busy


def test_browser_engines_with_local_responses():
    """Run real Chromium multipart/navigation/DOM paths without contacting the search sites."""
    from playwright.async_api import async_playwright

    async def scenario():
        async with async_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Playwright Chromium is not installed")
            chromium = await playwright.chromium.launch(headless=True)
            context = await chromium.new_context()
            browser = engines.Browser(Config(image_search_timeout=10))
            browser.context = context
            uploads = []

            async def serve(route):
                request = route.request
                if "/v3/upload" in request.url:
                    uploads.append(request.post_data_buffer)
                if "/pcpage/" in request.url:
                    body = """<input type="file" onchange="location.href='/details?test=1'">"""
                else:
                    body = '<a href="https://x.com/artist_123">X作者名字 (@artist_123) / X</a>'
                await route.fulfill(status=200, content_type="text/html", body=body)

            await context.route("**/*", serve)
            try:
                for engine in ("lens", "baidu", "bing", "yandex", "google"):
                    rows = await browser.search(
                        engine, b"synthetic-image", "image/jpeg", "https://example.com/input.jpg"
                    )
                    assert len(rows) == 1 and rows[0].source == "https://x.com/artist_123"
                    assert rows[0].weak == (engine == "baidu")
                assert len(uploads) == 1 and b"synthetic-image" in uploads[0]
                assert b'name="encoded_image"' in uploads[0]
            finally:
                await context.close()
                await chromium.close()

    run(scenario())
