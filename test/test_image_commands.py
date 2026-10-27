from __future__ import annotations

import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import nonebot
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.exception import FinishedException

nonebot.init()


class ImageCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_save_image_ignores_attached_image_segment_in_tags(self) -> None:
        from kanamibot.plugins.image import save_image_handler

        image_segment = MessageSegment.image("https://example.invalid/image.png")
        event = SimpleNamespace(user_id=12345, group_id=67890, message_id=42)
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
        bot.call_api.assert_awaited_once_with(
            "set_msg_emoji_like", message_id=42, emoji_id="124", set=True, _timeout=2.0
        )
        matcher.send.assert_not_awaited()
        matcher.finish.assert_not_awaited()

    async def test_save_images_reacts_once_after_all_saves_even_when_reaction_fails(self) -> None:
        from kanamibot.plugins.image import save_image_handler

        for failure in (None, RuntimeError("reaction unsupported")):
            order = []
            event = SimpleNamespace(
                user_id=12345,
                group_id=67890,
                message_id=42,
                reply=SimpleNamespace(message_id=99),
            )
            bot, matcher = AsyncMock(), AsyncMock()

            async def react(api, *, order=order, failure=failure, **data):
                order.append("reaction")
                if failure:
                    raise failure

            bot.call_api.side_effect = react
            with (
                self.subTest(failure=failure),
                patch("kanamibot.plugins.image.get_first_superuser", AsyncMock(return_value=99999)),
                patch("kanamibot.plugins.image.get_folder_name", return_value="图片"),
                patch(
                    "kanamibot.plugins.image.download_all_images_from_event",
                    AsyncMock(return_value=[b"first", b"second"]),
                ),
                patch("kanamibot.plugins.image.guess_extension", return_value="png"),
                patch(
                    "kanamibot.plugins.image.save_image",
                    side_effect=lambda *a, order=order, **k: order.append("save"),
                ),
            ):
                await save_image_handler(
                    bot, event, matcher, Namespace(folder="图片", tags=[], sudo=True)
                )

            self.assertEqual(order, ["save", "save", "reaction"])
            self.assertEqual(bot.call_api.await_args.kwargs["message_id"], 42)
            matcher.send.assert_not_awaited()
            matcher.finish.assert_not_awaited()

    async def test_unsaved_images_keep_failure_or_duplicate_reply_without_ok(self) -> None:
        from kanamibot.plugins.image import save_image_handler

        for images in ([], [b"duplicate"]):
            bot, matcher = AsyncMock(), AsyncMock()
            matcher.finish.side_effect = FinishedException
            with (
                self.subTest(images=images),
                patch("kanamibot.plugins.image.get_first_superuser", AsyncMock(return_value=99999)),
                patch("kanamibot.plugins.image.get_folder_name", return_value="图片"),
                patch(
                    "kanamibot.plugins.image.download_all_images_from_event",
                    AsyncMock(return_value=images),
                ),
                patch("kanamibot.plugins.image.guess_extension", return_value="png"),
                patch(
                    "kanamibot.plugins.image.similar_images",
                    return_value=[{"id": "1", "filename": "1.png", "tags": []}],
                ),
                patch("kanamibot.plugins.image._image_path", return_value=Path("D:/test/1.png")),
                patch("kanamibot.plugins.image.save_image") as save,
            ):
                with self.assertRaises(FinishedException):
                    await save_image_handler(
                        bot,
                        SimpleNamespace(user_id=12345, group_id=67890, message_id=42),
                        matcher,
                        Namespace(folder="图片", tags=[], sudo=False),
                    )
            save.assert_not_called()
            bot.call_api.assert_not_awaited()
            reply = str(matcher.finish.await_args.args[0])
            self.assertIn("检测到相同图片" if images else "请附带图片", reply)

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
