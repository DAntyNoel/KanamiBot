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

命令头后必须有空格（支持半角和全角空格）：

- `#永恒轮回战绩 昵称` 或 `#BS战绩 昵称`：玩家资料与近期战绩，可追加排位/普通/钴协议/全部。
- `#BS评价 昵称`：读取 ERCraft 网站本赛季全部评价，在本地生成六维总结卡；直接显示接口返回的 E–SSS 等级、百分位（P）和各轴有效场数。需显式提供玩家昵称；也支持 `#BS 评价 昵称`。
- `#永恒轮回角色 席琳` 或 `#BS角色 席琳`：角色详情，可追加排位/钴协议及灭钻/星陨等段位。
- `#BS 名称`：优先匹配角色名称、英文名、ID、拼音或拼音首字母，其余名称查询玩家。

例如 `#BS 席琳` 查询角色，`#BS B站丨咕咕禽OC` 查询玩家。同名玩家用 `#BS战绩 席琳`
明确查询。自动分流只做精确名称和拼音匹配；角色的模糊查询请使用 `#BS角色`。
`#BS席琳`、`#BS角色席琳`、`#BS评价玩家名` 等缺少空格的写法不触发查询。

`#BS评价` 已暂时停用本地取对局、补样及六维评分流程，改为通过 ERCraft 的玩家 summary
和 `player-analysis/roles?scope=overall` 接口读取身份、赛季、RP 与六维评价。
身份解析和报告准备期间会持续等待，显示网站返回的排队次序、前方人数与预计等待时间；
只有接口或网站任务明确报错才退出。返回的评价直接显示，不再做本地样本数、等级、百分位或
身份/赛季复核，缺失维度显示“—”。不回退到本地评分。数据范围和时效取决于网站发布的报告，
不会在本地拼接普通匹配或重新计算等级、百分位；旧本地流程仍保留在插件的 `summary` CLI/API 中。
图片称号按网站前端 `play-card-percentile.v4` 的阈值、判定顺序和并列规则动态选择，
使用已确定的 [13 个中文风格称号](src/kanamibot/plugins/ERBS-plugin/docs/README.zh-CN.md#插画总结图)，
不再固定显示“光与盐”。

评价卡仍在本地绘制为 PNG，每次按 ERCraft 报告中的角色场次加权随机选择已有卡面。
DAK 角色元数据只用于素材名称和稀有度（英雄权重 10、其余 1），不参与评分；
元数据暂不可用时仍可使用已知 ERCraft 卡面出图。

`#BS 帮助` 查看全部命令；原有功能统一放在 `#BS` 后：

- `#BS 雷达 [昵称]`、`#BS 战绩 [昵称] [1-10]`
- `#BS 段位/统计/近期/实验体/皮肤/队友 [昵称]`
- `#BS 多查 昵称1 昵称2`、`#BS 对比 昵称1 昵称2`
- `#BS 查询角色装备 席琳 [排位/钴协议] [段位]`
- `#BS 角色数据 [段位]`、`#BS 段位统计 [服务器]`、`#BS 永恒多少分 [服务器]`
- `#BS 查询装备 名称或ID或拼音`

含空格的昵称请用双引号括起。六轴评分缺少必要数据时会保留缺失原因。
查询由新框架 KanamiBot 的 `er_dak` 模块开关和冷却控制，默认绘图无需浏览器。
本地 `erbs` CLI 的命令与调用方式独立于 Bot 命令头。
玩家与角色查询采用 DAK.GG 桌面宽屏布局：玩家为统计和战绩双栏，角色省略全角色列表，
保留横向指标、技能与战术技能并排、潜能组合及装备表格；Bot 与 CLI 共用本地 Skia 绘图。

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
