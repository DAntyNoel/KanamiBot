# KanamiBot

基于 NoneBot2 + NapCatQQ / OneBot V11 的 QQ Bot。

## 启动

首次运行：

```powershell
$env:UV_CACHE_DIR=".uv-cache"; uv sync
Copy-Item .env.example .env
.\vendor\install_napcat_windows.ps1
```

启动：

```powershell
.\start.ps1
```

或使用 CMD：

```cmd
start.cmd
```

需要调整端口、令牌等本地配置时，修改 `.env`。

## 布局

- `bot.py`：NoneBot 入口。
- `src/kanamibot/plugins/`：Bot 插件。
- `files/napcat_config/`：NapCat 配置模板。
- `files/napcat_runtime/`：NapCat 运行时目录，启动后生成。
- `logs/`：运行日志。
- `vendor/`：NapCat 安装与辅助脚本。
