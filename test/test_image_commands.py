from __future__ import annotations

import unittest
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import nonebot

nonebot.init()


class ImageCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_random_image_uses_group_visible_images(self) -> None:
        from kanamibot.plugins.image import pick_image_handler

        visible_image = {"id": "0001", "folder": "gallery", "filename": "0001.png"}
        event = SimpleNamespace(group_id=12345)
        matcher = Mock()

        with (
            patch("kanamibot.plugins.image.get_folder_name", return_value="gallery"),
            patch(
                "kanamibot.plugins.image.get_visible_images",
                return_value=[visible_image],
            ) as get_visible,
            patch("kanamibot.plugins.image.random.choice", return_value=visible_image),
            patch(
                "kanamibot.plugins.image._send_stored_image",
                new=AsyncMock(),
            ) as send_image,
        ):
            await pick_image_handler(event, matcher, Namespace(folder="gallery"))

        get_visible.assert_called_once_with("gallery", 12345)
        send_image.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
