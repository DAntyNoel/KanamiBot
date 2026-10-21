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

ERBS 图片使用 Skia 原生绘制，复用本地资源，并按需缓存查询结果引用的 DAK.GG 图片。
资源缓存不会提交到 Git。也可使用 `uv run erbs-assets update --directory files/erbs-assets`
预先更新资源。

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

## 永恒轮回查询

`/er 帮助` 查看全部命令，`#ER 昵称` 保留玩家概览快捷查询。
`/er 雷达 [昵称]` 查看 ERCraft 六轴评分，原有战绩、段位、统计、队友、多查和对比继续可用。
新增命令同时支持 `/er 查询玩家 ...` 和 `/查询玩家 ...` 两种写法：

- `/查询玩家 昵称 [排位/普通/钴协议/全部]`
- `/查询角色 席琳 [排位/钴协议] [灭钻/星陨/无暇等段位]`
- `/查询角色装备 席琳 [排位/钴协议] [段位]`
- `/角色数据 [段位]`
- `/段位统计 [亚1/国服/全球等服务器]`
- `/永恒多少分 [服务器]`
- `/查询装备 名称或ID或拼音`

含空格的昵称请用双引号括起。六轴评分缺少必要数据时会保留缺失原因。
查询由新框架 KanamiBot 的 `er_dak` 模块开关和冷却控制，默认绘图无需浏览器。

## 卡拉彼丘战绩图片

战绩插件默认启用，加载时不主动请求战绩 API。每次查询前检查 `/health`，
服务不可用时自动暂停该插件全部查询功能，恢复后自动可用。
`#卡丘战绩 玩家名` 默认显示玩家概览，追加 `对局战绩` 可切换界面；
`#卡丘赛季列表` 查看实时赛季，`#卡丘赛季 玩家名` 查询最近已结束主赛季，
`#卡丘名片 玩家名` 查看名片。
图片包含游戏头像与切换命令提示。
可以先独立运行本地脚本，输入昵称并保存 PNG：

```powershell
uv sync
uv run python -m playwright install chromium
uv run python scripts/query_strinova.py
# 也可直接指定昵称；本机已安装 Chrome 时可省略 Chromium 下载。
uv run python scripts/query_strinova.py "咕谷酱" --browser-channel chrome
```

脚本默认使用 `http://10.38.20.220:19080`，图片保存到忽略的 `data/strinova/`。
支持玩家资料、状态、生涯、历史赛季、近期战绩和单局详情。
命令、API 参数、浏览器配置见 [插件说明](src/kanamibot/plugins/strinova/README.md)。
