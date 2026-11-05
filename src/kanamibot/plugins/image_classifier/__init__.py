"""Group image classification; the CLI also works without starting NoneBot."""

from nonebot import get_driver
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="Image Classifier",
    description="自动使用 CPU ONNX 模型分类所有群图片，临时保留三天。",
    usage="静默处理群图片；模型准备和配置见本插件 README。",
)

try:
    get_driver()
except ValueError:
    pass
else:
    from . import handlers as handlers
