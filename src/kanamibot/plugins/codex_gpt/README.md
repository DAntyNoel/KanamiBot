# Codex GPT 插件

从老仓库 `D:\DAntyNoel\Kanami-NB\plugins\codex_gpt` 迁移而来，面向 NoneBot2 / NapCatQQ / OneBot v11，提供 OpenAI-compatible 对话、视觉输入和图片生成/编辑能力。

## 命令

- `#gpt <问题>`：带上下文对话
- `#gpt <问题>` + 图片：按视觉输入对话
- `#gpt` + 图片：默认描述图片
- `#gpt one <问题>`：单次提问，不写入上下文
- `#gpt clear`：清空当前会话
- `#gpt forget`：忘记上一轮对话
- `#gpt status`：查看当前会话状态
- `#gpt model`：查看当前模型和模型命令
- `#gpt model list [关键词]`：从 `/v1/models` 拉取可用模型
- `#gpt model use <编号|模型名>`：superuser 切换当前会话模型
- `#gpt model reset`：superuser 恢复默认模型
- `#gpt models`：拉取可用模型列表
- `#gpt system [设定]`：查看或设置当前会话 system prompt
- `#image <描述>`：生成图片
- `#image <描述>` + 图片：基于图片编辑/创作
- `#image` + 图片：默认基于图片内容创作

别名：`#codex`、`#chat`、`#img`。

## 配置

推荐写在项目根目录 `.env`：

```env
CODEX_GPT_API_KEY=your-api-key
CODEX_GPT_BASE_URL=https://cliproxyapi-dantynoel.onrender.com/v1
CODEX_GPT_MODEL=gpt-5.5
CODEX_GPT_IMAGE_MODEL=gpt-image-2
```

兼容旧插件目录下的 `src/kanamibot/plugins/codex_gpt/.env` 和 `apikey.env`，但这些文件只应作为本地私有配置，不要提交。

如果未配置 API Key，插件仍会正常加载，调用 `#gpt` 或 `#image` 时会提示补充配置，不会阻塞 Bot 启动。

## 可选环境变量

- `CODEX_GPT_SYSTEM_PROMPT`：默认 system prompt
- `CODEX_GPT_TEMPERATURE`：采样温度；不设置则不向接口传该参数
- `CODEX_GPT_MAX_HISTORY_MESSAGES`：最多保留历史消息数，默认 `24`
- `CODEX_GPT_MAX_HISTORY_CHARS`：最多保留历史字符数，默认 `16000`
- `CODEX_GPT_TIMEOUT`：对话请求超时秒数，默认 `120`
- `CODEX_GPT_IMAGE_TIMEOUT`：图片生成/编辑请求超时秒数，默认 `300`
- `CODEX_GPT_STREAM`：是否使用流式请求，默认 `true`
- `CODEX_GPT_SESSION_SCOPE`：群聊会话隔离方式，`user` 为每个群成员独立上下文，`group` 为全群共享上下文
- `CODEX_GPT_AUTH_SCHEME`：鉴权头格式，默认 `bearer`；代理要求原样传入 `Authorization` 时可设为 `raw`
- `CODEX_GPT_IMAGE_SIZE`：图片生成/编辑尺寸；不设置则不向图片接口传 `size`
- `CODEX_GPT_DEBUG`：是否输出本地 debug 日志，默认 `0`
- `CODEX_GPT_DEBUG_LOG`：debug 日志路径，默认 `data/codex_gpt/debug.log`

会话数据保存在 `data/codex_gpt/sessions.json`。`data/` 已加入 `.gitignore`，不会提交群聊上下文。

## 群聊概率自动回复

默认关闭。开启后会注册后台事件预处理器，只处理群聊普通消息，不污染手动 `#gpt` 上下文。

- `CODEX_GPT_ACTIVE_REPLY`: 是否开启群聊概率自动回复，默认 `false`
- `CODEX_GPT_ACTIVE_REPLY_PROBABILITY`: 每条可触发群消息的回复概率，默认 `0.03`
- `CODEX_GPT_ACTIVE_REPLY_AT_PROBABILITY`: 群聊直接 @ bot 时的回复概率，默认 `0.95`
- `CODEX_GPT_ACTIVE_REPLY_GROUPS`: 群白名单，逗号分隔；为空时允许所有群
- `CODEX_GPT_ACTIVE_REPLY_RATE_WINDOW_MINUTES`: 单群回复限额窗口分钟数，默认 `5`
- `CODEX_GPT_ACTIVE_REPLY_RATE_LIMIT`: 单群在窗口内最多自动回复条数，默认 `100`
- `CODEX_GPT_ACTIVE_REPLY_HISTORY_MESSAGES`: 从 `data/daily_report/chat_logs.db` 读取的最近群聊历史条数，默认 `80`
- `CODEX_GPT_ACTIVE_REPLY_MEMORY_MESSAGES`: 构造群聊上下文时保留的最近消息数，默认 `20`
- `CODEX_GPT_ACTIVE_REPLY_MAX_PROMPT_CHARS`: 群聊上下文 prompt 最大字符数，默认 `6000`

未配置 API Key 时，即使开启了自动回复环境变量，也不会注册主动回复监听。
