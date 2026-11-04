from __future__ import annotations

import base64
import html
import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from .config import ENGINE_LABELS


@dataclass
class Candidate:
    engine: str
    source: str = ""
    title: str = ""
    similarity: float | None = None
    author_url: str = ""
    author: str = ""
    links: list[str] = field(default_factory=list)
    snippet: str = ""
    weak: bool = False


@dataclass
class Account:
    platform: str
    handle: str
    url: str
    name: str = ""
    confidence: str = "direct"
    similarity: float | None = None
    source: str = ""


@dataclass
class Result:
    candidates: list[Candidate] = field(default_factory=list)
    accounts: list[Account] = field(default_factory=list)
    used: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    artwork_dropped: int = 0
    name_dropped: int = 0
    consensus: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Result:
        return cls(
            **{
                **data,
                "candidates": [Candidate(**row) for row in data["candidates"]],
                "accounts": [Account(**row) for row in data["accounts"]],
            }
        )


def clean(text: str, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", text))).strip()[:limit]


def host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def unwrap_url(url: str, base: str = "") -> str:
    url = urljoin(base, html.unescape(url))
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if host_matches(parts.hostname or "", "bing.com") and query.get("u", [""])[0].startswith("a1"):
        try:
            token = query["u"][0][2:]
            url = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        except (ValueError, UnicodeError):
            return ""
    elif host_matches(parts.hostname or "", "google.com") and parts.path == "/url":
        url = (query.get("q") or query.get("url") or [url])[0]
    return url.split("#")[0] if urlsplit(url).scheme in {"https", "http"} else ""


class Links(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, str]] = []
        self.href: str | None = None
        self.text: list[str] = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.text = []

    def handle_data(self, data):
        if self.href is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            self.rows.append((self.href, clean(" ".join(self.text))))
            self.href = None


def external_links(source: str, base: str, excluded: list[str]) -> list[tuple[str, str]]:
    rows = {}
    for href, title in Links(source).rows:
        url = unwrap_url(href, base)
        host = urlsplit(url).hostname or ""
        if host and not any(host_matches(host, domain) for domain in excluded):
            rows.setdefault(url, title)
    return list(rows.items())[:60]


def parse_saucenao(source: str) -> list[Candidate]:
    out = []
    blocks = re.split(r'<div\b[^>]*class=["\']result(?:\s[^"\']*)?["\']', source)[1:]
    for block in blocks:
        similarity = re.search(r"resultsimilarityinfo[^>]*>\s*([\d.]+)%", block)
        title = re.search(r"resulttitle[^>]*>(.*?)</div>", block, re.S)
        author = re.search(
            r'(?:Member|Author).*?<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            block,
            re.S | re.I,
        )
        links = external_links(block, "https://saucenao.com/", ["saucenao.com"])
        if links:
            out.append(
                Candidate(
                    "saucenao",
                    links[0][0],
                    clean(title[1]) if title else "",
                    float(similarity[1]) / 100 if similarity else None,
                    urljoin("https://saucenao.com/", author[1]) if author else "",
                    clean(author[2], 40) if author else "",
                    [row[0] for row in links],
                    clean(block),
                )
            )
    return out


def parse_iqdb(source: str) -> list[Candidate]:
    if re.search(r"Place in queue|under high load", source, re.I):
        raise ValueError("IQDB 正在排队，请稍后重试")
    if "No relevant matches" in source:
        return []
    out = []
    # Similarity belongs to each result table, never to every link on the page.
    for block in re.findall(r"<table\b.*?</table>", source, re.S | re.I):
        sim = re.search(r"([\d.]+)%\s*similarity", block, re.I)
        if not sim:
            continue
        for url, title in external_links(block, "https://iqdb.org/", ["iqdb.org"]):
            if re.search(r"\.(?:png|jpe?g|gif|webp|svg)(?:\?|$)", url, re.I):
                continue
            out.append(Candidate("iqdb", url, title, float(sim[1]) / 100, links=[url]))
    return out[:12]


def parse_tracemoe(payload: dict) -> list[Candidate]:
    out, seen = [], set()
    for row in payload.get("result", [])[:10]:
        similarity = row.get("similarity", 0)
        anime = row.get("anilist") or {}
        if not isinstance(anime, dict) or similarity < 0.87 or not anime.get("id"):
            continue
        url = f"https://anilist.co/anime/{int(anime['id'])}"
        if url in seen:
            continue
        seen.add(url)
        titles = anime.get("title") or {}
        title = next((titles[k] for k in ("native", "romaji", "english") if titles.get(k)), "")
        episode = row.get("episode")
        label = f"{title or row.get('filename', '')}"
        if episode is not None:
            label += f" 第{episode}集"
        seconds = row.get("from")
        if isinstance(seconds, int | float):
            label += f" {int(seconds) // 60:02d}:{int(seconds) % 60:02d}"
        out.append(Candidate("tracemoe", url, clean(label), similarity, links=[url]))
        if len(out) == 3:
            break
    return out


