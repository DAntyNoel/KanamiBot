from __future__ import annotations

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Message
from PIL import Image, ImageFont

from kanamibot.core.help_image import MAX_HEIGHT, WIDTH, render_help_pages, wrap_text
from kanamibot.core.paths import DEFAULT_FONT_PATH

nonebot.init(_env_file=None)
assert nonebot.load_plugin("kanamibot.plugins.help") is not None

from kanamibot.plugins import help as menu  # noqa: E402


def test_overview_hides_rules_and_details_remain_searchable(monkeypatch):
    features = [
        menu.FeatureInfo(
            plugin_name="er_dak", title="Eternal Return", usage="#BS 对比 <昵称1> <昵称2>",
            entries=["正则: ^\\s*#BS.*$"],
        ),
        menu.FeatureInfo(plugin_name="custom", title="自定义", entries=["/custom", "正则: abc"]),
    ]
    monkeypatch.setattr(menu, "collect_features", lambda: features)
    overview = menu.build_help_text()
    assert "正则:" not in overview
    assert "#BS 对比" not in overview
    assert "不写模式时查排位" in overview
    assert "<内容> 表示必须填写" in overview
    assert "/custom" in overview
    detail = menu.build_help_text("永恒轮回")
    assert "#BS 对比 <昵称1> <昵称2>" in detail
    assert "自定义" not in detail
    assert "#BS 对比" in menu.build_help_text("#BS")
    assert "没有找到" in menu.build_help_text("不存在的功能")


def test_renderer_wraps_and_paginates_without_losing_lines():
    font = ImageFont.truetype(str(DEFAULT_FONT_PATH), 30)
    source = "超长指令" * 400
    lines = wrap_text(source, font, WIDTH - 144)
    assert "".join(lines) == source
    assert all(font.getlength(line) <= WIDTH - 144 for line in lines)
    pages = render_help_pages("长标题" * 50, [("完整用法", source + "\n" * 70 + "最后一条")])
    assert len(pages) > 2
    for data in pages:
        with Image.open(BytesIO(data)) as page:
            assert page.format == "PNG"
            assert page.width == WIDTH
            assert page.height <= MAX_HEIGHT


def test_handler_sends_only_images_in_page_order(monkeypatch):
    monkeypatch.setattr(menu, "build_help_images", lambda query: (b"page1", b"page2"))
    matcher = AsyncMock()
    asyncio.run(menu.handle_help(matcher, Message("永恒轮回")))
    assert matcher.send.await_count == 2
    for call in matcher.send.await_args_list:
        assert call.args[0].type == "image"
    matcher.finish.assert_awaited_once_with()


def test_dynamic_discovery_skips_libraries(monkeypatch):
    from types import SimpleNamespace

    metadata = SimpleNamespace(
        name="自定义", description="新功能", usage="/new", type="application"
    )
    library = SimpleNamespace(metadata=SimpleNamespace(type="library", name="依赖"), name="lib")
    plugin = SimpleNamespace(metadata=metadata, name="custom", module_name="test.custom")
    monkeypatch.setattr(menu, "get_loaded_plugins", lambda: [plugin, library])
    monkeypatch.setattr(menu, "_collect_matcher_entries", lambda: {"custom": ["/new"]})
    assert [feature.plugin_name for feature in menu.collect_features()] == ["custom"]
