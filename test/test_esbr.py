from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init()

from kanamibot.plugins import esbr  # noqa: E402


class FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = esbr.PNG_SIGNATURE + b"card",
        stderr: bytes = b"",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True


class ESBRPluginTest(unittest.IsolatedAsyncioTestCase):
    def test_extract_player_name(self) -> None:
        self.assertEqual(esbr.extract_player_name('#ER "Player Name"'), '"Player Name"')
        self.assertEqual(esbr.extract_player_name("  #er   B站丨咕咕禽OC  "), "B站丨咕咕禽OC")
        self.assertEqual(esbr.extract_player_name("#ER"), "")

    async def test_render_player_overview_uses_cli_arguments_without_shell(self) -> None:
        process = FakeProcess()
        spawn = AsyncMock(return_value=process)

        with patch.object(esbr.asyncio, "create_subprocess_exec", spawn):
            result = await esbr.render_player_overview(
                'Player "Name"',
                executable=r"D:\ERBS-plugin\.venv\Scripts\erbs.exe",
            )

        self.assertEqual(result, esbr.PNG_SIGNATURE + b"card")
        spawn.assert_awaited_once_with(
            r"D:\ERBS-plugin\.venv\Scripts\erbs.exe",
            "overview",
            'Player "Name"',
            "--format",
            "bytes",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def test_render_player_overview_maps_player_not_found(self) -> None:
        process = FakeProcess(returncode=3, stdout=b"", stderr=b"player not found")

        with patch.object(
            esbr.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=process),
        ):
            with self.assertRaisesRegex(esbr.ERBSQueryError, "player not found") as caught:
                await esbr.render_player_overview("unknown")

        self.assertEqual(
            caught.exception.user_message,
            "未找到玩家「unknown」，请检查玩家名后重试。",
        )

    async def test_render_player_overview_rejects_non_png_output(self) -> None:
        process = FakeProcess(stdout=b"not an image")

        with patch.object(
            esbr.asyncio,
            "create_subprocess_exec",
            AsyncMock(return_value=process),
        ):
            with self.assertRaisesRegex(esbr.ERBSQueryError, "invalid PNG") as caught:
                await esbr.render_player_overview("player")

        self.assertEqual(caught.exception.user_message, "玩家概览图片生成失败，请稍后重试。")


if __name__ == "__main__":
    unittest.main()
