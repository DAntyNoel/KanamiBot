"""Standalone Google Lens cookie exchange and interactive authorization; no NoneBot imports."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dotenv import dotenv_values
from PIL import Image, ImageDraw
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from kanamibot.lens_browser import browser_options, import_chromium, profile_directory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COOKIE_FILE = PROJECT_ROOT / "data/image_search/google_cookies.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
)
logger = logging.getLogger(__name__)


def google_domain(domain: str) -> bool:
    domain = domain.lstrip(".").lower()
    return domain == "google.com" or domain.endswith(".google.com")


def normalize_cookies(value) -> list[dict]:
    """Accept a Playwright cookie list, storage_state, or our exported envelope."""
    rows = value.get("cookies") if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise ValueError("Cookie JSON 必须为 Cookie 数组或包含 cookies 数组的对象")
    cookies = []
    for row in rows:
        if not isinstance(row, dict) or not google_domain(str(row.get("domain", ""))):
            continue
        name, content = row.get("name"), row.get("value")
        if (
            not isinstance(name, str)
            or not isinstance(content, str)
            or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
            or any(c in content for c in "\r\n\0")
        ):
            raise ValueError("Google Cookie 格式无效")
        cookie = {
            key: row[key]
            for key in (
                "name",
                "value",
                "domain",
                "path",
                "expires",
                "httpOnly",
                "secure",
                "sameSite",
            )
            if key in row
        }
        cookie.setdefault("path", "/")
        if cookie.get("sameSite") not in {None, "Strict", "Lax", "None"}:
            cookie.pop("sameSite")
        if name.startswith("__Host-") and (
            row["domain"].startswith(".") or cookie["path"] != "/" or not cookie.get("secure")
        ):
            raise ValueError("__Host- Cookie 必须保留原主机、根路径及 Secure 属性")
        expires = cookie.get("expires", -1)
        if expires != -1 and expires <= time.time():
            continue
        cookies.append(cookie)
    return cookies


def header_cookies(header: str) -> list[dict]:
    cookies = []
    for pair in header.split(";"):
        name, separator, value = pair.strip().partition("=")
        if not separator:
            continue
        cookies.append(
            {
                "name": name,
                "value": value,
                "path": "/",
                "secure": True,
                "domain": "www.google.com" if name.startswith("__Host-") else ".google.com",
            }
        )
    return normalize_cookies(cookies)


class CookieFile:
    """Exchange cookies with a separate login profile, without overwriting a newer login."""

    def __init__(self, path: str | Path = DEFAULT_COOKIE_FILE):
        self.path = Path(path)
        if not self.path.is_absolute():
            self.path = PROJECT_ROOT / self.path
        self.signature: str | None = None

    def read(self) -> tuple[list[dict], str | None]:
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return [], None
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("Cookie 文件不能超过 2 MB")
        signature = hashlib.sha256(data).hexdigest()
        return normalize_cookies(json.loads(data)), signature

    async def reload(self, context, seed_header: str = "") -> bool:
        cookies, signature = self.read()
        if signature == self.signature and not (signature is None and seed_header):
            return False
        if signature is None and seed_header:
            cookies = header_cookies(seed_header)
        # Replace Google cookies as a set, so removed cookies from a newer login do not linger.
        for domain in {
            row["domain"] for row in await context.cookies() if google_domain(row["domain"])
        }:
            await context.clear_cookies(domain=domain)
        if cookies:
            await context.add_cookies(cookies)
        self.signature = signature
        return True

    async def save(self, context, *, source: str, force: bool = False) -> bool:
        cookies = normalize_cookies(await context.cookies())
        if not cookies:
            return False
        _, current = self.read()
        if not force and current != self.signature:
            return False
        payload = {
            "version": 1,
            "saved_at": datetime.now(UTC).isoformat(),
            "source": source,
            "cookies": cookies,
        }
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            # Check again after awaiting the browser and preparing the replacement.
            if not force and self.read()[1] != current:
                return False
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.signature = hashlib.sha256(data).hexdigest()
        return True


PROBE_SCRIPT = """() => {
    const text = document.body?.innerText || '';
    const challenge = new RegExp(['unusual traffic', 'our systems have detected',
        'verify you are human', '验证您是真人', '人机验证', '异常流量'].join('|'), 'i');
    const blocked = challenge.test(text) ||
        !!document.querySelector('form[action*="sorry"], .g-recaptcha, #captcha') ||
        location.pathname.startsWith('/sorry');
    const empty = new RegExp(['No matches', 'No matching images', 'No results found',
        '找不到相符', '未找到.*结果', '没有找到.*结果'].join('|'), 'i');
    const links = [...document.querySelectorAll('a[href]')].some(a => {
        if (a.closest('header,footer,nav,[role=navigation]')) return false;
        try {
            const u = new URL(a.href);
            const own = /(^|\\.)(google\\.com|gstatic\\.com|googleusercontent\\.com)$/i;
            const external = u.protocol.startsWith('http') && !own.test(u.hostname);
            return (u.pathname === '/goto' || external)
                && !!a.textContent.trim();
        } catch { return false; }
    });
    return {blocked, results: links || empty.test(text)};
}"""


def is_lens_result(url: str, snapshot: dict) -> bool:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    lens = bool({"vsrid", "vsint", "gsessionid", "lns_surface", "lns_mode"} & query.keys())
    lens = lens or query.get("udm", [""])[0] in {"26", "48"}
    return bool(
        parts.scheme == "https"
        and google_domain(parts.hostname or "")
        and parts.path == "/search"
        and lens
        and not snapshot.get("blocked")
        and snapshot.get("results")
    )


def test_image() -> bytes:
    output = BytesIO()
    image = Image.new("RGB", (384, 256), "white")
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((30, 30, 170, 200), fill="#66aaff")
    drawing.ellipse((200, 70, 340, 210), fill="#ffbb66")
    drawing.text((30, 220), "KanamiBot Lens session test", fill="black")
    image.save(output, "JPEG")
    return output.getvalue()


async def submit_probe(page, image: bytes) -> None:
    await page.evaluate("""() => {
        document.getElementById('kanamiLensAuth')?.remove();
        const f = document.createElement('form'); f.id='kanamiLensAuth';
        f.method='POST'; f.enctype='multipart/form-data';
        f.action='https://lens.google.com/v3/upload?hl=zh-CN';
        const i=document.createElement('input'); i.type='file'; i.name='encoded_image';
        f.appendChild(i);
        const marker=document.createElement('input'); marker.type='hidden';
        marker.name='image_content'; marker.value=''; f.appendChild(marker);
        document.body.appendChild(f);
    }""")
    await page.locator("#kanamiLensAuth input[type=file]").set_input_files(
        {
            "name": "lens-session-test.jpg",
            "mimeType": "image/jpeg",
            "buffer": image,
        }
    )
    async with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
        await page.locator("#kanamiLensAuth").evaluate("f => f.submit()")


async def authorize(
    context,
    cookies: CookieFile,
    *,
    timeout: float = 600,
    image: bytes | None = None,
    poll_interval: float = 1,
) -> bool:
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto("https://lens.google.com/", wait_until="domcontentloaded", timeout=45000)
    deadline = time.monotonic() + timeout
    submitted, stable, notified = False, 0, False
    while time.monotonic() < deadline and not page.is_closed():
        try:
            snapshot = await page.evaluate(PROBE_SCRIPT)
            parts = urlsplit(page.url)
            if snapshot["blocked"] and not notified:
                print("请在浏览器中完成人机验证；通过并进入 Lens 结果页后会自动保存。", flush=True)
                notified = True
            if submitted and is_lens_result(page.url, snapshot):
                stable += 1
                if stable >= 2:
                    if not await cookies.save(context, source="manual-verification", force=True):
                        raise ValueError("结果页已打开，但未取得可保存的 Google Cookie")
                    print(f"验证成功，Cookie 已自动保存：{cookies.path}", flush=True)
                    return True
            else:
                stable = 0
            if (
                not submitted
                and not snapshot["blocked"]
                and google_domain(parts.hostname or "")
                and parts.hostname not in {"accounts.google.com", "consent.google.com"}
            ):
                print("正在上传内置测试图，触发 Lens 搜索……", flush=True)
                submitted = True
                await submit_probe(page, image if image is not None else test_image())
        except PlaywrightError:
            # Navigation during manual verification destroys execution contexts temporarily.
            if page.is_closed():
                break
        await asyncio.sleep(poll_interval)
    print("验证未完成或窗口已关闭，未覆盖已保存的 Cookie。", flush=True)
    return False


async def seed_login_cookies(context, cookies: CookieFile, seed_header: str = "") -> None:
    # A partial manual login may be newer than the last fully verified export.
    # Preserve it when reopening the login profile instead of restoring an old file.
    if not normalize_cookies(await context.cookies()):
        await cookies.reload(context, seed_header)


async def manual_google_login(
    executable: str, profile: Path, proxy: str, *, timeout: float
) -> bool:
    """Let the user sign in with a normal browser, before attaching any automation."""
    arguments = [
        executable,
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if proxy:
        arguments.append(f"--proxy-server={proxy}")
    arguments.append(
        "https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Flens.google.com%2F"
    )
    process = await asyncio.create_subprocess_exec(
        *arguments, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    print(
        "普通 Chromium 登录窗口已打开，登录期间未连接自动化。\n"
        "请在窗口中登录 Google；完成后关闭这个登录浏览器的所有窗口，工具才会继续验证 Lens。",
        flush=True,
    )
    try:
        return await asyncio.wait_for(process.wait(), timeout=timeout) == 0
    except TimeoutError:
        print("等待登录窗口关闭超时，未覆盖 Cookie。可关闭窗口后重新运行认证工具。", flush=True)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打开 Lens 浏览器，人工验证后自动保存 Cookie")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--cookie-file", type=Path)
    parser.add_argument("--proxy", help="覆盖 IMAGE_SEARCH_PROXY；传空字符串可直连")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--image", type=Path, help="可选测试图片；默认使用程序生成的几何图形")
    parser.add_argument(
        "--login", action="store_true", help="先在普通浏览器中手动登录，关闭窗口后再验证 Lens"
    )
    parser.add_argument(
        "--import-chromium", type=Path, metavar="EXE", help="复制现有 Chromium 程序到本项目托管目录"
    )
    parser.add_argument(
        "--browser-status", action="store_true", help="查看选用的浏览器，不打开窗口"
    )
    parser.add_argument(
        "--query-profile", action="store_true", help="直接验证查询资料；需先关闭该查询浏览器"
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")
    settings = {**dotenv_values(args.env_file), **os.environ}
    path = args.cookie_file or settings.get("IMAGE_SEARCH_COOKIE_FILE") or DEFAULT_COOKIE_FILE
    cookies = CookieFile(path)

    async def run() -> bool:
        if args.import_chromium:
            installed = await asyncio.to_thread(import_chromium, args.import_chromium)
            print(f"Chromium 已纳入本项目管理：{installed}", flush=True)
        selection = browser_options(
            settings.get("IMAGE_SEARCH_BROWSER_EXECUTABLE", ""),
            settings.get("IMAGE_SEARCH_BROWSER_CHANNEL", ""),
        )
        print(
            "搜图浏览器："
            + selection.get("executable_path", selection.get("channel", "Playwright Chromium")),
            flush=True,
        )
        if args.browser_status:
            return True
        options = {"headless": False, "service_workers": "block", **selection}
        proxy = args.proxy if args.proxy is not None else settings.get("IMAGE_SEARCH_PROXY", "")
        if proxy:
            options["proxy"] = {"server": proxy}
        async with async_playwright() as playwright:
            profile_name = "browser" if args.query_profile else "browser-login"
            profile = profile_directory(
                PROJECT_ROOT / "data/image_search" / profile_name, selection
            )
            if args.query_profile:
                print("正在验证查询资料；该查询浏览器需保持关闭，避免争用。", flush=True)
            if args.login:
                if "channel" in selection:
                    raise ValueError("普通登录模式请指定浏览器 executable，或使用托管 Chromium。")
                executable = selection.get("executable_path", playwright.chromium.executable_path)
                if not await manual_google_login(executable, profile, proxy, timeout=args.timeout):
                    return False
            context = await playwright.chromium.launch_persistent_context(
                str(profile),
                **options,
            )
            try:
                await seed_login_cookies(
                    context, cookies, settings.get("IMAGE_SEARCH_GOOGLE_COOKIE", "")
                )
                print(
                    "认证窗口已打开；保持此终端运行，完成验证后将自动保存并关闭浏览器。", flush=True
                )
                return await authorize(
                    context,
                    cookies,
                    timeout=args.timeout,
                    image=args.image.read_bytes() if args.image else None,
                )
            finally:
                await context.close()

    try:
        return 0 if asyncio.run(run()) else 1
    except KeyboardInterrupt:
        print("已取消认证。")
        return 1
    except Exception as exc:
        # Exception strings may contain cookies/URLs; report category only.
        print(
            f"Lens 认证工具运行失败（{type(exc).__name__}）。请检查代理、浏览器和 Cookie 文件格式。"
        )
        return 1
