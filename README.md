# KanamiBot

基于 NoneBot2 + NapCatQQ / OneBot V11 的 QQ Bot。

## 启动

首次运行：

```powershell
git submodule update --init --recursive
$env:UV_CACHE_DIR=".uv-cache"; uv sync
Copy-Item .env.example .env
```

全新克隆建议直接使用：

```bash
git clone --recurse-submodules https://github.com/DAntyNoel/KanamiBot.git
cd KanamiBot
git submodule update --init --recursive
uv sync
uv run erbs-assets download --directory files/erbs-assets
uv run erbs-assets check --directory files/erbs-assets
```

ERBS 图片资源不会在 Bot 运行时下载，也不会提交到 Git。更新游戏版本资源时执行
`uv run erbs-assets update --directory files/erbs-assets`。

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

## 布局

- `bot.py`：NoneBot 入口。
- `src/kanamibot/plugins/`：Bot 插件。
- `src/kanamibot/plugins/ERBS-plugin/`：固定版本的独立 ERBS 数据与渲染库 submodule。
- `src/kanamibot/plugins/er_dak/`：NoneBot2 / OneBot v11 薄适配层。
- `files/napcat_config/`：NapCat 配置模板。
- `files/napcat_runtime/`：NapCat 运行时目录，启动后生成。
- `logs/`：运行日志。
- `vendor/`：NapCat 安装与辅助脚本。