_DENIED = {
    "twitter": {
        "i",
        "intent",
        "share",
        "home",
        "search",
        "hashtag",
        "explore",
        "login",
        "signup",
        "notifications",
        "messages",
        "settings",
        "tos",
        "privacy",
        "account",
    },
    "weibo": {
        "ttarticle",
        "p",
        "show",
        "status",
        "detail",
        "photo",
        "video",
        "article",
        "fav",
        "like",
        "comment",
        "search",
        "hot",
        "topic",
        "tv",
        "page",
        "s",
        "login",
    },
    "instagram": {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "about"},
}


def match_account(url: str) -> Account | None:
    parts = urlsplit(html.unescape(url))
    if parts.scheme not in {"http", "https"} or parts.username or parts.password:
        return None
    host = (parts.hostname or "").lower()
    path = unquote(parts.path).strip("/").split("/")
    first = path[0]
    platform, handle, canonical = "", "", ""
    if host in {
        "x.com",
        "twitter.com",
        "www.x.com",
        "www.twitter.com",
        "mobile.twitter.com",
        "m.twitter.com",
        "m.x.com",
    }:
        if re.fullmatch(r"[A-Za-z0-9_]{2,20}", first):
            platform, handle, canonical = "twitter", first, f"https://x.com/{first}"
    elif host in {"weibo.com", "www.weibo.com", "m.weibo.cn", "weibo.cn", "m.weibo.com"}:
        handle = path[1] if first in {"u", "n"} and len(path) > 1 else first
        if re.fullmatch(r"\d{6,}|[\w-]{3,40}", handle):
            platform = "weibo"
            prefix = "u/" if handle.isdigit() else "n/"
            canonical = f"https://weibo.com/{prefix}{handle}"
    elif host == "space.bilibili.com" or (host == "www.bilibili.com" and first == "space"):
        handle = path[1] if first == "space" and len(path) > 1 else first
        if re.fullmatch(r"\d{3,}", handle):
            platform, canonical = "bilibili", f"https://space.bilibili.com/{handle}"
    elif host == "www.bilibili.com" and first in {"opus", "video", "read"}:
        handle = parse_qs(parts.query).get("mid", [""])[0]
        if re.fullmatch(r"\d{3,}", handle):
            platform, canonical = "bilibili", f"https://space.bilibili.com/{handle}"
    elif host in {"instagram.com", "www.instagram.com"} and len(path) == 1:
        if re.fullmatch(r"[A-Za-z0-9_.]{2,30}", first):
            platform, handle, canonical = "instagram", first, f"https://www.instagram.com/{first}/"
    elif host in {"pixiv.net", "www.pixiv.net"}:
        match = re.search(r"^(?:en/)?users/(\d{2,})$", parts.path.strip("/"))
        handle = match[1] if match else ""
        if first == "member.php":
            handle = parse_qs(parts.query).get("id", [""])[0]
        if handle.isdigit():
            platform, canonical = "pixiv", f"https://www.pixiv.net/users/{handle}"
    if platform and handle.lower() not in _DENIED.get(platform, set()):
        return Account(platform, handle, canonical)
    return None


def normalize_name(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).lower()


