# Memes

使用 [MemeCrafters/meme-generator](https://github.com/MemeCrafters/meme-generator)
提供的 HTTP API 作为表情包生成引擎，当前插件只保留 NoneBot 命令适配层，方便
后续通过独立升级 meme-generator 服务同步上游模板和修复。

## 命令

- `meme <模板key> [文本...]`：生成表情包。图片可来自当前消息、回复消息或合并转发。
- `meme <模板key> -t 文本1 -t 文本2`：显式传入多段文本。
- `meme <模板key> -a key=value --args-json '{"key": "value"}'`：传入模板额外参数。
- `meme_info <模板key>`：查看模板需要的图片/文本数量、关键词和标签。
- `meme_list [关键词]`：搜索模板 key、关键词和标签。

## 部署注意

当前项目依赖 `pillow>=12`，而 `meme-generator<0.2.0` 本地 Python 包依赖
`pillow<11`。为了不破坏现有图片功能，本插件不把 meme-generator 安装进
KanamiBot 进程，而是连接一个独立的 meme-generator API 服务。

默认 API 地址是 `http://127.0.0.1:2233`，可在 `.env` 中修改：

```powershell
MEME_GENERATOR_BASE_URL=http://127.0.0.1:2233
```

在 meme-generator 环境中按上游要求启动服务：

```powershell
meme download
meme run
```

部分模板还依赖系统字体；如果文字渲染不正常，需要按上游文档安装对应字体。
