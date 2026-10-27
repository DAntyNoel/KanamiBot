# 森空岛 / 终末地

## 安装与更新

在项目根目录操作。同步依赖前先停止本仓库 NoneBot，完成后按原方式启动。

```powershell
git submodule update --init --recursive
uv sync --locked
uv run playwright install chromium
uv run python scripts/migrate_skland.py upgrade
uv run python scripts/migrate_skland.py check
```

默认读取根目录 `.env`。迁移脚本支持 `--env-file 路径`，仅加载插件和操作数据库，
不启动网络服务、资源更新或定时签到。升级子模块后也需执行迁移与检查。

主仓库固定 `_upstream` 的提交，通过 uv editable 路径依赖安装上游包。
本地适配层沿用现有插件目录扫描，无需修改 `bot.py`。适配代码与上游源码分别维护。

未配置 `RENDER` 时，适配层让 htmlrender 复用 `playwright install` 的标准浏览器目录；
也支持 `PLAYWRIGHT_BROWSERS_PATH` 自定义目录。显式 `RENDER` 配置优先，使用它时自行设置
`provider_config.storage_path` 或浏览器启动参数。

默认数据位置：

- `data/nonebot_plugin_orm/db.sqlite3`：账号、角色与抽卡记录。
- `data/nonebot_plugin_skland/`：游戏数据与签到结果。
- `data/cache/nonebot_plugin_skland/`：图片、快捷指令等缓存。

如果 `.env` 已指定 `LOCALSTORE_DATA_DIR`、`LOCALSTORE_CACHE_DIR`、
`LOCALSTORE_CONFIG_DIR` 或数据库连接，则优先使用这些配置。备份时保留整个数据目录。
账号凭证属于敏感运行数据，不要提交到公开仓库。

## 绑定与查询

指令头支持 `#SK`、`#sk`、`#森空岛`，与功能之间用空格分隔；三个指令头完全等价，
不依赖全局 `COMMAND_START`。例如 `#SK 扫码登录`、`#sk 扫码登录`、`#森空岛 扫码登录`。
原有上游命令仍按项目 `COMMAND_START` 配置兼容。

`#SK 帮助`、`#SK`、`/skland --help` 等森空岛命令帮助入口统一发送三张命令图片，覆盖终末地与账号、
明日方舟与干员筛选、管理与图片交互，只展示可直接使用的命令和用法。

1. 发送 `#SK 扫码登录`，使用森空岛 App 扫码。
   `登录`、`登陆`、`扫码`、`QR`、`扫码登录`、`扫码绑定` 均为同一命令；`QR` 不区分大小写。
2. 核对机器人展示的账号角色列表，按提示回复「确认」完成绑定。
3. 发送 `#SK 森空岛角色` 查看角色序号；用 `#SK 切换终末地角色 2` 设置默认角色。

| 命令 | 用途 |
| --- | --- |
| `#SK ef` | 默认终末地角色卡 |
| `#SK ef -a -s` | 展示全部干员，使用简化背景 |
| `#SK ef -r 2` | 临时查询自己的第 2 个终末地角色 |
| `#SK 终末地签到` | 签到本人全部终末地角色 |
| `#SK 终末地签到详情` | 本人缓存签到结果与失败原因 |
| `#SK 终末地抽卡记录` | 更新并查询抽卡记录 |
| `#SK efgacha -l 3` | 每类别展示前 3 个卡池；支持英文子命令 |
| `#SK 战争回响` | 当前赛季、当前轮换 |
| `#SK 战争回响 -s -1` | 上一赛季 |
| `#SK 角色更新` | 同步本人绑定账号的角色列表 |
| `#SK 森空岛解绑` | 交互选择并确认删除账号、角色及关联抽卡记录 |
| `#SK` / `#SK 帮助` / `#SK --help` | 完整功能，包含明日方舟 |
| `/帮助 森空岛` | KanamiBot 中文用法 |

优先使用扫码绑定。手工 token/cred 绑定只允许私聊，cred 绑定无法同步终末地抽卡记录。
抽卡同步失败而存在缓存时，上游会标明当前展示的是本地缓存。
明日方舟角色卡可用 `#SK 方舟` 查询，也支持 `-r 序号` 或 @用户。

## 权限与后台任务

- `/enable skland`、`/disable skland` 管理本群查询；`/list module` 查看状态。
- 普通群成员受群开关与黑名单限制，私聊及超管沿用 Core 的放行规则。
- 命令帮助、解析提示及保持会话的确认回复也检查权限。
- 群开关不停止账号后台签到。`KANAMIBOT_SKLAND_AUTO_SIGN=false` 全局关闭两款游戏的自动签到，手动签到仍可用。
- `KANAMIBOT_SKLAND_ENABLED=false` 全局停止加载森空岛功能。
- 配置修改后需要重启本仓库 NoneBot。

默认定时签到为明日方舟 00:15、终末地 00:20，以 APScheduler 的时区配置为准；
任务保存结果，不向群广播。每日 09:00 更新游戏及卡池数据，由
`SKLAND__AUTO_UPDATE_RESOURCES` 控制。

## 维护验证

```powershell
uv run pytest test/test_skland.py
uv run ruff check src/kanamibot/plugins/skland --exclude _upstream scripts/migrate_skland.py test/test_skland.py
```

检查主仓库改动时排除 `_upstream`，上游格式与测试规则由其自身仓库管理。
