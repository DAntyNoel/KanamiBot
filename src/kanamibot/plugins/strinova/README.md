# 卡拉彼丘战绩

通过真实 API 查询并生成 HTML/CSS 战绩图片，信息层次参考 [弦外](https://klbq.122999.xyz/)，导航及栏目标题参考游戏官方个人信息界面。采用 840px 紧凑画布、短导航栏、深色栏目标题和红色强调，主要正文为 13–15px，使用地图背景及完整缩放的角色立绘。渲染不依赖参考网站，也不会通过参考站转发查询。Bot 命令默认启用，由健康检查控制是否接受查询。

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
uv run python scripts/query_strinova.py "咕谷酱" 对局战绩 -o data/strinova/standings.png
uv run python scripts/query_strinova.py "丹提诺艾丨3601" --html-output data/strinova/overview.html

# 其余视图与过滤参数。
uv run python scripts/query_strinova.py --player-id 3586568 --view profile
uv run python scripts/query_strinova.py "咕谷酱" --view status
uv run python scripts/query_strinova.py "咕谷酱" --view career --season-id 0 --season-type 1
uv run python scripts/query_strinova.py "咕谷酱" --view history --season-id 25
uv run python scripts/query_strinova.py "咕谷酱" --view standings --index 1 --game-mode 0 --room-mode-type 0 --play-mode 0 --limit 10
uv run python scripts/query_strinova.py "咕谷酱" 对局战绩 第2页 -o data/strinova/page-2.png
uv run python scripts/query_strinova.py "咕谷酱" 对局战绩 枪王 -o data/strinova/gun-king.png
uv run python scripts/query_strinova.py "咕谷酱" --mode 生化 -o data/strinova/infection.png
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
| `STRINOVA_AVATAR_URL_TEMPLATE` | 空，使用公开资源镜像 | 本地头像不存在时的下载地址，使用 `{icon}` 替换头像编号 |
| `KANAMIBOT_STRINOVA_ENABLED` | `true` | 是否注册 Bot 命令，不影响本地脚本 |

CLI 也支持 `--api-url`、`--timeout`、`--browser-channel`、`--browser-executable`。
API 请求绕过环境 HTTP 代理，确保访问提供的内网地址；查询和图片生成合计超时为 2 分钟。

## Bot 命令

重启 Bot 后默认注册以下命令；如需手动关闭，可设置 `KANAMIBOT_STRINOVA_ENABLED=false`。
已有 `.env` 中显式设置的 `false` 会保留关闭行为。
无需改动旧仓库或已运行的 NapCat。群模块名为 `strinova`，支持 `enable strinova` / `disable strinova`。

| 命令 | 图片视图 |
| --- | --- |
| `#卡丘战绩 昵称` / `#卡丘 昵称` / `#KQ 昵称` | 玩家概览（默认） |
| `#卡丘战绩 昵称 对局战绩` | 对局列表 |
| `#卡丘战绩 昵称 引航者资料` | 玩家资料 |
| `#卡丘战绩 昵称 生涯数据` | 生涯数据 |
| `#卡丘战绩 昵称 玩家概览` | 返回玩家概览 |
| `#卡丘资料 昵称` | 玩家资料 |
| `#卡丘状态 昵称` | 当前状态快照 |
| `#卡丘生涯 昵称 --season-id 0 --season-type 1` | 生涯及指定赛季统计 |
| `#卡丘赛季 昵称 --season-id 25` | 历史赛季 |
| `#卡丘战绩 昵称 对局战绩 --index 1 --limit 8` | 对局列表（分页） |
| `#卡丘战绩 昵称 2` | 概览及最近战绩第 2 页 |
| `#卡丘战绩 昵称 排位 2` | 概览及排位爆破战绩第 2 页 |
| `#卡丘战绩 昵称 排位` | 概览及排位爆破战绩第 1 页 |
| `#卡丘战绩 昵称 对局战绩 2` | 对局列表第 2 页 |
| `#卡丘战绩 昵称 对局战绩 排位` | 只看排位爆破战绩 |
| `#卡丘战绩 昵称 枪王` | 概览及枪王乱斗战绩 |
| `#卡丘战绩 昵称 对局战绩 生化` | 只看晶源感染战绩 |
| `#卡丘对局 昵称 --room-id 房间ID` | 单局双方玩家统计 |

Bot 和 CLI 共用查询参数，也可用 `--view` 指定视图。Bot 同时最多执行两次查询。
最近战绩可在玩家概览或对局战绩命令末尾追加完整模式名或唯一前缀，也可用 `--mode 模式名`。
图片默认每页展示 8 场；有后续记录时，图片底部会给出保留当前模式筛选的下一页命令。
页码可直接写为末尾数字，例如 `#卡丘战绩 昵称 排位 2`。原有 `第2页`、`第3页`
及 `--page 2` / `--index 2` 仍可使用。页码按图片实际展示的场次连续翻页，
`--limit` 调整每页场次时，翻页命令会保留相同的条数。
例如「一般」匹配一般爆破，「枪王」匹配枪王乱斗，「生化」是晶源感染的别名；
「排位」固定匹配排位爆破。前缀对应多个模式时会提示补全；原有 `--game-mode` 编号仍可使用。
枪王乱斗等无双队比分的对局卡片保留胜负和个人数据，不展示原始多项 `scores`。
Bot 查询超过 2 分钟会取消并发送超时提示。查询成功、失败、超时或任务被取消时，
都会撤回「正在查询」消息；撤回失败只记录日志，不影响返回结果或释放查询名额。
玩家名后可以追加「玩家概览」「引航者资料」「当前状态」「生涯数据」「历史赛季」
「对局战绩」或「单局详情」切换图片界面。历史赛季及单局详情仍需对应的赛季、房间参数。
图片提示栏统一显示「#卡丘战绩 玩家名 玩家概览/引航者资料/生涯数据/对局战绩 可切换展示页」。昵称含空格或本身以界面名称结尾时，
请将完整昵称放在引号内。中文界面名称与 `--view` 同时填写时必须一致。

## 本地资源与字体

资源根目录固定为 `src/kanamibot/plugins/strinova/resources/`，缓存为同一插件下的 `cache/`。
原始资源包、复制的字体、下载头像和验收图片均由 `.gitignore` 忽略，不进入公开仓库。
模板和 CSS 位于插件内的 `templates/`，作为源码提交。其他机器需另行复制资源目录。

当前已将用户提供的 `data.7z` 解压到该目录，直接包含 `images/`、`mappings/`、`tables/`、
`snapshots/` 和 `fonts/`。将已有资源目录复制到插件下即可，不运行资源包中的脚本。
从用户提供的游戏字体目录复制了 `Game-Semibold.ttf`（方正兰亭黑 Pro Global Semibold）
及 `orbitron-*.otf`；界面中文使用前者，数值使用 `orbitron-medium.otf`。
运行时仅从插件目录加载字体，不依赖原字体目录。

通过 `mappings/*.json` 中已验证的记录识别角色、段位、地图、模式和在线状态。
概览加入收藏数量、常用角色及阵营；对局卡片加入地图图景与角色图标。
玩家概览和引航者资料的「段位与资料」同时展示排位、挑战赛的当前段位及各自积分。
排位徽章和挑战赛奖杯均使用已验证映射中的 `IconDivision` 与 `IconDivisionLevel`
在本地透明合成，补齐小段位数字；未定级、顶级或缺少数字素材时保留原图。
挑战赛名称来自 `ranks.json` 的 `leisure_rank`，资源包缺失时使用已核对的内置名称。
挑战赛数据优先读取公开资料的 `leisure_rank` / `leisure_score`，字段缺失时读取生涯的
`cur_leisure_rank` / `cur_leisure_score`，不使用历史最高记录代替当前段位；缺失值显示 `—`。
合成结果有内存缓存，渲染阶段不请求远程图片，不向资源目录写入生成文件。
右上角立绘按公开资料的 `freq_roles` 列表第一个角色显示，并标明「常用角色」。
公开资料缺少常用角色时读取生涯资料的同名列表，不使用 `role_module` 的模型展示字段。
常用角色为空或对应立绘资源缺失时省略立绘，不默认展示其他角色。
立绘优先读取 `resources/images/wiki/portraits/{角色ID}.png` 中的 Wiki 高清默认服装原图，
避免游戏 `CareerIcon` 资源自带的虚化；缺少 Wiki 原图时仍使用原游戏立绘。
角色头像和对局角色图标继续使用原游戏资源。原图来源链接记录在插件的
`wiki_portraits.json` 中，可运行以下脚本补齐本地资源：

```powershell
.\.venv\Scripts\python.exe scripts/download_strinova_portraits.py
```

脚本优先复用相邻仓库 `local-server/files/WIKI/` 中已下载的原图，也可用
`--wiki-cache "已有的 WIKI 目录"` 指定位置；缺失图片才下载，并验证 PNG 格式和尺寸。
图片保留原始字节、透明通道及分辨率，不进入公开仓库，查询和截图时不请求 Wiki。
「引航者」「超弦体」采用游戏用语；猫形标记及「喵」文案是社区风格装饰，
不把玩家均为猫娘作为官方设定。所有第三方游戏资源和字体的权利归原权利人所有。

缺少资源包时仍可查询：地图和状态保留编号，图片使用系统字体，装饰图片自动省略。
资源路径只能指向该目录内部；只使用已验证的名称映射，未知条目保留编号。
HTML 内嵌图片与字体，截图阶段无需访问网络。

## 玩家头像

头像按玩家 API 返回的 `icon` 编号优先读取本地资源包，不需要网络下载。
本地缺少或无法解码时，默认使用公开的
[Celest151/StrinovaData](https://github.com/Celest151/StrinovaData/tree/main/PaperMan/UI/Atlas/DynamicResource/IdCard/Appearance)
游戏资源镜像（经 jsDelivr CDN 获取），不依赖参考网站的验证接口。
下载图片缓存到忽略的 `src/kanamibot/plugins/strinova/cache/avatars/`，重复查询直接使用缓存。
可通过 `STRINOVA_AVATAR_URL_TEMPLATE` 换成自己的图片服务，例如
`https://your-assets.example/avatars/{icon}.png`。

下载采用 5 秒超时及 2 MiB 大小限制，验证 PNG、JPEG 或 WebP 后以 data URI 内嵌到 HTML。
头像资源暂不可用时保留昵称占位并给出提示，不影响其他查询数据。
图片和缓存均不提交到仓库；第三方游戏资源的权利归原权利人所有。

## 服务健康检查

Bot 的全部战绩命令（含帮助入口）以及本地脚本在查询前请求 API 根地址的 `/health`。
每个后续数据请求也会先复查健康状态。单次检查超时为 5 秒，若 API 配置超时更短，则采用更短值。
首次立即检查，失败后依次等待 5、10、20、40 秒再试，最多检查 5 次；任意一次成功即继续查询。
等待和请求耗时合计最多约 100 秒，控制在 2 分钟内；只有 5 次全部失败才判定服务不可用。
只有 HTTP 请求成功、响应为 JSON 对象且 `ok=true` 才允许查询；响应中的 `hooked`、
`lua_ready`、`game_tick_seen` 如明确为 `false`，也视为游戏桥接不可用。

健康检查连续 5 次失败时暂停本次查询，不再请求玩家接口或生成图片，提示服务暂不可用。
查询过程中复查也使用相同重试逻辑，连续 5 次失败才终止查询，不输出部分结果。
复查及重试仍受整次查询已有的 2 分钟超时限制；提前触及总时限时按查询超时取消。
下一条命令会重新检查，正常后自动恢复。
该状态不会写入 `.env` 或群模块配置，不影响其他 Bot 插件；无需额外后台监控或重启。

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
玩家概览顶部依次显示对局数、胜率、胜场、MVP数、场均击杀、场均伤害、排位爆破KD、头部击败率。
后四项取自公开生涯的 `battle_infos`：场均值以各模式总对局数为分母；
排位爆破 KD 直接读取排位爆破模式的 `k_d`；头部击败率为头部击败数除以总击败数。
相关计数缺失或分母为零时显示 `—`，不把个人资料中的 `avr_kill=0` 当作可靠的场均击杀。
历史赛季失败不会把其他赛季的数据填进去。空列表和 API 失败分别显示。
缺失统计显示 `—`；胜负通过 `my_team` 与 `winner_team` 判断。
近期对局和单局摘要的 API 比分数组按队伍 1、队伍 2 排列，图片根据 `my_team` 调整为
「我方 : 对方」，图片不额外显示该顺序文字；队伍字段缺失或不是两队时保留原始数组顺序并注明，不猜测。
房间 ID 始终保留为整数字符串，避免 JavaScript 浮点精度损失。

当前原始 API 不带前端使用的地图、模式和在线状态字典，使用本地资源包的已验证映射。
资源包缺失或条目未知时保留编号；角色与排位段位还有已核对的内置中文名称作为回退。
每张图标明查询时间、数据来源差异和缓存提示；查询时间不等于游戏数据更新时间。
昵称等外部文本经过 HTML 转义，截图阶段禁用 JavaScript 并阻止网络访问。

查询及渲染实现位于 `src/kanamibot/strinova/`；插件内的样式及模板分别为
`src/kanamibot/plugins/strinova/templates/report.css` 和 `report.html`。

## 本地验证

```powershell
uv run python -m unittest discover -s test -p "test_strinova*.py"
uv run ruff check src/kanamibot/strinova src/kanamibot/plugins/strinova scripts/query_strinova.py test/test_strinova.py
```
