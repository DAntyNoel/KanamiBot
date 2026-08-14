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

```cmd
start.cmd
```

启动器使用原生 CMD 命令分别检测 NapCat 和 NoneBot。已经运行的服务保持不动，每个缺少的服务都会在各自独立、可见的前台 CMD 终端中启动。启动 NapCat 前会生成 WebUI 与 OneBot 反向 WebSocket 配置，并将工作目录固定为 `files/napcat_runtime`。关闭某个服务的终端只会停止该服务。

首次启动或修改 `pyproject.toml`、`uv.lock` 后，请先同步依赖：

```cmd
uv sync
```

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
