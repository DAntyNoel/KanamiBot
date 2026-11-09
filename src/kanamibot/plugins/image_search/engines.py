from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlencode, urlsplit

import httpx
from playwright.async_api import BrowserContext, Playwright, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from kanamibot.core.paths import DATA_DIR
from kanamibot.lens_browser import browser_options, profile_directory
from kanamibot.lens_session import PROBE_SCRIPT, USER_AGENT, CookieFile

from .config import Config
from .network import fetch, upload_allowed, validate_url
from .results import (
    Candidate,
    Result,
    external_links,
    host_matches,
    match_account,
    normalize_name,
    parse_iqdb,
    parse_saucenao,
    parse_tracemoe,
    unwrap_url,
)

logger = logging.getLogger(__name__)
UA = USER_AGENT
NETWORK_ENGINES = {"saucenao", "iqdb", "tracemoe"}
EXCLUDED = {
    "lens": ["google.com", "gstatic.com", "googleusercontent.com"],
    "google": ["google.com", "gstatic.com", "googleusercontent.com"],
    "bing": ["bing.com", "microsoft.com", "msn.com", "live.com"],
    "yandex": ["yandex.com", "yandex.ru", "yastatic.net", "yandex.net"],
    "baidu": ["baidu.com", "bdstatic.com", "bdimg.com", "baidupcs.com", "bdurl.net"],
}
LINK_SCRIPT = """() => Array.from(document.querySelectorAll('a[href]'))
    .filter(a => !a.closest('header,footer,nav,[role=navigation]'))
    .slice(0, 300).map(a => ({url: a.href,
        preview: a.querySelector('img')?.currentSrc || a.querySelector('img')?.src || '',
        title: (a.querySelector('h1,h2,h3,[role=heading]')?.textContent ||
                a.getAttribute('aria-label') || a.textContent || '').trim().slice(0,200)}))"""


