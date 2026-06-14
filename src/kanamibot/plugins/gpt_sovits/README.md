# GPT-SoVITS TTS

插件已迁移，但默认禁用。设置 `KANAMIBOT_TTS_ENABLED=1` 后会注册 `#tts` 命令。

常用配置：

- `GPT_SOVITS_URL`：后端地址，默认 `http://127.0.0.1`
- `GPT_SOVITS_PORT`：后端端口，默认 `9550`
- `GPT_SOVITS_PROJECT_ROOT`：GPT-SoVITS 项目根目录
- `GPT_SOVITS_PYTHON_EXEC`：可选，GPT-SoVITS runtime Python 路径
- `GPT_SOVITS_GPT_MODEL_PATH` / `GPT_SOVITS_SOVITS_MODEL_PATH`：模型路径

启用后用法：`#tts [-e 自然|激动|沮丧] 文本`
