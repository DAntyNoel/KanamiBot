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

## 布局

- `bot.py`：NoneBot 入口。
- `src/kanamibot/plugins/`：Bot 插件。
- `files/napcat_config/`：NapCat 配置模板。
- `files/napcat_runtime/`：NapCat 运行时目录，启动后生成。
- `logs/`：运行日志。
- `vendor/`：NapCat 安装与辅助脚本。