def title_name(title: str) -> str:
    text = clean(title)
    badge = (
        r"(?:Twitter|Instagram|Bilibili|Weibo|Facebook|Threads|TikTok|推特|微博|哔哩哔哩|IG|Ins|X)"
    )
    for pattern in (
        rf"^(?:{badge}\s*)?(.{{2,32}}?)\s*[（(]@?[\w.\-]+[)）]",
        rf"^(?:{badge}\s*)?(.{{2,32}}?)\s*on\s+{badge}\b",
        r"(?:CN|coser|cosplayer)\s*[:：_-]\s*([\w@.]{2,24})",
        r"(?:博主|coser|cosplayer)(?![吧网站群])([\u4e00-\u9fff][\w@.]{1,23})",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            return match[1].strip()
    return ""


def dominant_name(candidates: list[Candidate]) -> tuple[str, str]:
    names = {}
    for row in candidates:
        name = row.author or title_name(row.title)
        key = normalize_name(name)
        if len(key) >= 4:
            names.setdefault(key, name)
    best, best_prefix, hits = "", "", 1
    # Distinct source pages prevent repeated links/engines manufacturing consensus.
    for key in sorted(names, key=len):
        for size in range(min(12, len(key)), 3, -1):
            prefix = key[:size]
            count = len(
                {
                    c.source
                    for c in candidates
                    if c.source and prefix in normalize_name(f"{c.title} {c.snippet}")
                }
            )
            if count > hits:
                best, best_prefix, hits = names[key], prefix, count
    return best, best_prefix


def select_accounts(result: Result, strict: bool, maximum: int) -> None:
    found: dict[tuple[str, str], Account] = {}
    for row in result.candidates:
        urls = [row.author_url, row.source, *row.links]
        urls += re.findall(r'https?://[^\s"<>）)]+', row.title + " " + row.snippet)
        for url in urls:
            account = match_account(url)
            if account is None:
                continue
            account.name = title_name(row.title) or row.author
            account.source, account.similarity = row.source, row.similarity
            account.confidence = "weak" if row.weak else "direct"
            key = account.platform, account.handle.lower()
            previous = found.get(key)
            if previous is None or (account.similarity or 0) > (previous.similarity or 0):
                found[key] = account
            elif not previous.name and account.name:
                previous.name = account.name
    accounts = list(found.values())
    # Weibo nickname and UID from the same source are aliases of one author.
    numeric_sources = {a.source for a in accounts if a.platform == "weibo" and a.handle.isdigit()}
    accounts = [
        a
        for a in accounts
        if not (a.platform == "weibo" and not a.handle.isdigit() and a.source in numeric_sources)
    ]
    result.consensus, prefix = dominant_name(result.candidates)
    artwork_only = bool(accounts) and all(a.platform == "pixiv" for a in accounts)
    kept = []
    for account in accounts:
        name = normalize_name(account.name)
        if (
            strict
            and prefix
            and not artwork_only
            and not (name and (name.startswith(prefix) or prefix.startswith(name)))
        ):
            result.name_dropped += 1
        elif account.platform == "pixiv" and (account.similarity or 0) < 0.8:
            result.artwork_dropped += 1
        else:
            kept.append(account)
    weights = {
        key: i for i, key in enumerate(["twitter", "weibo", "bilibili", "instagram", "pixiv"])
    }
    kept.sort(key=lambda a: (a.confidence != "direct", -(a.similarity or 0), weights[a.platform]))
    result.accounts = kept[:maximum]


def format_reply(result: Result, cached: bool = False) -> str:
    lines = ["🎯 搜图结果" + ("（缓存）" if cached else "")]
    labels = {
        "twitter": "Twitter",
        "weibo": "微博",
        "bilibili": "B站",
        "instagram": "Instagram",
        "pixiv": "Pixiv（画作平台）",
    }
    for account in result.accounts:
        name = account.name or (account.handle if not account.handle.isdigit() else "未知")
        handle = account.handle if account.handle.isdigit() else "@" + account.handle
        note = {"weak": "（相似图，未核实）", "inferred": "（推断，未核实）"}.get(
            account.confidence, ""
        )
        lines.extend(
            [
                "",
                f"🌐 平台：{labels[account.platform]}",
                f"👤 名字：{name}",
                f"🆔 账号：{handle} · {account.url}{note}",
            ]
        )
    anime = [c for c in result.candidates if c.engine == "tracemoe"]
    for candidate in anime:
        lines.extend(
            [
                "",
                f"🎬 动画：{candidate.title}",
                f"相似度：{candidate.similarity:.1%}",
                f"图源：{candidate.source}",
            ]
        )
    if not result.accounts and not anime:
        if not result.used:
            lines.append("❌ 这次没查成，所有引擎均请求失败。")
        elif result.candidates:
            lines.append(f"找到 {len(result.candidates)} 条相似图，但没有能确认的账号。")
        else:
            lines.append("没有找到匹配结果，可以尝试原图或减少裁剪。")
    if result.artwork_dropped:
        lines.append(f"已过滤 {result.artwork_dropped} 个相似度不足的画作账号。")
    if result.name_dropped:
        lines.append(f"已过滤 {result.name_dropped} 个与「{result.consensus}」名字不符的账号。")
    if result.accounts and all(a.platform == "pixiv" for a in result.accounts):
        lines.append("Pixiv 为画作平台，以上是画师账号。")
    if not result.accounts:
        sources = sorted(result.candidates, key=lambda c: -(c.similarity or 0))
        for candidate in sources[:3]:
            if candidate.engine != "tracemoe" and candidate.source:
                lines.extend([f"相似图源：{candidate.title}", candidate.source])
    if result.failures:
        lines.append(
            "部分引擎不可用："
            + "；".join(
                f"{ENGINE_LABELS[k]}：{clean(result.failures[k], 60)}" for k in result.failures
            )
        )
    return "\n".join(lines)[:3500]
