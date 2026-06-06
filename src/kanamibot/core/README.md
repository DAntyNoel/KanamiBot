# KanamiBot Core

`kanamibot.core` 是从老仓库 `D:\DAntyNoel\Kanami-NB\plugins\core` 迁移来的核心能力层。它不再被视为普通业务插件，而是为其他插件提供权限、群配置、存储、图片、文本、语音和消息构造等共享能力。

NoneBot 命令入口由 `kanamibot.plugins.core` 插件壳注册；普通代码导入 `kanamibot.core` 不会注册 matcher。

## 原始 Core 内容

- `__init__.py`：聚合导出 core API。
- `group_manager.py`：群模块开关、群黑名单和 `ModuleRule`。
- `check_perm.py`：超管、群主、群管理权限级别与检查函数。
- `config_storage.py`：插件 JSON 配置、自动备份和周期重置。
- `media_storage.py`：增强媒体存储、元数据、哈希去重和图库预览图。
- `image_wrappers.py`：面向旧图库插件的兼容封装函数。
- `utils/__init__.py`：聚合导出工具函数。
- `utils/file.py`：群文件上传。
- `utils/image.py`：图片提取、下载、格式判断。
- `utils/message_builder.py`：消息链和合并转发节点构造。
- `utils/text.py`：纯文本提取和 RapidFuzz 模糊匹配。
- `utils/text2image.py`：文本转图片 bytes。
- `utils/vedio.py`：视频转 GIF 工具，保留老拼写。
- `utils/voice.py`：MP3 转 Silk/语音消息段。

新增 `utils/video.py` 作为 `utils/vedio.py` 的正确拼写别名。

## 推荐导入

新代码优先使用：

```python
from kanamibot.core import ModuleRule, ConfigManager, get_first_superuser
from kanamibot.core.image_wrappers import save_image, get_folder_name
from kanamibot.core.utils.image import guess_extension
```

为了兼容后续迁移老插件，`kanamibot.plugins.core` 会映射以下旧式相对导入：

```python
from ..core import ModuleRule
from ..core.group_manager import group_config
from ..core.utils.image import guess_extension
from ..core.image_wrappers import save_image
```

## 兼容命令入口

这些命令由 `src/kanamibot/plugins/core/__init__.py` 加载：

- `enable <module>`：群管理员、群主、超管启用本群模块。
- `disable <module>`：群管理员、群主、超管禁用本群模块。
- `ban user <qq>`：超管拉黑本群用户。
- `list module`：群管理员、群主、超管列出已注册模块状态。

`ModuleRule(module_name)` 的行为保持兼容：

- 超管永远放行。
- 私聊默认放行。
- 群聊会检查 `data/group_manager.json` 中的用户黑名单和模块开关。
- 未配置的模块默认启用。

## 运行文件

Core 运行时会在新仓库内使用这些路径：

- `data/group_manager.json`：群模块状态和黑名单。
- `data/plugin_configs/`：各插件配置和自动备份。
- `data/advanced_media/`：媒体文件、图库配置和元数据。
- `files/fonts/MiSans-Regular.ttf`：文本转图和图库预览图的默认中文字体。

`data/` 已加入 `.gitignore`，不会把运行数据提交到 public 仓库。
