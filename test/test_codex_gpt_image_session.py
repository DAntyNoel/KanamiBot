from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kanamibot.plugins.codex_gpt.session import ImageSessionStore


class ImageSessionStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_context_is_persisted_bounded_and_clearable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "image_sessions.json"
            store = ImageSessionStore(max_context_chars=80, path=path)

            await store.add_turn("private:2", "画一只白猫", "白猫坐在窗台上")
            await store.add_turn("private:2", "改成夜景", "窗外变成了夜景")

            prompt = await store.build_prompt("private:2", "让猫抬头")
            self.assertIn("此前图片对话", prompt)
            self.assertIn("当前请求：\n让猫抬头", prompt)
            context = prompt.split("当前请求：", 1)[0]
            self.assertLessEqual(len(context.removeprefix("此前图片对话：\n").strip()), 80)

            reloaded = ImageSessionStore(max_context_chars=80, path=path)
            self.assertEqual(
                await reloaded.build_prompt("private:2", "继续"),
                await store.build_prompt("private:2", "继续"),
            )

            await reloaded.clear("private:2")
            self.assertEqual(await reloaded.build_prompt("private:2", "重新画"), "重新画")

    async def test_zero_limit_disables_image_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ImageSessionStore(0, Path(temp_dir) / "image_sessions.json")
            await store.add_turn("private:2", "第一轮", "已生成")
            self.assertEqual(await store.build_prompt("private:2", "第二轮"), "第二轮")


if __name__ == "__main__":
    unittest.main()
