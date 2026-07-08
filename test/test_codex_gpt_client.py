from __future__ import annotations

import unittest
from types import SimpleNamespace

import httpx

from kanamibot.plugins.codex_gpt.client import (
    CodexGPTClient,
    CodexGPTError,
    CodexGPTImageTextResponse,
)


class CodexGPTClientImageResponseTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client = CodexGPTClient(SimpleNamespace(image_timeout_seconds=1.0))

    async def test_image_response_uses_item_text_when_image_payload_missing(self) -> None:
        response = httpx.Response(
            200,
            json={
                "data": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": "无法生成图片，但可以给你一版替代描述。",
                            }
                        ]
                    }
                ]
            },
        )

        with self.assertRaises(CodexGPTImageTextResponse) as cm:
            await self.client._parse_image_response(response)

        self.assertEqual(cm.exception.text, "无法生成图片，但可以给你一版替代描述。")

    async def test_image_response_uses_top_level_output_text_when_data_missing(self) -> None:
        response = httpx.Response(
            200,
            json={"output_text": "这次没有返回图片，只返回了文字说明。"},
        )

        with self.assertRaises(CodexGPTImageTextResponse) as cm:
            await self.client._parse_image_response(response)

        self.assertEqual(cm.exception.text, "这次没有返回图片，只返回了文字说明。")

    async def test_image_response_keeps_final_text_when_image_payload_exists(self) -> None:
        response = httpx.Response(
            200,
            json={
                "data": [
                    {
                        "b64_json": "iVBORw0KGgo=",
                        "content": [
                            {"type": "reasoning", "text": "中间思考不要发出去。"},
                            {"type": "output_text", "text": "这是最终要和图片一起发出的文字。"},
                        ],
                    }
                ]
            },
        )

        image = await self.client._parse_image_response(response)

        self.assertEqual(image.text, "这是最终要和图片一起发出的文字。")

    async def test_image_response_handles_separate_text_and_image_items(self) -> None:
        response = httpx.Response(
            200,
            json={
                "data": [
                    {"type": "reasoning", "text": "中间思考不要发出去。"},
                    {"type": "output_text", "text": "第一段不是最后文本。"},
                    {"b64_json": "iVBORw0KGgo="},
                    {"type": "output_text", "text": "最终文本。"},
                ]
            },
        )

        image = await self.client._parse_image_response(response)

        self.assertEqual(image.text, "最终文本。")

    async def test_image_response_keeps_error_path_when_only_error_message_exists(self) -> None:
        response = httpx.Response(
            200,
            json={"data": [{}], "error": {"message": "provider error"}},
        )

        with self.assertRaises(CodexGPTError):
            await self.client._parse_image_response(response)


if __name__ == "__main__":
    unittest.main()
