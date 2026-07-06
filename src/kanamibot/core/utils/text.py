from __future__ import annotations

from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from rapidfuzz import fuzz, process


def extract_pure_text(raw_args: list) -> str:
    """
    将包含 str 和 MessageSegment 的混合列表转换为纯文本字符串。
    会自动过滤掉图片、@等非文字元素，提取纯文本 Prompt。
    """
    if not raw_args:
        return ""

    # 1. 创建一个空的 Message 对象容器
    msg = Message()

    # 2. 遍历参数列表，将其还原为 Message 对象
    for item in raw_args:
        if isinstance(item, str):
            # 如果是字符串，转为文本段加入
            msg.append(MessageSegment.text(item))
        elif isinstance(item, MessageSegment):
            # 如果是消息段（如图片、表情），直接加入
            msg.append(item)

    # 3. 使用 NoneBot 自带的 extract_plain_text() 方法
    # 这个方法会自动提取所有 text 类型的消息，忽略 Image/Record/At 等
    return msg.extract_plain_text().strip()

def get_best_items_by_text_list(
    text_list: list[tuple[str, Any]],
    text: str,
    _threshold: int = 60,
    _limit: int = 6,
) -> list[tuple[Any, int | float]]:
    '''根据文本内容做模糊匹配 (RapidFuzz 优化版)'''
    matches = process.extract(text, text_list, limit=_limit, scorer=fuzz.WRatio)
    results = []
    for match in matches:
        item_tuple = match[0] # (str, Any)
        score = match[1]
        if score >= _threshold:
            results.append((item_tuple[1], score))
    return results
