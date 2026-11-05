"""Group image classification; the CLI also works without starting NoneBot."""

from nonebot import get_driver
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="Image Classifier",
    description="自动分类群聊及私聊图片，临时保留48小时，23点私聊超管预览。",
    usage="超管私聊：图ab 查看A1B1缩略图；图ab 3 或 图aabb 3，5 发送原图。",
)

try:
    get_driver()
except ValueError:
    pass
else:
    from . import handlers as handlers