class Browser:
    """One lazy Playwright context; browser operations serialize, shutdown closes only ours."""

    def __init__(self, config: Config):
        self.config = config
        self.lock = asyncio.Lock()
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.cookies = (
            CookieFile(config.image_search_cookie_file) if config.image_search_cookie_file else None
        )

    async def reload_cookies(self) -> None:
        if self.cookies is not None and self.context is not None:
            try:
                await self.cookies.reload(self.context, self.config.image_search_google_cookie)
            except Exception as exc:
                logger.warning("导入 Lens Cookie 失败：%s", type(exc).__name__)

    async def save_cookies(self) -> None:
        if self.cookies is not None and self.context is not None:
            try:
                await self.cookies.save(self.context, source="successful-query")
            except Exception as exc:
                logger.warning("保存 Lens Cookie 失败：%s", type(exc).__name__)

    async def ensure(self) -> BrowserContext:
        if self.context is None:
            self.playwright = await async_playwright().start()
            options = {
                "headless": self.config.image_search_browser_headless,
                "accept_downloads": False,
                "service_workers": "block",
            }
            if self.config.image_search_proxy:
                options["proxy"] = {"server": self.config.image_search_proxy}
            try:
                selection = browser_options(
                    self.config.image_search_browser_executable,
                    self.config.image_search_browser_channel,
                )
                options.update(selection)
                self.context = await self.playwright.chromium.launch_persistent_context(
                    str(profile_directory(DATA_DIR / "image_search" / "browser", selection)),
                    **options,
                )
                await self.context.route("**/*", self.guard)
            except BaseException:
                await self.playwright.stop()
                self.playwright = None
                raise
        return self.context

    async def guard(self, route) -> None:
        request = route.request
        try:
            # The browser may fetch result thumbnails, but uploads stay on engine domains.
            if request.method not in {"GET", "HEAD", "OPTIONS"} and not upload_allowed(request.url):
                await route.abort()
                return
            await validate_url(request.url, proxy_remote_dns=bool(self.config.image_search_proxy))
            await route.continue_()
        except (ValueError, OSError) as exc:
            logger.warning("搜图浏览器拒绝访问 %s：%s", urlsplit(request.url).hostname, exc)
            await route.abort()

    async def close(self) -> None:
        if self.context is not None:
            await self.context.close()
            self.context = None
        if self.playwright is not None:
            await self.playwright.stop()
            self.playwright = None

    async def links(self, page, engine: str) -> list[Candidate]:
        body = (await page.locator("body").inner_text())[:12000]
        if "/sorry/" in page.url or any(
            text in body.lower()
            for text in (
                "unusual traffic",
                "our systems have detected",
                "verify you are human",
            )
        ):
            raise ValueError("站点要求人机验证，请稍后重试")
        raw = await page.evaluate(LINK_SCRIPT)
        out, wrappers, seen = [], [], set()
        for row in raw:
            url = unwrap_url(row["url"])
            parts = urlsplit(url)
            host = parts.hostname or ""
            if engine in {"lens", "google"} and host_matches(host, "google.com"):
                if parts.path == "/goto" and parts.query:
                    wrappers.append(row)
                continue
            if not host or any(host_matches(host, domain) for domain in EXCLUDED[engine]):
                continue
            if engine == "baidu" and not match_account(url):
                continue
            if url not in seen:
                seen.add(url)
                out.append(
                    Candidate(
                        engine,
                        url,
                        row["title"],
                        weak=engine == "baidu",
                        preview=row.get("preview", ""),
                    )
                )
        # Google opaque /goto cards need browser redirects. Abort at the external target.
        wrappers.sort(
            key=lambda r: (
                not any(
                    word in r["title"].lower()
                    for word in (
                        "twitter",
                        "instagram",
                        "微博",
                        "bilibili",
                        "x",
                    )
                )
            )
        )
        for row in wrappers[:6]:
            target = await self.resolve_wrapper(row["url"])
            if target and target not in seen:
                seen.add(target)
                out.append(Candidate(engine, target, row["title"], preview=row.get("preview", "")))
        return out[:30]

    async def wait_results(self, page, engine: str) -> None:
        try:
            await page.wait_for_function(
                """domains => {
                    const text = document.body?.innerText || '';
                    const empty = /no matches|no results|未找到|没有找到|unusual traffic/i;
                    if (empty.test(text)) return true;
                    return [...document.querySelectorAll('a[href]')].some(a => {
                        if (a.closest('header,footer,nav,[role=navigation]')) return false;
                        try {
                            const u = new URL(a.href);
                            return u.pathname === '/goto' || (u.protocol.startsWith('http') &&
                                !domains.some(d => u.hostname === d || u.hostname.endsWith('.'+d)));
                        } catch { return false; }
                    });
                }""",
                arg=EXCLUDED[engine],
                timeout=10000,
            )
        except PlaywrightTimeoutError:
            # Still inspect the current DOM: empty results are valid, not network errors.
            pass

    async def resolve_wrapper(self, url: str) -> str:
        context = await self.ensure()
        page = await context.new_page()
        found = ""

        async def intercept(route):
            nonlocal found
            request = route.request
            if request.is_navigation_request() and not host_matches(
                urlsplit(request.url).hostname or "", "google.com"
            ):
                try:
                    await validate_url(
                        request.url,
                        proxy_remote_dns=bool(self.config.image_search_proxy),
                    )
                    found = request.url
                except (ValueError, OSError):
                    pass
                await route.abort()
            else:
                await self.guard(route)

        try:
            await page.route("**/*", intercept)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=8000)
            except Exception:
                pass
            return found
        finally:
            await page.close()

    async def search(
        self,
        engine: str,
        image: bytes,
        mime: str,
        public_url: str,
    ) -> list[Candidate]:
        async with self.lock:
            async with asyncio.timeout(self.config.image_search_timeout):
                context = await self.ensure()
                await self.reload_cookies()
                page = await context.new_page()
                page.set_default_timeout(self.config.image_search_timeout * 1000)
                try:
                    if engine == "lens":
                        await page.goto("https://www.google.com/", wait_until="domcontentloaded")
                        await page.evaluate("""() => {
                            const f=document.createElement('form'); f.id='imageSearchUpload';
                            f.method='POST'; f.enctype='multipart/form-data';
                            f.action='https://lens.google.com/v3/upload?hl=zh-CN';
                            const i=document.createElement('input'); i.type='file';
                            i.name='encoded_image'; f.appendChild(i); document.body.appendChild(f);
                        }""")
                        await page.locator("#imageSearchUpload input").set_input_files(
                            {
                                "name": "image.jpg",
                                "mimeType": mime,
                                "buffer": image,
                            }
                        )
                        async with page.expect_navigation(wait_until="domcontentloaded"):
                            await page.locator("#imageSearchUpload").evaluate("f => f.submit()")
                    elif engine == "baidu":
                        await page.goto(
                            "https://graph.baidu.com/pcpage/index?tpl_from=pc",
                            wait_until="domcontentloaded",
                        )
                        await page.locator("input[type=file]").first.set_input_files(
                            {
                                "name": "image.jpg",
                                "mimeType": mime,
                                "buffer": image,
                            }
                        )
                        await page.wait_for_url("**/details**")
                    else:
                        if not public_url:
                            raise ValueError("需要公网图片 URL；可使用 #搜图 URL 或启用临时图床")
                        urls = {
                            "bing": "https://www.bing.com/images/search?"
                            + urlencode(
                                {
                                    "q": "imgurl:" + public_url,
                                    "view": "detailv2",
                                    "iss": "sbi",
                                }
                            ),
                            "yandex": "https://yandex.com/images/search?"
                            + urlencode(
                                {
                                    "rpt": "imageview",
                                    "url": public_url,
                                }
                            ),
                            "google": "https://lens.google.com/uploadbyurl?"
                            + urlencode(
                                {
                                    "url": public_url,
                                }
                            ),
                        }
                        await page.goto(urls[engine], wait_until="domcontentloaded")
                    await page.wait_for_timeout(2500)
                    await self.wait_results(page, engine)
                    result_url = page.url
                    rows = await self.links(page, engine)
                    if engine in {"lens", "google"} and not any(
                        match_account(r.source) for r in rows
                    ):
                        if urlsplit(result_url).path == "/search":
                            await page.goto(result_url + "&udm=48", wait_until="domcontentloaded")
                            await page.wait_for_timeout(2200)
                            await self.wait_results(page, engine)
                            rows = await self.links(page, engine) + rows
                    if engine in {"lens", "google"}:
                        await self.save_cookies()
                    return rows
                finally:
                    await page.close()

    async def web_search(self, query: str, google: bool = False) -> list[Candidate]:
        async with self.lock:
            async with asyncio.timeout(min(25, self.config.image_search_timeout)):
                context = await self.ensure()
                await self.reload_cookies()
                page = await context.new_page()
                try:
                    domain = "www.google.com" if google else "www.bing.com"
                    await page.goto(
                        f"https://{domain}/search?" + urlencode({"q": query}),
                        wait_until="domcontentloaded",
                    )
                    await page.wait_for_timeout(1200)
                    if google and not (await page.evaluate(PROBE_SCRIPT))["blocked"]:
                        await self.save_cookies()
                    excluded = EXCLUDED["google" if google else "bing"]
                    return [
                        Candidate("search", url, title)
                        for url, title in external_links(await page.content(), page.url, excluded)
                    ]
                finally:
                    await page.close()


