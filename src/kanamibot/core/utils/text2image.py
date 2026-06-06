from __future__ import annotations

import os
import random
from io import BytesIO
from typing import Any

from nonebot.log import logger
from PIL import Image, ImageDraw, ImageFont

from ..paths import DEFAULT_FONT_PATH


def get_random_color(alpha: bool = False) -> str:
    """
    获取随机颜色
    """
    color = '#'
    colorchoice = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 'A', 'B', 'C', 'D', 'E', 'F']
    for _ in range(6):
        color += f'{random.choice(colorchoice)}'
    if alpha:
        for _ in range(2):
            color += f'{random.choice(colorchoice)}'
    return color

def load_font(fontsize: int, bold: bool = False):
    """
    加载字体，带异常处理
    """
    try:
        # 这里你可以根据 bold 参数选择不同的字体文件
        return ImageFont.truetype(DEFAULT_FONT_PATH, fontsize)
    except OSError:
        # 如果找不到字体文件，使用 PIL 默认字体（不支持中文）或尝试系统字体
        # 实际部署建议确保 DEFAULT_FONT_PATH 存在
        return ImageFont.load_default()

def text_to_imagebytes(
    text: str | dict[Any, Any] | set[Any] | list[Any] | tuple[Any, ...],
    fontsize: int = 30,
    bold: bool = False,
    fontcolor: tuple[int, int, int] = (0, 0, 0),
    bgkcolor: tuple[int, int, int] = (255, 255, 255),
    backimgpath: str | None = None,
    imgbytes: bytes | None = None,
    needtobase64: bool = True,
) -> bytes | None:
    """
    将文本转换为图片并返回 NoneBot 消息段
    
    Returns: 
        MessageSegment: 可以直接 await matcher.send() 的对象
    """
    texts: list[str] = []
    
    # --- 数据预处理 ---
    if isinstance(text, str):
        texts = text.replace('\t', '    ').split('\n')
    elif isinstance(text, dict):
        for k, v in text.items():
            texts.append(f'{k}:{v}'.replace('\t', '    ').strip())
    elif isinstance(text, (list, set, tuple)):
        for item in text:
            texts.append(f'{item}'.replace('\t', '    ').strip())
    else:
        logger.warning("文字转图片输入了不支持的类型: %s", type(text))
        return None
            
    # --- 字体加载 ---
    font = load_font(fontsize, bold)

    # --- 计算画布尺寸 ---
    maxwidth = fontsize
    for item in texts:
        # Pillow 10.0+ 移除了 getsize，改用 getlength
        if hasattr(font, 'getlength'):
            wd = int(font.getlength(item))
        else:
            # 兼容旧版 Pillow
            wd = font.getsize(item)[0]
            
        if wd > maxwidth:
            maxwidth = wd

    # --- 创建背景 ---
    if backimgpath and os.path.exists(backimgpath):
        bgimg = Image.open(backimgpath).convert("RGB")
    elif imgbytes:
        bgimg = Image.open(BytesIO(imgbytes)).convert("RGB")
    else:
        # 动态计算高度
        bg_height = (len(texts) + 2) * (fontsize + 5)
        bg_width = maxwidth + 2 * (fontsize + 5)
        bgimg = Image.new('RGB', (bg_width, bg_height), bgkcolor)

    bx, by = bgimg.size
    textdraw = ImageDraw.Draw(bgimg)

    # --- 绘制装饰性边框像素 (保留原作者风格) ---
    textdraw.line((1, 1, 1, 1), get_random_color(), 1)
    textdraw.line((bx - 2, by - 2, bx - 2, by - 2), get_random_color(), 1)
    textdraw.line((1, by - 2, 1, by - 2), get_random_color(), 1)
    textdraw.line((bx - 2, 1, bx - 2, 1), get_random_color(), 1)

    # --- 绘制文字 ---
    for i in range(len(texts)):
        textdraw.text((fontsize, i * (fontsize + 5) + fontsize), 
                      text=f'{texts[i].strip()}',
                      font=font, 
                      fill=fontcolor)

    # --- 输出处理 ---
    output = BytesIO()
    bgimg.save(output, format='PNG')
    if needtobase64:
        logger.debug("needtobase64 is kept for compatibility; bytes are returned directly.")
    return output.getvalue()
