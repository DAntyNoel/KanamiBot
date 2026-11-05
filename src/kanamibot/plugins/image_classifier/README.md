# QQ 图片自动分类与分组发图

自动监听全部 OneBot V11 群聊和私聊图片，以 CPU ONNX Runtime 运行启用的模型，保存图片或本地引用及分类 JSON。
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

采用固定规则 `ab-v1`。记 `x` 为三个 `anime_*` 分数之和，`y` 为二次元分类模型的 `anime` 分数，
`a` 为 `anime_adult`，`c` 为 `anime_contentious`，`n` 为 NSFW 模型的 `nsfw` 分数。
分号两侧按“或”处理，按表中从上到下的顺序判断：

| 分类 | 条件 | 兼容字段 |
| --- | --- | --- |
| A1 二次元 | `x > 0.70` 或 `y > 0.70` | `dimension=anime` |
| A2 三次元 | 未命中A1，且（`x <= 0.30` 或 `y <= 0.30`） | `dimension=real` |
| A3 无法判断 | 均未命中 | `dimension=unknown` |
| B1 NSFW | `a > 0.50` 或 `n > 0.70` | `safety=nsfw` |
| B2 涩图 | 未命中B1，且（`a+c > 0.70` 或 `n > 0.20`） | `safety=suggestive` |
| B3 SFW | 两个相关模型都已运行，且均未命中 | `safety=normal` |

A1和A2同时命中时优先A1；B1和B2同时命中时优先B1。“超过”使用严格大于，“不超过”使用小于等于。
模型关闭时只使用实际存在的分数；缺少细分类或NSFW模型且未命中B1/B2时，B类为空、
`safety=unknown`，显示“未完整检测”，不把缺测当作SFW。两个二次元模型都关闭时为A3。
标签不等同于真实年龄判断，三次元标签也可能包含截图等非照片内容。
所有原始分数保留；二次元仍保留最高分细类和原始四类最高分标签 `original_label`。

`anime_real_cls` 独立输出 `anime` / `real` 两个概率，保存为 `anime_real_scores`。
它与第一个模型分别展示，不融合分数。该 ONNX 已包含 softmax，直接使用其输出概率。
预处理遵循上游 imgutils：透明背景合成白色，RGB、双线性缩放为 384×384，
像素除以 255，再以 0.5 / 0.5 归一化。动图同样取抽样帧的平均分数。

三个模型默认启用，可以独立关闭。目录分类和页面标签遵循上述A/B规则。
结果中的 `dimension_model` 记录判断来源，同时运行两个二次元模型时为 `anime+anime_real`。
`anime_probability` 保留细分类三类之和；没有细分类模型时记录二分类模型分数，不混合概率。
只启用 `anime_real_cls` 时，二次元没有年龄细分，保存到 `anime/unspecified/`。
二次元模型都关闭时 `dimension=unknown`；NSFW关闭时仍可由细分类模型命中B1/B2。
未运行模型的分数是空对象、概率是 null，结果中 `enabled_models` 记录实际运行的模型。
这些新增归档目录同样遵守48小时清理规则（JSON schema version 4）。

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
    anime_adult/{normal,suggestive,nsfw,unknown}/<UTC时间_UUID>/
    anime_contentious/{normal,suggestive,nsfw,unknown}/<UTC时间_UUID>/
    anime_non_adult/{normal,suggestive,nsfw,unknown}/<UTC时间_UUID>/
    unspecified/{normal,suggestive,nsfw,unknown}/<UTC时间_UUID>/
  real/
    non_anime/{normal,suggestive,nsfw,unknown}/<UTC时间_UUID>/
  unknown/
    unspecified/{normal,suggestive,nsfw,unknown}/<UTC时间_UUID>/