async def network_search(client, engine: str, image: bytes, mime: str) -> list[Candidate]:
    file = ("image.jpg", image, mime)
    if engine == "tracemoe":
        response = await fetch(
            client, "https://api.trace.moe/search?anilistInfo", method="POST", files={"image": file}
        )
        payload = response.json()
        if payload.get("error"):
            raise ValueError("trace.moe 返回错误")
        return parse_tracemoe(payload)
    if engine == "iqdb":
        response = await fetch(
            client,
            "https://iqdb.org/",
            method="POST",
            files={"file": file},
            data={"service[]": [str(i) for i in range(1, 7)]},
        )
        return parse_iqdb(response.text)
    response = await fetch(
        client,
        "https://saucenao.com/search.php",
        method="POST",
        files={"file": file},
        data={"db": "999", "output_type": "0"},
    )
    if "Daily Search Limit" in response.text or "Too Many Requests" in response.text:
        raise ValueError("SauceNAO 免费额度已用完，请稍后重试")
    return parse_saucenao(response.text)


async def reverse_search(
    image: bytes,
    mime: str,
    config: Config,
    browser: Browser,
    public_url: str = "",
) -> Result:
    result = Result()
    async with httpx.AsyncClient(
        timeout=config.image_search_timeout, headers={"User-Agent": UA}
    ) as client:
        if (
            config.image_search_public_upload
            and not public_url
            and any(e in {"bing", "yandex", "google"} for e in config.image_search_engines)
        ):
            try:
                response = await fetch(
                    client,
                    "https://litterbox.catbox.moe/resources/internals/api.php",
                    method="POST",
                    files={"fileToUpload": ("image.jpg", image, mime)},
                    data={"reqtype": "fileupload", "time": "1h"},
                )
                public_url = response.text.strip()
                if not host_matches(urlsplit(public_url).hostname or "", "catbox.moe"):
                    raise ValueError("图床返回地址无效")
                await validate_url(public_url)
            except Exception:
                public_url = ""
                logger.warning("搜图临时图床上传失败")

        async def run(engine):
            try:
                if engine in NETWORK_ENGINES:
                    async with asyncio.timeout(config.image_search_timeout):
                        rows = await network_search(client, engine, image, mime)
                else:
                    rows = await browser.search(engine, image, mime, public_url)
                result.used.append(engine)
                result.candidates.extend(rows)
            except Exception as exc:
                logger.warning("搜图引擎 %s 失败：%s", engine, type(exc).__name__)
                if isinstance(exc, httpx.HTTPStatusError):
                    reason = f"HTTP {exc.response.status_code}"
                elif isinstance(exc, ValueError):
                    reason = str(exc)
                elif isinstance(exc, TimeoutError | httpx.TimeoutException):
                    reason = "请求超时"
                else:
                    reason = "网络或浏览器不可用，请检查配置与日志"
                result.failures[engine] = reason

        engines = config.image_search_engines
        await asyncio.gather(*(run(engine) for engine in engines if engine in NETWORK_ENGINES))
        for engine in engines:
            if engine not in NETWORK_ENGINES:
                await run(engine)
    # Stable ordering independent of network completion time.
    result.used.sort(key=engines.index)
    result.candidates.sort(key=lambda row: engines.index(row.engine))
    return result


async def infer_accounts(result: Result, browser: Browser, maximum: int) -> None:
    direct = [a for a in result.accounts if a.confidence == "direct"]
    if not direct or len(result.accounts) >= maximum:
        return
    names = list(dict.fromkeys(a.name for a in direct if a.name))[:3]
    seen = {(a.platform, a.handle.lower()) for a in result.accounts}
    added = 0
    for name in names:
        for platform, hint in (("twitter", "Twitter"), ("weibo", "微博"), ("bilibili", "B站")):
            if any(a.platform == platform for a in direct):
                continue
            try:
                rows = await browser.web_search(f"{name} {hint} 账号")
                if not any(match_account(r.source) for r in rows):
                    rows = await browser.web_search(f"{name} {hint} 账号", google=True)
            except Exception:
                continue
            for row in rows:
                account = match_account(row.source)
                if (
                    not account
                    or account.platform != platform
                    or normalize_name(name) not in normalize_name(row.title)
                ):
                    continue
                key = account.platform, account.handle.lower()
                if key in seen:
                    continue
                seen.add(key)
                account.name, account.confidence = name, "inferred"
                account.source = row.source
                result.accounts.append(account)
                added += 1
                if added == 2 or len(result.accounts) >= maximum:
                    return
