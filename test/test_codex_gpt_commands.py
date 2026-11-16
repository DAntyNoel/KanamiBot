from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import kanamibot.plugins.codex_gpt as codex_gpt
from kanamibot.plugins.codex_gpt.session import ImageSessionStore


class CodexGPTCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_image_clear_starts_new_session_without_downloading_images(self) -> None:
        for command in ("clear", "reset"):
            with self.subTest(command=command):
                event = SimpleNamespace(message_id=1, user_id=2)
                with (
                    patch.object(codex_gpt, "_extract_image_payload", return_value=command),
                    patch.object(codex_gpt, "_session_id", return_value="private:2"),
                    patch.object(
                        codex_gpt, "_download_event_images", AsyncMock()
                    ) as download_images,
                    patch.object(codex_gpt.image_store, "clear", AsyncMock()) as clear_session,
                    patch.object(codex_gpt.store, "clear", AsyncMock()) as clear_chat_session,
                    patch.object(codex_gpt.codex_image, "finish", AsyncMock()) as finish,
                    patch.object(codex_gpt, "_reply", return_value="reply:"),
                ):
                    await codex_gpt.handle_codex_image(SimpleNamespace(), event)

                clear_session.assert_awaited_once_with("private:2")
                clear_chat_session.assert_not_awaited()
                download_images.assert_not_awaited()
                finish.assert_awaited_once_with("reply:已清空图片上下文。")

    async def test_image_model_routing_lists_all_models_without_downloading(self) -> None:
        event = SimpleNamespace(message_id=1, user_id=2)
        with tempfile.TemporaryDirectory() as temp_dir:
            image_store = ImageSessionStore(80, Path(temp_dir) / "images.json")
            await image_store.set_model("private:2", "custom-image")
            with (
                patch.object(codex_gpt, "image_store", image_store),
                patch.object(codex_gpt, "_extract_payload", return_value="image model"),
                patch.object(codex_gpt, "_session_id", return_value="private:2"),
                patch.object(codex_gpt, "_reply", return_value=""),
                patch.object(
                    codex_gpt,
                    "_get_models",
                    AsyncMock(
                        return_value=["gpt-5.5", "gpt-image-2", "custom-image"]
                        + [f"custom-{i}" for i in range(65)]
                    ),
                ),
                patch.object(codex_gpt, "_download_event_images", AsyncMock()) as download,
                patch.object(codex_gpt.codex_gpt, "finish", AsyncMock()) as finish,
            ):
                await codex_gpt.handle_codex_gpt(SimpleNamespace(), event)
            download.assert_not_awaited()
            reply = finish.call_args.args[0]
            self.assertIn("当前图片模型：custom-image", reply)
            self.assertIn("1. gpt-5.5", reply)
            self.assertIn("2. gpt-image-2", reply)
            self.assertIn("3. custom-image", reply)
            self.assertIn("68. custom-64", reply)
            self.assertIn("#gpt image model use", reply)

    async def test_image_model_permission_selection_errors_and_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_store = ImageSessionStore(80, Path(temp_dir) / "images.json")
            with (
                patch.object(codex_gpt, "image_store", image_store),
                patch.object(codex_gpt, "SUPERUSER", AsyncMock(return_value=False)) as permission,
                patch.object(
                    codex_gpt,
                    "_get_models",
                    AsyncMock(return_value=["gpt-5.5", "gpt-image-2", "custom-image"]),
                ),
                patch.object(codex_gpt, "_reply", return_value=""),
                patch.object(codex_gpt.codex_gpt, "finish", AsyncMock()) as finish,
                patch.object(codex_gpt.store, "set_model", AsyncMock()) as chat_model,
            ):

                async def command(value):
                    await codex_gpt._handle_image_model_command(
                        SimpleNamespace(), "private:2", value, SimpleNamespace(user_id=2)
                    )

                await command("use 2")
                self.assertIsNone(await image_store.get_model("private:2"))
                self.assertIn("只有 superuser", finish.call_args.args[0])
                permission.return_value = True
                for value, expected in (
                    ("use 2", "gpt-image-2"),
                    ("custom-image", "custom-image"),
                    ("use gpt-5.5", "gpt-5.5"),
                ):
                    await command(value)
                    self.assertEqual(await image_store.get_model("private:2"), expected)
                for invalid in ("use 99", "use nonexistent", "use gpt-", "use"):
                    await command(invalid)
                    self.assertIn("切换图片模型失败", finish.call_args.args[0])
                    self.assertEqual(await image_store.get_model("private:2"), "gpt-5.5")
                await command("reset")
                self.assertIsNone(await image_store.get_model("private:2"))
                chat_model.assert_not_awaited()

    async def test_generation_and_edit_use_selected_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_store = ImageSessionStore(80, Path(temp_dir) / "images.json")
            await image_store.set_model("private:2", "custom-image")
            with (
                patch.object(codex_gpt, "image_store", image_store),
                patch.object(
                    codex_gpt,
                    "config",
                    SimpleNamespace(is_configured=True, image_model="default-image"),
                ),
                patch.object(codex_gpt, "_reply", return_value=""),
                patch.object(codex_gpt.codex_image, "send", AsyncMock()),
                patch.object(
                    codex_gpt.client,
                    "create_image",
                    AsyncMock(
                        return_value=SimpleNamespace(
                            data=b"image", text=None, revised_prompt=None, mime_type="image/png"
                        )
                    ),
                ) as create,
                patch.object(codex_gpt, "_store_codex_image_assets", return_value=[]),
                patch.object(codex_gpt, "_store_codex_image_asset", return_value=None) as save,
            ):
                for images in ([], [b"input"]):
                    await codex_gpt._run_image(
                        SimpleNamespace(), SimpleNamespace(), "private:2", "draw", images
                    )
                    self.assertEqual(create.call_args.kwargs["model"], "custom-image")
                    self.assertEqual(create.call_args.kwargs["images"], images)
                    self.assertEqual(save.call_args.kwargs["model"], "custom-image")
                await image_store.set_model("private:2", None)
                await codex_gpt._run_image(
                    SimpleNamespace(), SimpleNamespace(), "private:2", "draw"
                )
                self.assertEqual(create.call_args.kwargs["model"], "default-image")


if __name__ == "__main__":
    unittest.main()
