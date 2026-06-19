from __future__ import annotations

import unittest

import nonebot

nonebot.init()


def parse_dynamic_message(dynamic_data: dict) -> object:
    from kanamibot.plugins.bilibili.dynamic_parser import parse_dynamic

    return parse_dynamic(dynamic_data)


class BilibiliDynamicParserTest(unittest.TestCase):
    def test_display_url_keeps_bilibili_jump_link_clickable(self) -> None:
        msg = parse_dynamic_message(
            {
                "type": "DYNAMIC_TYPE_WORD",
                "id": 1210000000000000000,
                "url": "//www.bilibili.com/opus/1210000000000000000",
                "pub_ts": 0,
                "name": "UP",
                "pub_time": "",
                "major": {
                    "opus": {
                        "title": "",
                        "summary": {
                            "rich_text_nodes": [
                                {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "hello"},
                            ],
                        },
                    },
                },
            }
        )

        self.assertIsNotNone(msg)
        self.assertIn(
            "==> https://www.bilibili.com/opus/1210000000000000000 <==",
            str(msg),
        )

    def test_forward_origin_url_keeps_bilibili_jump_link_clickable(self) -> None:
        msg = parse_dynamic_message(
            {
                "type": "DYNAMIC_TYPE_FORWARD",
                "id": 1210000000000000001,
                "url": "www.bilibili.com/opus/1210000000000000001",
                "pub_ts": 0,
                "name": "UP",
                "pub_time": "",
                "desc": {
                    "rich_text_nodes": [
                        {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "forward text"},
                    ],
                },
                "orig": {
                    "type": "DYNAMIC_TYPE_WORD",
                    "id_str": "1210000000000000002",
                    "basic": {"jump_url": "//www.bilibili.com/opus/1210000000000000002"},
                    "modules": {
                        "module_author": {
                            "pub_ts": 0,
                            "mid": 1,
                            "name": "Origin",
                            "pub_time": "",
                        },
                        "module_dynamic": {
                            "desc": {},
                            "major": {
                                "opus": {
                                    "title": "",
                                    "summary": {"rich_text_nodes": []},
                                },
                            },
                        },
                    },
                },
                "major": {},
            }
        )

        self.assertIsNotNone(msg)
        rendered = str(msg)
        self.assertIn(
            "源动态https://www.bilibili.com/opus/1210000000000000002",
            rendered,
        )
        self.assertIn(
            "==> https://www.bilibili.com/opus/1210000000000000001 <==",
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
