"""Bounded, locally rendered help pages; no browser or network required."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from kanamibot.core.paths import DEFAULT_FONT_PATH

WIDTH = 1080
MAX_HEIGHT = 1600
LINE_HEIGHT = 44


def wrap_text(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines = []
    for paragraph in text.expandtabs(4).splitlines() or [""]:
        line = ""
        for char in paragraph:
            if line and font.getlength(line + char) > width:
                lines.append(line)
                line = ""
            line += char
        lines.append(line)
    return lines


def render_help_pages(title: str, sections: list[tuple[str, str]]) -> tuple[bytes, ...]:
    font = ImageFont.truetype(str(DEFAULT_FONT_PATH), 30)
    heading = ImageFont.truetype(str(DEFAULT_FONT_PATH), 38)
    small = ImageFont.truetype(str(DEFAULT_FONT_PATH), 24)
    pages: list[list[tuple[str, list[str]]]] = [[]]
    used = 200
    for label, body in sections:
        lines = wrap_text(body, font, WIDTH - 144)
        offset = 0
        while offset < len(lines):
            capacity = (MAX_HEIGHT - 70 - used - 94) // LINE_HEIGHT
            # Keep short cards together, while long details fill the available page.
            if capacity < 2 or (len(lines) <= 8 and len(lines) > capacity):
                pages.append([])
                used = 200
                continue
            chunk = lines[offset:offset + capacity]
            chunk_label = label + ("（续）" if offset else "")
            while font.getlength(chunk_label) > WIDTH - 128:
                chunk_label = chunk_label[:-2] + "…"
            pages[-1].append((chunk_label, chunk))
            used += 94 + LINE_HEIGHT * len(chunk)
            offset += len(chunk)

    images = []
    for index, page in enumerate(pages, 1):
        height = 270 + sum(94 + LINE_HEIGHT * len(lines) for _, lines in page)
        canvas = Image.new("RGB", (WIDTH, height), "#eef3f7")
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, WIDTH, 10), fill="#397b85")
        draw.text((48, 34), "KANAMIBOT / 使用指南", font=small, fill="#397b85")
        # User queries can be arbitrarily long; keep the page heading bounded.
        display_title = title
        while heading.getlength(display_title) > WIDTH - 96:
            display_title = display_title[:-2] + "…"
        draw.text((48, 78), display_title, font=heading, fill="#203744")
        draw.text((48, 140), "找到想用的功能，照着指令发送即可", font=small, fill="#607482")
        y = 200
        for label, lines in page:
            block_height = 78 + LINE_HEIGHT * len(lines)
            draw.rounded_rectangle(
                (40, y, WIDTH - 40, y + block_height), radius=18, fill="white"
            )
            draw.text((64, y + 14), label, font=font, fill="#397b85")
            for row, line in enumerate(lines):
                draw.text((72, y + 62 + row * LINE_HEIGHT), line, font=font, fill="#293d48")
            y += block_height + 16
        draw.text((48, height - 48), "help 功能名 · 查看详细用法", font=small, fill="#607482")
        draw.text(
            (WIDTH - 180, height - 48), f"{index} / {len(pages)}", font=small, fill="#607482"
        )
        output = BytesIO()
        canvas.save(output, format="PNG")
        images.append(output.getvalue())
    return tuple(images)
