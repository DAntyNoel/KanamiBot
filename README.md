# KanamiBot

基于 NoneBot2 + NapCatQQ / OneBot V11 的 QQ Bot。

## 启动

首次运行：

```powershell
git submodule update --init --recursive
$env:UV_CACHE_DIR=".uv-cache"; uv sync
Copy-Item .env.example .env
uv run playwright install chromium
uv run python scripts/migrate_skland.py upgrade
uv run python scripts/migrate_skland.py check
```

全新克隆建议直接使用：

```bash
git clone --recurse-submodules https://github.com/DAntyNoel/KanamiBot.git
cd KanamiBot
git submodule update --init --recursive
uv sync
uv run playwright install chromium
uv run python scripts/migrate_skland.py upgrade
uv run python scripts/migrate_skland.py check
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

森空岛启动时会在后台更新游戏数据；启用图片资源检查时，图片也在后台下载，均不阻塞机器人启动。数据准备期间，抽卡记录、抽卡导入和干员查询会提示稍后重试，其他命令正常使用。更新结果记录在日志中；数据更新失败可发送 `#SK 资源更新` 重试。

停止 NoneBot（包括隐藏窗口中的后台进程）：

```cmd
stop-nonebot.cmd
```

停止脚本按 `12706` 端口定位并核对本仓库的 Python 和 `bot.py` 进程，停止后确认端口释放，NapCat 保持运行。若端口由其他程序占用，则报错并退出。

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
- `src/kanamibot/plugins/skland/`：森空岛本地适配层，内含固定提交的 `_upstream` submodule。
- `files/napcat_config/`：NapCat 配置模板。
- `files/napcat_runtime/`：NapCat 运行时目录，启动后生成。
- `logs/`：运行日志。
- `vendor/`：NapCat 安装与辅助脚本。

## 森空岛 / 终末地

森空岛通过 `plugins/skland/_upstream` 子模块安装，本地适配层沿用插件目录扫描。
依赖同步与数据库迁移期间先停止本仓库 NoneBot，NapCat 保持运行。
安装、账号绑定、命令、数据备份和任务配置见 [森空岛说明](src/kanamibot/plugins/skland/README.md)。

指令头支持 `#SK`、`#sk`、`#森空岛`，与功能之间用空格分隔。
发送 `#SK 扫码登录`，使用森空岛 App 扫码并回复「确认」；之后可以使用 `#SK ef`、
`#SK 终末地签到`、`#SK 终末地抽卡记录`、`#SK 战争回响`，用 `#SK 帮助` 查看完整功能。
群查询开关为 `/enable skland`、`/disable skland`，不会停止后台账号签到。

## 永恒轮回查询

命令头后必须有空格（支持半角和全角空格）：

- `#永恒轮回战绩 昵称` 或 `#BS战绩 昵称`：玩家资料与近期战绩，默认展示“全部”页签，可追加全部/排位/匹配/钴协议筛选；普通等同匹配。
- `#BS排位 昵称`、`#BS钴协议 昵称`、`#BS匹配 昵称`：直接查询玩家并切换对应页签，也可使用 `#BS 昵称 排位`、`#BS 昵称 钴协议`、`#BS 昵称 匹配`。
- `#BS评价 昵称`：读取 ERCraft 网站本赛季全部评价，在本地生成六维总结卡；直接显示接口返回的 E–SSS 等级、百分位（P）和各轴有效场数。需显式提供玩家昵称；也支持 `#BS 评价 昵称`。
- `#永恒轮回角色 席琳` 或 `#BS角色 席琳`：角色详情，可追加排位/钴协议及灭钻/星陨等段位。
- `#BS 名称`：优先匹配角色名称、英文名、ID、拼音或拼音首字母，其余名称查询玩家，默认展示“全部”页签。

例如 `#BS 席琳` 查询角色，`#BS B站丨咕咕禽OC` 查询玩家。同名玩家用 `#BS战绩 席琳`
明确查询。自动分流只做精确名称和拼音匹配；角色的模糊查询请使用 `#BS角色`。
`#BS席琳`、`#BS角色席琳`、`#BS评价玩家名` 等缺少空格的写法不触发查询。

`#BS评价` 已暂时停用本地取对局、补样及六维评分流程，改为通过 ERCraft 的玩家 summary
和 `player-analysis/roles?scope=overall` 接口读取身份、赛季、RP 与六维评价。
身份解析和报告准备期间会持续等待，每次查询最多发送一条排队消息，包含网站返回的
排队次序、前方人数与预计等待时间；不发送身份解析、资料刷新或报告准备的阶段提醒。
资料刷新遇到等待重试、刷新汇总等中间状态时会继续跟踪同一个任务；上次尝试的错误码不会提前终止等待。
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
玩家卡左侧固定展示所选赛季的排位三排段位、统计、RP 趋势、实验体与队友；模式筛选仅切换右侧近期战绩。

## 卡拉彼丘战绩图片

战绩插件默认启用，加载时不主动请求战绩 API。每次查询前检查 `/health`，
服务不可用时自动暂停该插件全部查询功能，恢复后自动可用。
`#卡丘战绩 玩家名` 默认显示玩家概览，追加 `对局战绩` 可切换界面；
`#卡丘赛季列表` 查看实时赛季，`#卡丘赛季 玩家名` 查询最近已结束主赛季，
`#卡丘名片 玩家名` 查看名片。
图片包含游戏头像与切换命令提示。

插件启用后，每分钟轮询测试服 `Game_TYF/manifest.txt`，独立于战绩 API 健康状态。
首次成功获取仅建立基线；之后版本号或清单内容变化时，在群 `1062499136`
中 @`2774133374` 提醒，包含前后版本号和清单链接。
基线保存在 `data/strinova/tyf_manifest.json`，重启后继续使用；抓取、解析或发送失败
保留旧基线，下分钟重试。正式服 `Game_Release/manifest.txt` 不在本次监测范围。

可以先独立运行本地脚本，输入昵称并保存 PNG：

```powershell
uv sync
uv run python -m playwright install chromium
uv run python scripts/query_strinova.py
# 也可直接指定昵称；本机已安装 Chrome 时可省略 Chromium 下载。
uv run python scripts/query_strinova.py "咕谷酱" --browser-channel chrome
```

脚本优先使用 `http://127.0.0.1:19080`，不可用时回退到 `http://10.38.20.220:19080`；自定义其他 API 地址时仅使用指定地址。图片保存到忽略的 `data/strinova/`。
支持玩家资料、状态、生涯、历史赛季、近期战绩和单局详情。
命令、API 参数、浏览器配置见 [插件说明](src/kanamibot/plugins/strinova/README.md)。
