"""PRTS source excerpts for the current Stronghold Protocol boss roster.

Game text and values are taken from PRTS without rewriting or recalculation.
The ordinary enemy tables remain labelled as general data, separate from the
current activity's tables. PRTS content: CC BY-SA 3.0.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import re
import time
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, Tag

from .client import BackendError
from .wiki import PlainText, Wiki

ACTIVITY = "卫戍协议·规程/PRTS盟约记录"
# Keep existing numbers stable even if the source table is later rearranged.
BOSS_ORDER = (
    "假想敌：胄",
    "假想敌：铳",
    "假想敌：管",
    "盐风主教昆图斯",
    "阿利斯泰尔，帝国余晖",
    "“萨米的意志”",
    "假想敌：械",
    "假想敌：茧",
    "假想敌：鳍",
    "矩兽",
)


def source_url(title: str, revision: int = 0) -> str:
    if revision:
        return f"https://prts.wiki/w/{quote(title)}?oldid={revision}"
    return f"https://prts.wiki/w/{quote(title)}"


def rows(table: Tag) -> list[Tag]:
    body = table.find("tbody", recursive=False) or table
    return body.find_all("tr", recursive=False)


def cells(row: Tag) -> list[Tag]:
    return row.find_all(["td", "th"], recursive=False)


def hidden_values(cell: Tag) -> list[Tag]:
    return cell.select('span[style*="#ff9a9a"], span[style*="#FF9A9A"]')


def leader_tables(soup: BeautifulSoup) -> list[Tag]:
    return [
        table
        for table in soup.find_all("table")
        if (rs := rows(table)) and rs[0].get_text(strip=True).startswith("领袖属性")
    ]


@dataclass(frozen=True)
class Boss:
    name: str
    icon: str
    hidden: bool


@dataclass(frozen=True)
class BossCard:
    title: str
    html: str
    text: str


def roster(html: str) -> list[Boss]:
    tables = leader_tables(BeautifulSoup(html, "html.parser"))
    if not tables:
        raise BackendError("PRTS 本期领袖列表暂时无法读取。")
    result = []
    for row in rows(tables[0]):
        cs = cells(row)
        if len(cs) != 5 or cs[0].name != "td":
            continue
        link = cs[0].find("a", title=True)
        icon = cs[0].find("img", src=True)
        if not link or not icon or not all(re.search(r"\d", c.get_text()) for c in cs[1:]):
            continue
        result.append(
            Boss(str(link["title"]), str(icon["src"]), any(hidden_values(c) for c in cs[1:]))
        )
    if not result or len({b.name for b in result}) != len(result):
        raise BackendError("PRTS 本期领袖列表格式发生变化，请稍后重试。")
    order = {name: index for index, name in enumerate(BOSS_ORDER)}
    return sorted(result, key=lambda b: order.get(b.name, len(order)))


def activity_excerpt(html: str, boss: Boss) -> str:
    """Show both source variants in two adjacent groups of three columns."""
    parts = []
    for table in leader_tables(BeautifulSoup(html, "html.parser")):
        rs = rows(table)
        if len(rs) < 3:
            continue
        entries = []
        if len(cells(rs[1])) == 5:
            matching = [r for r in rs[3:] if r.find("a", title=boss.name) is not None]
            if len(matching) != 1:
                raise BackendError("PRTS 本期领袖数值暂时无法读取。")
            entries = [
                (heading.get_text(strip=True), *_variant_values(value))
                for heading, value in zip(cells(rs[1])[1:], cells(matching[0])[1:], strict=True)
            ]
        else:
            headers = cells(rs[1])
            column = next((i for i, c in enumerate(headers) if c.find("a", title=boss.name)), None)
            if column is None:
                raise BackendError("PRTS 规程等级数值暂时无法读取。")
            for row in rs[3:]:
                cs = cells(row)
                if len(cs) == 1:
                    continue
                if len(cs) != len(headers):
                    raise BackendError("PRTS 规程等级数值格式发生变化，请稍后重试。")
                entries.append((cs[0].get_text(strip=True), *_variant_values(cs[column])))
        if not entries:
            raise BackendError("PRTS 本期领袖数值暂时无法读取。")
        parts.append(_attribute_table(cells(rs[0])[0], cells(rs[2])[0], entries))
    if not parts:
        raise BackendError("PRTS 本期领袖数值暂时无法读取。")
    return "".join(parts)


def _variant_values(cell: Tag) -> tuple[str, str]:
    normal = copy.deepcopy(cell)
    for value in hidden_values(normal):
        value.decompose()
    return normal.get_text(strip=True), "".join(str(value) for value in hidden_values(cell))


def _attribute_table(title: Tag, notice: Tag, entries: list[tuple[str, str, str]]) -> str:
    soup = BeautifulSoup("", "html.parser")
    table = soup.new_tag("table", attrs={"class": "boss-attributes"})
    soup.append(table)

    def full_row(source: Tag) -> None:
        row = soup.new_tag("tr")
        cell = copy.deepcopy(source)
        cell["colspan"] = "6"
        row.append(cell)
        table.append(row)

    full_row(title)
    headers = soup.new_tag("tr")
    for label in ("模式", "普通", "隐秘核心") * 2:
        cell = soup.new_tag("th")
        cell.string = label
        headers.append(cell)
    table.append(headers)
    full_row(notice)
    # Read down the left half, then the right; copy every source value once.
    halfway = (len(entries) + 1) // 2
    for index in range(halfway):
        row = soup.new_tag("tr")
        for position in (index, index + halfway):
            values = entries[position] if position < len(entries) else ("", "", "")
            for column, value in enumerate(values):
                cell = soup.new_tag("td")
                if column == 2 and value:
                    cell.extend(BeautifulSoup(value, "html.parser").contents)
                else:
                    cell.string = value or ("—" if values[0] else "")
                row.append(cell)
        table.append(row)
    return str(table)


def enemy_excerpt(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    overview = soup.select_one("table.enemy-info-common")
    if overview is None:
        raise BackendError("PRTS 敌人条目格式发生变化，暂时无法读取。")
    # PRTS publishes a corrected default and a collapsed game-text version.
    # Use the visible default, without duplicating the alternative or tooltips.
    for alternative in overview.select(".mw-collapsed"):
        alternative.decompose()
    detail = []
    for block in soup.select(".prts-enemy-level-table"):
        heading = block.find_previous("h2")
        if heading is None:
            continue
        detail.extend([str(heading), str(block)])
    # Preserve PRTS's warning about general enemy data when including those tables.
    notice = soup.find(
        lambda tag: (
            tag.name == "p" and tag.get_text().startswith("本页面的敌人数据只能代表通常数据")
        )
    )
    return str(overview), (str(notice) if notice and detail else "") + "".join(detail)


ALLOWED = {
    "table",
    "tbody",
    "thead",
    "tr",
    "th",
    "td",
    "div",
    "span",
    "p",
    "br",
    "b",
    "strong",
    "i",
    "em",
    "small",
    "s",
    "sup",
    "sub",
    "ul",
    "ol",
    "li",
    "a",
    "img",
    "h2",
    "h3",
    "h4",
    "hr",
}


def clean_excerpt(html: str) -> BeautifulSoup:
    """Remove site controls and hidden annotations, retaining visible source text."""
    soup = BeautifulSoup(html, "html.parser")
    for node in list(soup.find_all(string=lambda s: isinstance(s, Comment))):
        node.extract()
    for node in list(
        soup.select(
            "script, style, iframe, object, embed, input, button, link, meta, "
            ".mw-editsection, .mw-customtoggle-description, .prts-enemy-level-about"
        )
    ):
        node.decompose()
    for node in list(soup.find_all(True)):
        if node.name is None or node.attrs is None:
            continue
        if re.search(r"display\s*:\s*none", node.get("style", ""), re.I):
            node.decompose()
            continue
        if node.name not in ALLOWED:
            node.unwrap()
            continue
        attrs = node.attrs
        node.attrs = {k: v for k, v in attrs.items() if k in {"rowspan", "colspan", "class"}}
        colors = re.findall(
            r"(?:^|;)\s*(color|background-color)\s*:\s*([#a-zA-Z0-9(),.% ]+)",
            attrs.get("style", ""),
        )
        if colors:
            node["style"] = ";".join(f"{key}:{value}" for key, value in colors)
        if node.name == "a":
            href = urljoin("https://prts.wiki", attrs.get("href", ""))
            if urlsplit(href).scheme == "https" and urlsplit(href).hostname == "prts.wiki":
                node["href"] = href
        if node.name == "img":
            node["src"] = attrs.get("src", "")
            node["alt"] = attrs.get("alt", "")
    return soup


class BossQueries:
    def __init__(self, wiki: Wiki) -> None:
        self.wiki = wiki
        self.cache: dict[str, tuple[float, dict]] = {}
        self.images: dict[str, tuple[float, str]] = {}
        self.downloads = asyncio.Semaphore(4)

    async def page(self, title: str) -> dict:
        cached = self.cache.get(title)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        value = await self.wiki.request(
            action="parse", page=title, prop="text|revid", redirects=1, disableeditsection=1
        )
        page = value.get("parse", {})
        if not page.get("text", {}).get("*"):
            raise BackendError("PRTS Wiki 暂时没有可用的页面内容。")
        if len(self.cache) >= 32:
            self.cache.pop(next(iter(self.cache)))
        self.cache[title] = (time.monotonic() + 600, page)
        return page

    async def image(self, url: str) -> str:
        # Use the original portrait instead of enlarging the table's 60px thumbnail.
        if url.startswith("https://media.prts.wiki/thumb/"):
            url = url.replace("/thumb/", "/", 1).rsplit("/", 1)[0]
        cached = self.images.get(url)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        async with self.downloads:
            data, mime = await self.wiki.image(url)
        value = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        if len(self.images) >= 128:
            self.images.pop(next(iter(self.images)))
        self.images[url] = (time.monotonic() + 3600, value)
        return value

    async def inline(self, soup: BeautifulSoup) -> str:
        urls = {str(img["src"]) for img in soup.select("img[src]")}

        async def fetch(url):
            try:
                return url, await self.image(url)
            except BackendError:
                return url, ""

        images = dict(await asyncio.gather(*(fetch(url) for url in urls)))
        for img in soup.select("img[src]"):
            if data := images[str(img["src"])]:
                img["src"] = data
            else:
                img.replace_with(img.get("alt", ""))
        return str(soup)

    async def query(self, index: int = 0) -> BossCard:
        from .render import render_html

        activity = await self.page(ACTIVITY)
        activity_html = activity["text"]["*"]
        bosses = roster(activity_html)
        activity_url = source_url(ACTIVITY, activity.get("revid", 0))
        if index == 0:
            icons = await asyncio.gather(*(self.image(boss.icon) for boss in bosses))
            entries = [
                {"name": boss.name, "icon": icon, "index": i}
                for i, (boss, icon) in enumerate(zip(bosses, icons, strict=True), 1)
            ]
            text = "卫戍协议 · Boss\n" + "\n".join(
                f"{i}. {boss.name}" for i, boss in enumerate(bosses, 1)
            )
            text += f"\n#卫 boss 序号\n来源：{activity_url}"
            return BossCard(
                "卫戍协议 · Boss",
                render_html(title="卫戍协议 · Boss", entries=entries),
                text,
            )
        if not 1 <= index <= len(bosses):
            raise ValueError(f"Boss 序号应为 1～{len(bosses)}，请发送 #卫 boss 查看列表。")
        boss = bosses[index - 1]
        enemy = await self.page(boss.name)
        overview, details = enemy_excerpt(enemy["text"]["*"])
        values = activity_excerpt(activity_html, boss)
        blocks = [
            ("能力", overview, source_url(boss.name, enemy.get("revid", 0))),
            ("卫戍协议·规程", values, activity_url),
        ]
        if details:
            blocks.append(("敌人条目 · 通常数据", details, blocks[0][2]))
        sections = []
        for title, content, url in blocks:
            cleaned = clean_excerpt(content)
            plain = PlainText()
            plain.feed(str(cleaned))
            sections.append(
                {
                    "title": title,
                    "html": await self.inline(cleaned),
                    "text": plain.text(),
                    "url": url,
                }
            )
        title = f"{index}. {boss.name}"
        text = (
            title
            + "\n\n"
            + "\n\n".join(f"{s['title']}\n{s['text']}\n来源：{s['url']}" for s in sections)
            + "\nPRTS 内容许可：CC BY-SA 3.0"
        )
        return BossCard(title, render_html(title=title, sections=sections), text)
