from __future__ import annotations

import random
from io import BytesIO

from nonebot.log import logger
from PIL import Image

from kanamibot.core.paths import DEFAULT_FONT_PATH, FILES_DIR

BACKGROUND_IMAGE_DIR = FILES_DIR / "wordcloud_images"

STOP_WORDS = {
    "一个",
    "一些",
    "一切",
    "一样",
    "不是",
    "为了",
    "为什么",
    "什么",
    "他们",
    "你们",
    "我们",
    "自己",
    "大家",
    "这个",
    "那个",
    "这些",
    "那些",
    "这么",
    "那么",
    "怎么",
    "怎样",
    "如何",
    "多少",
    "哪个",
    "哪里",
    "啥",
    "就是",
    "现在",
    "然后",
    "因为",
    "所以",
    "但是",
    "如果",
    "还是",
    "没有",
    "可以",
    "应该",
    "感觉",
    "真的",
    "其实",
    "这里",
    "那里",
    "啊啊",
    "啊啊啊",
    "哈哈",
    "哈哈哈",
    "还有",
    "已经",
    "不会",
    "知道",
    "一下",
    "今天",
    "明天",
    "昨天",
    "人家",
    "东西",
}


def gen_wc(contents: list[str]) -> bytes:
    try:
        import jieba
        import numpy as np
        from wordcloud import ImageColorGenerator, WordCloud
    except ImportError as exc:
        logger.warning("[daily_report] wordcloud dependencies unavailable: %s", exc)
        return b""

    text_blob = " ".join(content.strip() for content in contents if content.strip())
    if not text_blob:
        return b""

    filtered_words = [
        word.strip()
        for word in jieba.cut(text_blob)
        if len(word.strip()) > 1 and word.strip() not in STOP_WORDS
    ]
    if not filtered_words:
        logger.info("[daily_report] wordcloud skipped because no effective words were found.")
        return b""

    mask = None
    if BACKGROUND_IMAGE_DIR.is_dir():
        images = sorted(
            path
            for path in BACKGROUND_IMAGE_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        )
        if images:
            selected_image = random.choice(images)
            try:
                with Image.open(selected_image) as image:
                    if image.mode == "P":
                        image = image.convert("RGBA")
                    elif image.mode == "CMYK":
                        image = image.convert("RGB")
                    mask = np.asarray(image)
            except Exception as exc:
                logger.warning(
                    "[daily_report] failed to read wordcloud background %s: %s",
                    selected_image,
                    exc,
                )

    font_path = DEFAULT_FONT_PATH if DEFAULT_FONT_PATH.exists() else None
    wc_kwargs = {
        "font_path": str(font_path) if font_path else None,
        "background_color": "white",
        "mode": "RGB",
        "mask": mask,
        "width": 1080,
        "height": 720,
        "random_state": 42,
    }
    wordcloud = WordCloud(**wc_kwargs).generate(" ".join(filtered_words))
    if mask is not None:
        wordcloud.recolor(color_func=ImageColorGenerator(mask, default_color=(32, 32, 32)))

    output = BytesIO()
    wordcloud.to_image().save(output, format="PNG")
    return output.getvalue()
