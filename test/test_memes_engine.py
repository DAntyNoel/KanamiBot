from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init()

from kanamibot.plugins import memes  # noqa: E402


class FakeImageAssetMissing:
    def __init__(self, path: str) -> None:
        self.path = path


FakeImageAssetMissing.__name__ = "ImageAssetMissing"


class MemesEngineTest(unittest.IsolatedAsyncioTestCase):
    async def test_meme_info_uses_in_process_engine(self) -> None:
        info = await memes._get_meme_info("luxun_say")

        self.assertEqual(info["key"], "luxun_say")
        self.assertEqual(info["params_type"]["min_images"], 0)
        self.assertEqual(info["params_type"]["max_texts"], 1)

    async def test_generate_passes_sender_name_to_engine_images(self) -> None:
        captured: dict[str, object] = {}

        class FakeMeme:
            def generate(self, images, texts, options):
                captured.update(images=images, texts=texts, options=options)
                return b"generated"

        fake_engine = SimpleNamespace(
            get_meme=lambda key: FakeMeme(),
            Image=lambda name, data: SimpleNamespace(name=name, data=data),
        )

        with patch.object(memes, "MEME_ENGINE", fake_engine):
            result = await memes._generate_meme(
                "fake",
                images=[b"image"],
                texts=["text"],
                args={"option": True},
                image_name="测试用户",
            )

        self.assertEqual(result, b"generated")
        self.assertEqual(captured["images"][0].name, "测试用户")
        self.assertEqual(captured["texts"], ["text"])
        self.assertEqual(captured["options"], {"option": True})

    async def test_missing_asset_syncs_resources_and_retries(self) -> None:
        calls = 0

        class FakeMeme:
            def generate(self, images, texts, options):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return FakeImageAssetMissing("fake/0.png")
                return b"generated-after-sync"

        fake_engine = SimpleNamespace(
            get_meme=lambda key: FakeMeme(),
            Image=lambda name, data: SimpleNamespace(name=name, data=data),
        )

        with (
            patch.object(memes, "MEME_ENGINE", fake_engine),
            patch.object(memes, "_update_resources", new=AsyncMock()) as update_resources,
        ):
            result = await memes._generate_meme(
                "fake",
                images=[],
                texts=["text"],
                args={},
                image_name="测试用户",
            )

        self.assertEqual(result, b"generated-after-sync")
        update_resources.assert_awaited_once()
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
