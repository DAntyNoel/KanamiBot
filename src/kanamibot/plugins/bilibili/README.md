# Bilibili 插件迁移说明

本插件从老仓库 `D:\DAntyNoel\Kanami-NB` 的 `plugins/bilibili` 迁移而来，用于 B 站动态订阅、动态推送、直播推送和二维码登录。

## 仓库来源

- 新仓库：`https://github.com/DAntyNoel/KanamiBot.git`
- 老仓库：`https://github.com/DAntyNoel/Kanami-NB.git`

## 原始插件内容

- `__init__.py`：登录、订阅/取关/列表、动态定时推送、手动更新、查看动态。
- `dynamic.py`：动态 API 拉取、原始动态清洗、消息渲染、分页查询。
- `live.py`：旧直播查询/渲染辅助，原主流程未实际启用。
- `cred.py`：二维码登录、cookie 保存/读取/校验。
- `test.ipynb`：调试草稿，不迁移。
- `__pycache__/`：生成物，不迁移。

## 兼容命令入口

- `bili_login`：超管触发 B 站二维码登录。
- `关注 <UID|UP主名>` / `add sub <UID|UP主名>`：群管理员、群主、超管添加本群订阅。
- `取关 <UID|UP主名>` / `del sub <UID|UP主名>`：群管理员、群主、超管取消本群订阅。
- `关注列表` / `sub list`：查看本群订阅列表。
- `更新动态`：手动触发一次动态检查。
- `查看动态 <UID|订阅名> [序号]` / `dynamic <UID|订阅名> [序号]` / `view dynamic <UID|订阅名> [序号]`：查看指定 UP 的第 N 条动态。

旧代码里注释掉的 `三连/一键三连` 没有恢复。

## 新结构

- `commands.py`：兼容命令注册。
- `jobs.py`：动态和直播定时轮询。
- `store.py`：订阅配置读写，沿用旧 schema：`uid -> name/groups/live_status/dynamic`。
- `credential.py`：二维码登录和 cookie 管理。
- `dynamic.py`：动态拉取和分页查询。
- `dynamic_parser.py`：动态消息渲染，按动态类型拆分。
- `live.py`：批量直播状态查询和开播/下播消息渲染。

## 运行数据

- 订阅配置：`data/plugin_configs/bilibili.json`
- 登录凭证：`data/bilibili/credential.json`

`data/` 已加入 `.gitignore`，不会提交 cookie 或群订阅数据。

## 环境变量

- `BILIBILI_LOGIN_COOLDOWN=3600`
- `BILIBILI_USE_FORWARD=false`，兼容旧 `BILIBILI__USE_FORWARD`
- `BILIBILI_DYNAMIC_INTERVAL_MINUTES=3`
- `BILIBILI_LIVE_INTERVAL_MINUTES=5`
- `BILIBILI_LIVE_BATCH_SIZE=50`
- `BILIBILI_REQUEST_DELAY_SECONDS=1`
- `BILIBILI_MANUAL_MAX_PAGES=5`

## 轮询策略

动态推送仍使用登录态凭证，通过 `bilibili-api-python` 拉取订阅 UP 的新动态。首次启动只记录动态基线，不推送历史动态。

直播推送使用无 cookie 的批量直播状态接口：

`POST https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids`

策略：

- 默认每 5 分钟轮询一次，任务带 0-45 秒随机抖动。
- 每批最多 50 个 UID，不并发请求。
- 批次和群消息之间默认间隔 1 秒。
- 首次启动只记录直播基线，不推送历史开播状态。
- 只在未开播到开播、开播到下播时推送。
- 遇到 412、429、5xx 或连续失败时指数退避，最高暂停 60 分钟。

该策略只能降低风控风险，不能保证第三方接口永不触发限制。
