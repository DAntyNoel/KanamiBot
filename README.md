# KanamiBot

基于 NoneBot2 + NapCatQQ / OneBot V11 的 QQ Bot。

## 启动

首次运行：

```powershell
$env:UV_CACHE_DIR=".uv-cache"; uv sync
Copy-Item .env.example .env
```

启动：

```powershell
.\start.ps1
```

启动器会分别检测 NapCat 和 NoneBot：已经运行的服务保持不动，缺少的服务会在当前前台终端中启动。关闭该终端会停止本次启动器新建的所有进程，但不会影响启动前已经存在的服务。

或使用 CMD：

```cmd
start.cmd
```

已有 `.venv` 时会跳过重复的依赖同步。修改 `pyproject.toml` 或 `uv.lock` 后，可执行：

```cmd
start.cmd -SyncDependencies
```

如只需补启动 NoneBot、明确不启动 NapCat，可执行 `start.cmd --nonebot-only`。

需要调整端口、令牌等本地配置时，修改 `.env`。

## 群图片自动分类

新增 [image_classifier 插件](src/kanamibot/plugins/image_classifier/README.md)：在所有群静默处理图片，
使用可独立启停的 CPU ONNX 模型保留二次元细分类、二次元/三次元对照和独立 NSFW 结果，保存原图及 JSON，
北京时间每天 12:00 清理保存满三天的记录。部署前需按插件说明准备模型文件。

本地拖放图片测试：运行 `uv run kanami-image-web`，后台就绪后会自动打开网页，
逐张展示所有分类分数，无需连接 QQ。

## 布局

- `bot.py`：NoneBot 入口。
- `src/kanamibot/plugins/`：Bot 插件。
- `files/napcat_config/`：NapCat 配置模板。
- `files/napcat_runtime/`：NapCat 运行时目录，启动后生成。
- `logs/`：运行日志。
- `vendor/`：NapCat 安装与辅助脚本。
