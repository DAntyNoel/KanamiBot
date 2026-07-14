from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import kanamibot.plugins.codex_gpt as codex_gpt


class CodexGPTCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_image_clear_starts_new_session_without_downloading_images(self) -> None:
        event = SimpleNamespace(message_id=1, user_id=2)

        with (
            patch.object(codex_gpt, "_extract_image_payload", return_value="clear"),
            patch.object(codex_gpt, "_session_id", return_value="private:2"),
            patch.object(codex_gpt, "_download_event_images", AsyncMock()) as download_images,
            patch.object(codex_gpt.store, "clear", AsyncMock()) as clear_session,
            patch.object(codex_gpt.codex_image, "finish", AsyncMock()) as finish,
            patch.object(codex_gpt, "_reply", return_value="reply:"),
        ):
            await codex_gpt.handle_codex_image(SimpleNamespace(), event)

        clear_session.assert_awaited_once_with("private:2")
        download_images.assert_not_awaited()
        finish.assert_awaited_once_with("reply:已新建对话，上下文清空。")


if __name__ == "__main__":
    unittest.main()