```

每条记录包含 `result.json`；HTTP 下载的图片另外保存 `image.<原始格式>`，
可信本地图片仅在 `image_reference` 记录原始路径，不复制图片。JSON 保存：

- 保存时间和到期时间（UTC）、原图 SHA-256、宽高、格式。
- 机器人、群、用户、消息 ID、消息时间和图序号；不保存图片下载签名 URL。
- 二次元聚合结果及来源、细分类、原始标签、四类概率、独立的 anime/real 分数，以及 NSFW 分数与结果。
- 阈值、模型来源、revision 和 ONNX 校验和；动图另有逐帧分数。

文件先写入暂存目录，再原子重命名，避免将未完成记录暴露为完整结果。
同一图片在不同消息出现时各存一条来源记录，不会刷新旧记录的期限。
相同内容的下载图片优先通过硬链接复用磁盘数据；文件系统不支持时回退为独立副本。
删除旧记录的硬链接不影响新记录。启动会重建当前未过期图片的索引。

使用机器人进程内的 APScheduler，**每分钟**删除保存满 **48小时** 的记录。
预览和保存操作实时排除过期记录；正常运行时物理删除最多额外等待一个清理周期。
这不是停机期间的文件删除保证：启动时补做清理，处理机器人停机错过计划的情况。
历史72小时元数据也按目录时间应用新的48小时规则。
只清理上述分类目录下符合本插件命名规则的记录，模型目录不在清理范围。
清理使用文件夹中的 UTC 保存时间，不依赖文件修改时间或 JSON 是否完好。

## 输入与运行限制

处理群聊和私聊的所有图片段，以及合并转发内的图片；忽略机器人自己发送的消息。
通过 OneBot `get_image` 尝试取得本地路径，只有处于 `LOCAL_ROOTS` 中且当前进程可读的文件才复用。
路径不可用时回退到 HTTP(S) 下载；不把 NapCat 容器内的路径当作本机路径。
NoneBot 本身不保证已将图片缓存到磁盘，消息中的 `file` 也可能只是QQ文件标识。
默认可信目录列表为空；部署时须明确配置实际 NapCat 缓存目录或共享挂载目录，不能填整个磁盘。
引用模式依赖 NapCat 源文件的寿命：源图被删或修改后预览会标记失效，请求原图时会提示失效。
本插件的清理从不删除引用的外部文件；48小时仅约束本插件的临时记录和自有缓存。
转发记录支持嵌套与循环去重。单独回复旧消息不会重新归档引用图。
只处理机器人实际收到的消息，不回溯历史聊天。

后台单队列串行推理，解码、推理和文件操作在线程中运行。
按 SHA-256 缓存最近512种内容的推理结果，同样的图片仍逐条记录来源。
启动只恢复模型来源、抽帧及像素限制相符的未过期结果；超出缓存容量的图片可能再次推理。
旧记录读取时使用已保存的分数重新计算A/B分类，不重复推理、不移动旧目录，也不延长保留时间。
两个 ViT ONNX 输入固定为 batch=1，等待攒图不等于批量推理，不会自动提升吞吐。
当前采用即时入队、后台逐张处理；若长期超载，应先降低抽帧数或按需求关闭模型，
再考虑重新导出动态 batch 模型和有等待上限的微批处理，并实测吞吐和内存。
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
| `CPU_THREADS` | `2` |
| `QUEUE_SIZE` | `512` 条消息 |
| `MAX_DOWNLOAD_MB` | `20` |
| `MAX_PIXELS` | `25000000` |
| `ANIMATION_SAMPLES` | `8` |
| `LOCAL_ROOTS` | `[]`，只读复用的可信本地缓存目录 |
| `PREVIEW_DIMENSIONS` | `["anime","real","unknown"]` |
| `PREVIEW_SAFETY` | `["normal","suggestive","nsfw","unknown"]` |
| `PREVIEW_CATEGORIES_A` | `["A1","A2","A3"]` |
| `PREVIEW_CATEGORIES_B` | `["B1","B2","B3","unknown"]`，unknown表示未完整检测 |
| `PREVIEW_PAGE_SIZE` | `20`，每页最多40张 |
| `INFERENCE_CACHE_SIZE` | `512`，缓存不同内容的分类结果数 |

关闭收集不关闭过期清理。配置路径时不要把临时目录指向其他插件的图片目录。
旧的 `ANIME_THRESHOLD` / `NSFW_THRESHOLD` 环境变量及同名CLI选项已移除，避免覆盖固定A/B规则。

## 超管缩略图预览与分组原图发送

北京时间每天 **23:00**，向 NoneBot `SUPERUSERS` 中的 QQ 账号私聊发送当天全部审查页。
默认允许全部分类，管理员可配置 `PREVIEW_CATEGORIES_A` / `PREVIEW_CATEGORIES_B`，与旧维度过滤取交集。
预览只取北京时间当日00:00至生成时已保存且未过期的记录，按内容去重，按A/B组合分页。
每格200×200以内、保持比例居中排列，标注组内数字序号；同组跨页连续编号。
主动预览仅发送重新编码的JPEG缩略图拼图；动图只显示首帧。
管理员明确指定数字序号时才发送对应完整原文件（动图保留动画），通过字节传给OneBot。

全部命令仅超管私聊可用，无需 `#` 或“图片”；分组命令可省略“图”：

