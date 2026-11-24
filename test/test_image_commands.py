from __future__ import annotations

import unittest
from argparse import Namespace
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import nonebot
from nonebot.adapters.onebot.v11 import Adapter, Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.exception import FinishedException, ParserExit, StopPropagation
from nonebot.message import check_and_run_matcher
from nonebot.rule import TRIE_VALUE, TrieRule

nonebot.init()


class ImageCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_save_rejects_removed_sudo_options(self) -> None:
        from kanamibot.plugins.image import save_parser

        for option in ("-s", "--sudo"):
            with self.subTest(option=option), self.assertRaises(ParserExit):
                save_parser.parse_args(["图片", option])

    async def test_batch_duplicates_are_separate_forward_nodes(self) -> None:
        from kanamibot.plugins.image import save_image_handler

        existing = {"id": "1", "filename": "1.png", "tags": ["原标签"]}
        for new_folder, all_duplicate, storage_duplicate in (
            (False, False, False),
            (True, False, False),
            (False, True, False),
            (False, False, True),
        ):
            with self.subTest(
                new_folder=new_folder,
                all_duplicate=all_duplicate,
                storage_duplicate=storage_duplicate,
            ):
                bot, matcher = AsyncMock(), AsyncMock()
                bot.self_id = "99999"
                matcher.finish.side_effect = FinishedException
                registry = [existing] if all_duplicate else []

                def lookup(*args, registry=registry, storage_duplicate=storage_duplicate):
                    return registry.copy() if not storage_duplicate else []

                def save(*args, registry=registry, storage_duplicate=storage_duplicate, **kwargs):
                    registry.append(existing)
                    return {**existing, "status": "exists" if storage_duplicate else "created"}

                with (
                    patch(
                        "kanamibot.plugins.image.get_first_superuser", AsyncMock(return_value=12345)
                    ),
                    patch(
                        "kanamibot.plugins.image.get_folder_name",
                        side_effect=[None if new_folder else "图片", "图片"],
                    ),
                    patch(
                        "kanamibot.plugins.image.download_all_images_from_event",
                        AsyncMock(return_value=[b"same-image"] * 3),
                    ),
                    patch("kanamibot.plugins.image.guess_extension", return_value="png"),
                    patch("kanamibot.plugins.image.similar_images", side_effect=lookup),
                    patch("kanamibot.plugins.image.save_image", side_effect=save) as upload,
                    patch(
                        "kanamibot.plugins.image._image_path", return_value=Path("D:/test/1.png")
                    ),
                ):
                    with self.assertRaises(FinishedException):
                        await save_image_handler(
                            bot,
                            SimpleNamespace(user_id=12345, group_id=67890, message_id=42),
                            matcher,
                            Namespace(folder="图片", tags=[]),
                        )

                saved = 0 if all_duplicate or storage_duplicate else 1
                self.assertEqual(upload.call_count, 3 if storage_duplicate else saved)
                calls = bot.call_api.await_args_list
                self.assertEqual(len(calls), 1 + bool(saved))
                self.assertEqual(calls[-1].args, ("send_group_forward_msg",))
                self.assertEqual(calls[-1].kwargs["group_id"], 67890)
                nodes = calls[-1].kwargs["messages"]
                self.assertEqual(len(nodes), 1 + 3 - saved)
                self.assertIn(f"保存了 {saved} 张", str(nodes[0].data["content"]))
                for index, node in enumerate(nodes[1:], start=1 + saved):
                    content = node.data["content"]
                    self.assertIn(f"第 {index} 张", str(content))
                    self.assertIn("已跳过", str(content))
                    self.assertNotIn("-s", str(content))
                    self.assertEqual(len(content["image"]), 2)
                matcher.finish.assert_awaited_once_with()

    async def test_save_image_ignores_attached_image_segment_in_tags(self) -> None:
        from kanamibot.plugins.image import save_image_handler

        image_segment = MessageSegment.image("https://example.invalid/image.png")
        event = SimpleNamespace(user_id=12345, group_id=67890, message_id=42)
        bot = AsyncMock()
        matcher = AsyncMock()
        args = Namespace(
            folder="图片",
            tags=["可爱", image_segment, "[CQ:image,file=literal]"],
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
            patch("kanamibot.plugins.image.similar_images", return_value=[]),
            patch(
                "kanamibot.plugins.image.save_image", return_value={"status": "created"}
            ) as save_image,
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
                patch("kanamibot.plugins.image.similar_images", return_value=[]),
                patch(
                    "kanamibot.plugins.image.save_image",
                    side_effect=lambda *a, order=order, **k: (
                        order.append("save") or {"status": "created"}
                    ),
                ),
            ):
                await save_image_handler(
                    bot, event, matcher, Namespace(folder="图片", tags=[])
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
                        Namespace(folder="图片", tags=[]),
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

    async def test_bare_save_without_images_does_not_create_gallery(self) -> None:
        from kanamibot.plugins import image

        event = SimpleNamespace(user_id=12345)
        matcher = AsyncMock()
        matcher.finish.side_effect = FinishedException
        with (
            patch.object(image, "get_first_superuser", return_value=12345),
            patch.object(image, "get_folder_name", return_value=None) as get_folder,
            patch.object(image, "download_all_images_from_event", return_value=[]),
            self.assertRaises(FinishedException),
        ):
            await image.save_image_handler(
                AsyncMock(), event, matcher, Namespace(folder="猫图", tags=[]), bare_alias=True
            )

        get_folder.assert_called_once_with("猫图")
        matcher.finish.assert_awaited_once_with(None)


class BareImageAliasTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from kanamibot.core.group_manager import group_config

        self.bot = Bot(Adapter(nonebot.get_driver()), "99999")
        self.send = self.enterContext(patch.object(self.bot, "send", new_callable=AsyncMock))
        self.enterContext(
            patch.object(group_config, "config", {"100": {"modules": {}, "blacklist": []}})
        )

    async def dispatch(
        self, matcher, text: str, command: str | None = None, reply: int | None = None
    ) -> None:
        message = Message(text)
        if reply is not None:
            message += MessageSegment.reply(reply)
        event = GroupMessageEvent.model_validate(
            {
                "time": 0,
                "self_id": 99999,
                "post_type": "message",
                "message_type": "group",
                "sub_type": "normal",
                "message_id": 10,
                "group_id": 100,
                "user_id": 200,
                "message": message,
                "raw_message": str(message),
                "font": 0,
                "sender": {"user_id": 200, "role": "owner"},
            }
        )
        prefix = "/" if text.startswith("/") else ""
        state = {}
        with patch.object(TrieRule, "prefix", TrieRule.prefix.copy()):
            if command is not None:
                TrieRule.prefix[prefix + command] = TRIE_VALUE(prefix, (command,))
            TrieRule.get_value(self.bot, event, state)
        await check_and_run_matcher(matcher, self.bot, event, state)

    async def test_ambiguous_gallery_listing_is_quiet(self) -> None:
        from kanamibot.plugins import image

        with (
            patch.object(image, "get_folder_name", return_value=None) as get_folder,
            patch.object(image, "create_image_gallery", return_value=b"image") as create_gallery,
        ):
            await self.dispatch(image.list_image_matcher, "所有人集合", "所有")
            await self.dispatch(image.list_image_matcher, "所有", "所有")
            get_folder.assert_not_called()
            await self.dispatch(image.list_image_matcher, "所有 不存在的图库", "所有")
            self.send.assert_not_awaited()

            with self.assertRaises(StopPropagation):
                await self.dispatch(image.list_image_matcher, "/所有 不存在的图库", "所有")
            self.assertEqual(str(self.send.await_args.kwargs["message"]), "图库不存在。")

            get_folder.return_value = "猫图"
            with self.assertRaises(StopPropagation):
                await self.dispatch(image.list_image_matcher, "所有 猫图", "所有")
            create_gallery.assert_called_once_with("猫图", 100)

    async def test_delete_alias_requires_a_command_and_a_tracked_reply(self) -> None:
        from kanamibot.plugins import image

        with (
            patch.object(image.send_buffer, "get", side_effect={99: ("1", "猫图")}.get),
            patch.object(image.send_buffer, "remove"),
            patch.object(image, "delete_image") as delete,
        ):
            await self.dispatch(image.delete_image_matcher, "删除一下", "删除", reply=99)
            await self.dispatch(image.delete_image_matcher, "删除 这条消息", "删除", reply=99)
            await self.dispatch(image.delete_image_matcher, "删除", "删除", reply=98)
            await self.dispatch(image.delete_image_matcher, "删除", "删除")
            self.send.assert_not_awaited()
            delete.assert_not_called()

            with self.assertRaises(StopPropagation):
                await self.dispatch(image.delete_image_matcher, "删除", "删除", reply=99)
            delete.assert_called_once_with("1", "猫图")
            self.assertEqual(str(self.send.await_args.kwargs["message"]), "图片已删除。")

    async def test_chat_about_memes_does_not_send_missing_template_errors(self) -> None:
        from kanamibot.plugins import memes

        missing_template = memes.MemeError("模板不存在")
        with patch.object(memes, "_get_meme_info", side_effect=missing_template) as info:
            await self.dispatch(memes.meme_matcher, "表情包真好笑", "表情包")
            await self.dispatch(memes.meme_matcher, "表情包", "表情包")
            info.assert_not_awaited()
            await self.dispatch(memes.meme_matcher, "表情包 真好笑", "表情包")
            self.send.assert_not_awaited()

            with self.assertRaises(StopPropagation):
                await self.dispatch(memes.meme_matcher, "meme missing", "meme")
            self.assertEqual(str(self.send.await_args.kwargs["message"]), "模板不存在")

    async def test_group_messages_do_not_interrupt_private_image_processing(self) -> None:
        from kanamibot.plugins import image_obfuscator

        pending = {"99999:200": image_obfuscator._Pending("encode", monotonic() + 60)}
        matcher = image_obfuscator.image_obfuscator
        with (
            patch.object(image_obfuscator, "_pending", pending),
            patch.object(image_obfuscator, "download_all_images_from_event", return_value=[]),
        ):
            await self.dispatch(matcher, "结束")
            with self.assertRaises(StopPropagation):
                await self.dispatch(matcher, "混淆")
            self.send.assert_not_awaited()
            self.assertIn("99999:200", pending)


if __name__ == "__main__":
    unittest.main()
