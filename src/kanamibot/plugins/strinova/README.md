# 卡拉彼丘战绩

通过真实 API 查询并生成 HTML/CSS 战绩图片，浅色背景、绿色强调色、白色圆角卡片及战绩行布局参考 [弦外](https://klbq.122999.xyz/)。渲染不依赖参考网站，也不会通过参考站转发查询。没有部署动作，默认不注册 Bot 命令。

## 独立运行

在仓库根目录安装依赖和浏览器（Python 3.12）：

```powershell
uv sync
uv run python -m playwright install chromium
uv run python scripts/query_strinova.py
```

最后一条命令会提示输入昵称或 ID，直接查询并保存 PNG；不启动 NoneBot、NapCat 或 HTTP 服务。
也可使用 `.\.venv\Scripts\python.exe` 代替 `uv run python`。已安装 Chrome / Edge 时使用
`--browser-channel chrome` / `--browser-channel msedge`，无需下载 Chromium。

```powershell
# 概览：资料、公开生涯统计、最近 8 条战绩。
uv run python scripts/query_strinova.py "咕谷酱" -o data/strinova/overview.png
uv run python scripts/query_strinova.py "丹提诺艾丨3601" --html-output data/strinova/overview.html

# 其余视图与过滤参数。
uv run python scripts/query_strinova.py --player-id 3586568 --view profile
uv run python scripts/query_strinova.py "咕谷酱" --view status
uv run python scripts/query_strinova.py "咕谷酱" --view career --season-id 0 --season-type 1
uv run python scripts/query_strinova.py "咕谷酱" --view history --season-id 25
uv run python scripts/query_strinova.py "咕谷酱" --view standings --index 1 --game-mode 0 --room-mode-type 0 --play-mode 0 --limit 10
uv run python scripts/query_strinova.py "咕谷酱" --view match --room-id 751028091756896271
```

纯数字默认当作玩家 ID；查询纯数字昵称时使用 `--nick 12345`。昵称含空格时建议加引号。
多个匹配结果会提示候选 ID，不会自动选择错误玩家。
默认输出为 `data/strinova/{玩家ID}-{视图}-{时间}.png`，可用 `-o` 指定路径。
`--html-output` 可同时保存自包含 HTML。原始响应、真实昵称、验收图片均不作为测试数据提交。

脚本读取仓库 `.env`（已有进程环境优先），但不会调用 Bot 启动器。可设置：

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `STRINOVA_API_URL` | `http://10.38.20.220:19080` | 真实 API 根地址，不能加 `/v1` |
| `STRINOVA_API_TIMEOUT` | `35` | 单次请求超时秒数，1–120 |
| `STRINOVA_BROWSER_CHANNEL` | 空 | `chrome` 或 `msedge`；空值使用 Playwright Chromium |
| `STRINOVA_BROWSER_EXECUTABLE` | 空 | 明确指定浏览器可执行文件，优先于 channel |
| `KANAMIBOT_STRINOVA_ENABLED` | `false` | 是否注册 Bot 命令，不影响本地脚本 |

CLI 也支持 `--api-url`、`--timeout`、`--browser-channel`、`--browser-executable`。
API 请求绕过环境 HTTP 代理，确保访问提供的内网地址；总查询超时 180 秒。

## Bot 命令（以后启用时）

将 `KANAMIBOT_STRINOVA_ENABLED=true` 写入 `.env` 并在需要部署时自行启动 Bot。
无需改动旧仓库或已运行的 NapCat。群模块名为 `strinova`，支持 `enable strinova` / `disable strinova`。

| 命令 | 图片视图 |
| --- | --- |
| `#卡丘 昵称` / `#KQ 昵称` | 玩家概览 |
| `#卡丘资料 昵称` | 玩家资料 |
| `#卡丘状态 昵称` | 当前状态快照 |
| `#卡丘生涯 昵称 --season-id 0 --season-type 1` | 生涯及指定赛季统计 |
| `#卡丘赛季 昵称 --season-id 25` | 历史赛季 |
| `#卡丘战绩 昵称 --index 1 --limit 8` | 对局列表 |
| `#卡丘对局 昵称 --room-id 房间ID` | 单局双方玩家统计 |

Bot 和 CLI 共用查询参数，也可用 `--view` 指定视图。Bot 同时最多执行两次查询。

## 接口与数据处理

客户端完整覆盖以下 GET 路由：

- `/v1/friends/search?nick=...`、`/v1/friends/search?player_id=...`
- `/v1/players/{player_id}`、`/status`、`/career`
- `/v1/players/{player_id}/career/battle-data?season_id=...&season_type=...`
- `/v1/players/{player_id}/career/history?season_id=...`
- `/v1/players/{player_id}/standings?index=...&game_mode=...&room_mode_type=...&play_mode=...`
- `/v1/players/{player_id}/standings/{room_id}`

检查 HTTP、外层 `ok` 与内层业务 `code`，只接受 `ok=true` 且 `code=0`。
搜索失败直接报错；后续某项不可用时保留其他结果，在图片中说明缺失。
概览只请求资料、状态、生涯和对局列表；生涯视图额外请求赛季统计。赛季统计失败时显示生涯数据并注明来源。
历史赛季失败不会把其他赛季的数据填进去。空列表和 API 失败分别显示。
缺失统计显示 `—`；胜负通过 `my_team` 与 `winner_team` 判断，比分保留原始队伍顺序。
房间 ID 始终保留为整数字符串，避免 JavaScript 浮点精度损失。

当前原始 API 不带前端使用的地图、模式和在线状态字典，这三项保留编号。
角色与排位段位使用已核对的中文名称，未知角色回退到编号。
每张图标明查询时间、数据来源差异和缓存提示；查询时间不等于游戏数据更新时间。
昵称等外部文本经过 HTML 转义，截图阶段禁用 JavaScript 并阻止网络访问。

实现位于 `src/kanamibot/strinova/`；样式及模板分别为 `templates/report.css` 和 `templates/report.html`。

## 本地验证

```powershell
uv run python -m unittest discover -s test -p "test_strinova*.py"
uv run ruff check src/kanamibot/strinova src/kanamibot/plugins/strinova scripts/query_strinova.py test/test_strinova.py
```