| 指令 | 含义 |
| --- | --- |
| `图` | 查看当天全部分组缩略图 |
| `图ab` | 查看A1B1组的全部缩略图页 |
| `图ab 3` | 发送A1B1组第3张原图 |
| `图aabb 3,5,12,25` | 发送A2B2组这4张原图 |
| `图a2b2 3，5,12，25` | 数字分级写法，中英文逗号可混用 |
| `aabb 3` | 等同于 `图aabb 3`，发送A2B2第3张原图 |
| `aabb` | 等同于 `图aabb`，查看今日截至当前的该组缩略图 |

分组为 `ab`=A1B1、`abb`=A1B2、`abbb`=A1B3、`aab`=A2B1、`aabb`=A2B2、
`aabbb`=A2B3、`aaab`=A3B1、`aaabb`=A3B2、`aaabbb`=A3B3。
也可统一写成 `a1b1` 至 `a3b3`。尚未完整检测B类的图片仍可预览，不支持按A/B组请求原图。
序号从1开始，重复序号只发一次，按用户指定顺序发送；每次最多100张。
无效、过期、源图失效或发送失败的序号会提示，不阻断其余有效图片。

每位管理员每天每组的序号持久保存，新图只追加编号，失效图片的编号不挪给其他图。
数字请求使用最近一次成功展示的该组日期快照，因此午夜后仍能按昨晚预览请求，前提是图片未满48小时。
重新查看今天该组后，数字请求改用今天的组内编号。相同内容按SHA-256索引，不再要求输入16位编号。

23点自动审查成功投递的SHA-256按超管账号写入 `tmp/review_history.sqlite3`，历史不随临时图片清理。
此前日期自动发给该管理员的内容，即使今天重发，也不再进入预览；23点任务还排除当天自动投递的内容。
手动预览展示今日截至当前已收集且符合筛选规则的图片，可重看当天自动发过的内容。
手动查看缩略图、索取原图均不写自动投递历史，不减少或排除23点自动审查的候选图片。
手动预览仅记录序号对应关系。自动发送失败或源图失效不记为已投递；各管理员的状态独立。
升级前无对应记录的历史投递无法自动推断。QQ风控等发送失败会记录日志，不保证自动补发。

本分类器的保存命令、归档方法和 `SAVED_DIRS` 配置已移除；旧的 `#图片预览`、`#图片保存` 也已移除。
图片保持48小时临时清理策略；此前已永久保存的文件不会被删除。原有 `image` 图库的存图指令不受影响。

本机 CPU 基准可运行 `uv run python scripts/benchmark_image_classifier.py`，无需连接QQ。
2026-11-05 Windows AMD64、两线程、1024×1024合成JPEG、预热后10次：
anime / nsfw / anime_real 单模型中位分别约270 / 277 / 50毫秒；三个模型合计约556毫秒，
最大约602毫秒；模型加载约2.28秒。包含解码和预处理，不含网络和落盘，也不代表分类准确率。
不同尺寸、动图和机器负载需要另测；默认最多8帧的动图可能接近静态图的8倍推理工作量。

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
