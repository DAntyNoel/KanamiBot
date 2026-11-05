"""Group image classification; the CLI also works without starting NoneBot."""

from nonebot import get_driver
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="Image Classifier",
    description="自动分类群聊及私聊图片，临时保留48小时，23点私聊超管预览。",
    usage="超管私聊：images/所有图 查看数量；图ab 看缩略图；图ab 3 或 aabb 3，5 发原图。",
)

try:
    get_driver()
except ValueError:
    pass
else:
    from . import handlers as handlers
