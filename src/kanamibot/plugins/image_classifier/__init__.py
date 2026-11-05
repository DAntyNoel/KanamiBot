"""Group image classification; the CLI also works without starting NoneBot."""

from nonebot import get_driver
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="Image Classifier",
    description="自动分类群聊及私聊图片，临时保留48小时，23点私聊超管预览。",
    usage="超管私聊：#图片预览 [页码]；#图片保存 编号 [目录名]。配置见插件 README。",
)

try:
    get_driver()
except ValueError:
    pass
else:
    from . import handlers as handlers
