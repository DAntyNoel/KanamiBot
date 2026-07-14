# Memes

使用 [MemeCrafters/meme-generator-rs](https://github.com/MemeCrafters/meme-generator-rs)
提供的 Python 绑定在 KanamiBot 进程内生成表情包，不再依赖单独运行的 2233 HTTP 服务。

## 命令

- `meme <模板key> [文本...]`：生成表情包。图片可来自当前消息、回复消息或合并转发。
- `meme <模板key> -t 文本1 -t 文本2`：显式传入多段文本。
- `meme <模板key> -a key=value --args-json '{"key": "value"}'`：传入模板额外参数。
- `meme_info <模板key>`：查看模板需要的图片/文本数量、关键词和标签。
- `meme_list [关键词]`：搜索模板 key、关键词和标签。
- `meme_update`：检查并同步表情素材；首次下载约 400 MB。

## 素材目录

`meme-generator>=0.2.3` 使用 Rust 实现，不再与项目的 `pillow>=12` 冲突。模板素材和
字体默认保存在仓库忽略的 `data/memes/resources/`，不会提交到 Git。
如需修改保存位置，可在 `.env` 中设置 `MEME_HOME`。

首次生成时如果发现素材缺失，插件会自动同步一次并重试。也可以提前发送
`meme_update` 完成下载，避免首次生成等待。下载完成后，后续重启会复用现有素材。
