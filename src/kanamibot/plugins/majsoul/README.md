# Majsoul 雀魂插件

本目录是从老仓库 `D:\DAntyNoel\Kanami-NB\plugins\Majsoul` 迁移来的雀魂查询插件，模块名为 `majsoul`，可用核心命令启停。

## 原始来源

- 原始作者：NekoRabi，老插件 `source.py` 头部标注版本 `0.6.5`，更新时间 `2022/8/28 3:14`。
- 原作者远程仓库：[NekoRabi/Majsoul-QQBot](https://github.com/NekoRabi/Majsoul-QQBot)。
- 数据来源：牌谱屋 / Amae-Koromo API。

## 原始插件内容

- `__init__.py`：NoneBot 命令入口、参数解析、QQ 账号绑定快捷查询、管理员初始化命令、牌画转换命令。原文件底部还有一段已注释的语音合成旧代码，但缺少 `construct_voice`、角色语音资源等依赖，原本并不是活跃入口，本次迁移未启用。
- `source.py`：雀魂查询核心逻辑，包括玩家搜索、三麻/四麻 PT 查询、最近牌谱、玩家详细统计、月报、绑定表、牌谱缓存表、段位格式化和转发消息构造。
- `unicode.py`：将 `123m456p789s123z` 这类麻将牌简写转换为 Unicode 麻将牌字符。

## 兼容命令入口

| 命令 | 兼容别名 | 说明 |
| --- | --- | --- |
| `qhpt <玩家名>` | `雀魂分数`、`雀魂pt` | 查询玩家三麻/四麻 PT。首次查询会写入本地玩家缓存。 |
| `qhpt <玩家名> <3/4/三麻/四麻> [序号]` | 同上 | 指定三麻或四麻；同名玩家较多时可用序号选择。 |
| `qhpaipu <玩家名> [3/4] [数量]` | `雀魂最近对局` | 查询最近牌谱，数量范围 1-10，默认 5。 |
| `qhinfo <玩家名> [3/4] [场况] [类型]` | `雀魂玩家详情` | 查询详细统计。场况支持 `all`、`金`、`金东`、`金南`、`玉`、`玉东`、`玉南`、`王`、`王座`、`王座东`、`王座南`；类型支持 `基本`、`更多`、`立直`、`血统`、`all`。 |
| `qhyb <玩家名> [3/4] [年月]` | `雀魂月报` | 查询月报。年月支持 `2026-06`、`2026 6`、`2026年6月`，不填则查最近一个月。 |
| `qhbind <玩家名>` | `雀魂绑定` | 将当前 QQ 绑定到已缓存的雀魂玩家；绑定前需要先用 `qhpt` 查到该玩家。 |
| `qhmpt` | 无 | 查询当前 QQ 绑定账号的 PT。 |
| `qhmyb [3/4] [年月]` | 无 | 查询当前 QQ 绑定账号的月报。 |
| `qhminfo [3/4] [场况] [类型]` | 无 | 查询当前 QQ 绑定账号的详细统计。 |
| `qhmpaipu [3/4] [数量]` | 无 | 查询当前 QQ 绑定账号的最近牌谱。 |
| `qh <牌型>` | 无 | 牌画转换，例如 `qh 123m405p789s12345z`。 |
| `qhinit` | 无 | 超级用户命令，初始化雀魂 sqlite 表结构。 |

## 本次迁移调整

- 插件目录改为 `src/kanamibot/plugins/majsoul`，入口使用 `kanamibot.core.group_manager.ModuleRule("majsoul")`。
- 数据库路径改为 `data/majsoul/majsoul.sqlite`，由插件自动创建表结构。
- 网络请求保留原始 `ClientSession` 形态，底层改用项目已有的 `httpx`，避免额外引入 `aiohttp` 依赖。
- 移除原入口文件中已注释且缺少资源依赖的语音合成代码，减少误导；若后续要恢复语音，需要单独迁移角色语音资源和 `construct_voice` 逻辑。
- 已通过 `ruff check src\kanamibot\plugins\majsoul`、`python -m compileall` 和 `bot.create_app()` 加载检查。
