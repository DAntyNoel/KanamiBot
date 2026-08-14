from __future__ import annotations

import unittest
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import nonebot
from nonebot.adapters.onebot.v11 import MessageSegment

nonebot.init()


class ImageCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_save_image_ignores_attached_image_segment_in_tags(self) -> None:
        from kanamibot.plugins.image import save_image_handler

        image_segment = MessageSegment.image("https://example.invalid/image.png")
        event = SimpleNamespace(user_id=12345, group_id=67890)
        bot = AsyncMock()
        matcher = AsyncMock()
        args = Namespace(
            folder="图片",
            tags=["可爱", image_segment, "[CQ:image,file=literal]"],
            sudo=True,
        )

        with (
            patch(
                "kanamibot.plugins.image.get_first_superuser",
                new=AsyncMock(return_value=99999),
            ),
            patch("kanamibot.plugins.image.get_folder_name", return_value="图片"),
            patch(
                "kanamibot.plugins.image.download_all_images_from_event",
                new=AsyncMock(return_value=[b"image-bytes"]),
            ),
            patch("kanamibot.plugins.image.guess_extension", return_value="png"),
            patch("kanamibot.plugins.image.save_image") as save_image,
        ):
            await save_image_handler(bot, event, matcher, args)

        save_image.assert_called_once_with(
            b"image-bytes",
            "png",
            "图片",
            tags=["可爱"],
            qq=12345,
            group=67890,
        )

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
