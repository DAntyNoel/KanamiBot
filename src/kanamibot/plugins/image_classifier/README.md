# 群图片自动分类

自动监听全部 OneBot V11 群消息，以 CPU ONNX Runtime 运行启用的模型，保存原图和分类 JSON。
无需命令，群内不发送分类回复。消息命令是否被其他插件拦截、群模块开关或用户黑名单均不影响收集。
本插件不执行撤回、禁言等群管理操作。

## 准备模型

前两个上游仓库目前都没有提供 ONNX 文件。导出工具固定使用以下来源的 safetensors 权重，
导出 float32、opset 17、固定输入 `1×3×224×224` 的 ONNX，并校验 CPU 推理与原模型的数值一致性：

| 模型 | 固定 revision |
| --- | --- |
| [antonioglass/real-or-anime-age](https://huggingface.co/antonioglass/real-or-anime-age) | `bd208a02c3d0a15de26d1c94ce4624b017ea3639` |
| [Falconsai/nsfw_image_detection](https://huggingface.co/Falconsai/nsfw_image_detection) | `96cb0d0342c7afb80cab76ecc58b265fa44da256` |

第三个 [deepghs/anime_real_cls](https://huggingface.co/deepghs/anime_real_cls) 使用上游提供的
`mobilenetv3_v1.4_dist/model.onnx`，固定 revision
`097a6c1e9e62866a435810c883a45a7cb2c7077d`。该版本适合 CPU，权重约 16 MiB，
无需转换或安装新的推理依赖。

在准备机器的项目根目录执行（无需启动机器人）：

```sh
uv sync --group image-model-export
uv run --group image-model-export python scripts/export_image_classifier_models.py
uv run python scripts/prepare_anime_real_model.py
```

如果 Hugging Face 的 Xet 下载不可用，可在执行导出前设置 `HF_HUB_DISABLE_XET=1`。
模型输出到 `data/image_classifier/models/{anime,nsfw}/`，每个目录包含 `model.onnx` 和 `manifest.json`，
前两个合计约 655 MiB。新模型位于 `models/anime_real/`，三者合计约 671 MiB。
manifest 包含模型来源、revision 和 ONNX SHA-256；转换模型另记录源权重、依赖版本和误差。
重新导出应先停止使用这些文件的机器人，或导出到新目录再整体替换。

部署机器只需 `uv sync`，将准备好的整个 `models/` 目录复制到相同位置即可。
也可以配置 `IMAGE_CLASSIFIER_MODEL_DIR` 指向复制后的目录。
运行时只加载 ONNX，强制 `CPUExecutionProvider`；不需要 PyTorch、Transformers、CUDA 或网络下载模型。
启动时核对来源、标签、校验和和输入输出形状。模型缺失或损坏时记录错误并停止收集，
其余机器人功能仍可运行；补齐后重启。过期结果清理仍会执行。

## 分类含义

第一个模型的原始四类是：

- `anime_adult`
- `anime_contentious`
- `anime_non_adult`
- `non_anime`

二次元概率为三个 `anime_*` 概率之和，大于等于 `ANIME_THRESHOLD` 划为 `anime`，否则为 `real`。
`real` 在此表示模型的“非二次元”，也会包含文字截图等非照片内容。
对于二次元结果，再选三个 `anime_*` 中概率最高的作为细分类。
原始四类最高分标签另存为 `original_label`，与聚合后的二分类可能不同。

前三个标签看起来描述成年、存疑和未成年倾向，但上游没有提供明确的年龄标准或标注规范。
因此保留英文原标签，不把它们当作真实年龄结论，也不使用它们决定 NSFW。

NSFW 模型启用时，每张图片都独立经过第二个模型，输出 `normal` / `nsfw` 两类 softmax 概率。
`P(nsfw) >= NSFW_THRESHOLD` 时分类为 `nsfw`。两个阈值默认都是 0.5。
所有原始分数都会保留，便于后续重新调整分类规则。

`anime_real_cls` 独立输出 `anime` / `real` 两个概率，保存为 `anime_real_scores`。
它与第一个模型分别展示，不融合分数。该 ONNX 已包含 softmax，直接使用其输出概率。
预处理遵循上游 imgutils：透明背景合成白色，RGB、双线性缩放为 384×384，
像素除以 255，再以 0.5 / 0.5 归一化。动图同样取抽样帧的平均分数。

三个模型默认启用，可以独立关闭。两个二次元模型同时启用时，为保持现有归档规则，
目录分类和页面上方汇总仍以 `real-or-anime-age` 为准；关闭它后改用 `anime_real_cls`。
结果中的 `dimension_model` 明确记录汇总来源。
只启用 `anime_real_cls` 时，二次元没有年龄细分，保存到 `anime/unspecified/`。
二次元模型都关闭时 `dimension=unknown`；NSFW 关闭时 `safety=unknown`，不推断为 normal。
未运行模型的分数是空对象、概率是 null，结果中 `enabled_models` 记录实际运行的模型。
这些新增归档目录仍遵守相同的三天清理规则（JSON schema version 2）。

前两个模型均按上游配置预处理：RGB、双线性缩放到 224×224、像素除以 255，
再按均值/标准差 `[0.5, 0.5, 0.5]` 归一化，输入为 NCHW float32。
解码后先应用 EXIF 方向。

GIF / 多帧 WebP 等动图默认均匀抽取最多 8 帧，包含首尾帧。
二次元四类分数取抽样帧平均，NSFW 取最高风险帧的分数。
JSON 保存总帧数、抽样索引和每帧结果。未抽到的短暂内容可能被漏检。

## 保存与清理

默认临时目录为 `data/image_classifier/tmp/`：

```text
tmp/
  anime/
    anime_adult/{normal,nsfw,unknown}/<UTC时间_UUID>/
    anime_contentious/{normal,nsfw,unknown}/<UTC时间_UUID>/
    anime_non_adult/{normal,nsfw,unknown}/<UTC时间_UUID>/
    unspecified/{normal,nsfw,unknown}/<UTC时间_UUID>/
  real/
    non_anime/{normal,nsfw,unknown}/<UTC时间_UUID>/
  unknown/
    unspecified/{normal,nsfw,unknown}/<UTC时间_UUID>/
```

每条记录包含原图 `image.<原始格式>` 和 `result.json`。JSON 保存：

- 保存时间和到期时间（UTC）、原图 SHA-256、宽高、格式。
- 机器人、群、用户、消息 ID、消息时间和图序号；不保存图片下载签名 URL。
- 二次元聚合结果及来源、细分类、原始标签、四类概率、独立的 anime/real 分数，以及 NSFW 分数与结果。
- 阈值、模型来源、revision 和 ONNX 校验和；动图另有逐帧分数。

文件先写入暂存目录，再原子重命名，避免将未完成记录暴露为完整结果。
同一图片在不同消息出现时各存一条记录，不会刷新旧记录的期限。

使用机器人进程内的 APScheduler，在 **Asia/Shanghai 每天 12:00** 删除保存满 **72 小时** 的记录。
例如周一 13:00 保存的图，周五 12:00 清理；每日一次清理意味着实际保留可能接近 96 小时。
启动时也补做清理，处理机器人停机错过计划的情况。
只清理上述分类目录下符合本插件命名规则的记录，模型目录不在清理范围。
清理使用文件夹中的 UTC 保存时间，不依赖文件修改时间或 JSON 是否完好。

## 输入与运行限制

处理普通群消息的所有图片段，以及合并转发内的图片；缺少 URL 时尝试 OneBot `get_image`。
转发记录支持嵌套与循环去重。单独回复旧消息不会重新归档引用图。
只处理机器人实际收到的群消息，不回溯历史聊天。

后台单队列串行推理，解码、推理和文件操作在线程中运行。
队列满时等待入队，不主动丢弃新图。优雅关闭最多等待 30 秒排空队列；
异常退出、断电或关闭超时可能丢失尚未处理的内存队列。
下载失败、损坏或超限图片记录日志并跳过，不影响同条消息后续图片。
默认单图最大 20 MiB / 2500 万像素，转发解析最多 16 层 / 10000 段。

可在 `.env` 中配置以下项：

| 变量（前缀均为 `IMAGE_CLASSIFIER_`） | 默认值 |
| --- | --- |
| `ENABLED` | `true` |
| `ANIME_ENABLED` / `ANIME_REAL_ENABLED` / `NSFW_ENABLED` | 均为 `true` |
| `MODEL_DIR` | `data/image_classifier/models`（相对工程根的默认绝对路径） |
| `CACHE_DIR` | `data/image_classifier/tmp`（相对工程根的默认绝对路径） |
| `ANIME_THRESHOLD` / `NSFW_THRESHOLD` | `0.5` / `0.5` |
| `CPU_THREADS` | `2` |
| `QUEUE_SIZE` | `512` 条消息 |
| `MAX_DOWNLOAD_MB` | `20` |
| `MAX_PIXELS` | `25000000` |
| `ANIMATION_SAMPLES` | `8` |

关闭收集不关闭过期清理。配置路径时不要把临时目录指向其他插件的图片目录。

## 本地验证

### 拖放图片的网页

在项目根目录启动独立后台，模型加载完成并监听端口后自动打开浏览器：

```sh
uv run kanami-image-web
```

默认地址为 `http://127.0.0.1:12709/`。也可以使用
`uv run python -m kanamibot.plugins.image_classifier.web` 启动。
该后台无需启动 NoneBot 或 NapCat。

将一张或多张图片拖入页面，或点击选择本地图片，自动依次分析。
每张图片显示预览、原模型的四个分类概率、`anime_real_cls` 的两个分类概率、
二次元/非二次元汇总概率，以及独立的 `normal` / `nsfw` 概率。
支持将本次成功结果导出为 JSON。
页面的三个模型开关独立控制；选择保存在当前浏览器中，刷新后保留。
开关只影响之后添加的图片，每张图加入队列时固定选择；旧结果不改写。
禁用模型显示“本图未启用此模型”，后台不会为该图运行它。
至少需要选择一个模型。网页开关只控制该用户的上传，不修改 QQ 群自动收集的配置。
网页推理沿用相同模型、预处理和动图抽样规则。
网页上传的图片及结果只在内存中处理，不写入群聊图片临时目录。
超过推理文件大小（默认 20 MiB）或像素数（默认 2500 万）限制时，网页后台自动等比缩小到
最长边最多 2048 像素，并继续满足像素限制。先尝试无损 PNG / WebP，再按需降低 WebP
质量和尺寸，保留透明通道；未超限的图片直接使用原始数据。
超限动图按原有抽样数均匀保留覆盖全程的帧，编码为 WebP 后分析，不只取首帧。
结果显示压缩前后的大小、尺寸和动图帧数；原文件与网页预览不变，导出 JSON 包含 `preparation`。
原始上传上限为推理大小限制的 5 倍（默认 100 MiB）；超过此上限或解码安全限制仍会提示错误。

参数示例：

```sh
uv run kanami-image-web --port 8766 --no-open
uv run kanami-image-web --model-dir /path/to/models --cpu-threads 2
uv run kanami-image-web --disable-model anime --disable-model nsfw
```

可用 `--anime-threshold`、`--nsfw-threshold` 调整阈值。
`--disable-model` 可重复使用，支持 `anime`、`anime_real`、`nsfw`：后台不加载被禁用的模型，
其模型文件可以缺省，页面中对应开关不可用。
网页里的开关只跳过推理，已加载的会话仍保留以便再次启用。
每个模型另有“卸载”按钮，主动释放该独立后台的 ONNX 会话及其内存，保留磁盘上的模型文件。
卸载后显示“加载”按钮，从本地文件重新加载，并恢复之前的开关选择。
可卸载全部模型；重新加载至少一个模型后即可继续上传，已有结果不变。
卸载与加载影响连接同一后台的所有页面，后台会等待正在执行的推理完成后再操作；
本页还有待处理图片时，模型操作按钮暂不可用。
启动参数禁用的模型不能通过网页加载，需修改启动参数后重启。
此独立网页与 CLI 相同，使用命令行参数，不读取 NoneBot 的 `.env`。
默认只监听本机；保持终端运行，按 `Ctrl+C` 关闭后台。
页面静态文件随插件提供，不需要 Node.js、前端构建或外部 CDN。

### 命令行

不连接机器人，对任意本地例图运行：

```sh
uv run python -m kanamibot.plugins.image_classifier.cli example1.jpg example2.png
uv run python -m kanamibot.plugins.image_classifier.cli --cleanup
uv run python -m kanamibot.plugins.image_classifier.cli example.jpg --disable-model anime
```

CLI 的 `--model-dir` / `--cache-dir` / `--anime-threshold` / `--nsfw-threshold` 可以覆盖默认值，
它不读取 NoneBot 的 `.env`。每张图输出一行包含结果文件路径和完整分类的 JSON。

自动测试：

```sh
uv run python -m unittest discover -s test -p test_image_classifier.py
uv run python -m unittest discover -s test -p test_image_classifier_web.py
```

实际模型验证见 [VALIDATION.md](VALIDATION.md)。本机仅做离线验证，没有连接或启动部署机器人。
