# 离线验证记录

## A/B 分类规则接入（2026-11-05，Windows）

按用户确认实施 `ab-v1`：A1优先于A2；B1、B2各自的两项条件取或，B1优先于B2。
严格区分 `>` 和 `<=`，没有把70%、50%或20%处的相等情况归入“超过”。
JSON、网页标签、预览筛选及保存元数据均使用同一规则。
旧记录用已有原始分数重算标签，保持原目录和原过期时间；新增B2目录纳入清理。
旧单阈值配置及CLI参数移除，分类规则和版本写入 `thresholds`。

图片分类器相关测试：**61项通过、1项因Windows符号链接权限跳过，另有17个参数化子测试通过**。
覆盖阈值上下边界、两模型冲突、或条件、等级优先级、模型关闭、旧缓存重算、
A/B预览过滤、旧图选存及B2目录清理。Ruff与Git差异空白检查通过。
实际加载本机三套CPU ONNX对内存合成图执行推理，成功返回A/B标签及双模型来源。
这项合成图检查只验证运行链路，不是准确率评估。没有启动、重启或连接QQ服务。

## QQ 工程接入检查（2026-11-05，Windows）

本轮没有启动或重启机器人及 NapCat，也没有发送真实QQ消息。
接入测试使用 OneBot 事件、API mock 和本地文件；真实QQ私聊投递和远端缓存寿命仍需部署后验证。

- 图片分类器相关测试：58项通过，1项跳过。跳过项为 Windows 当前账户无法创建符号链接，
  不是删除逻辑放宽。Ruff 检查通过。
- 验证群聊、私聊、自身消息排除、转发遍历、可信本地路径及 file URI 复用、路径范围及读取限额。
- 验证相同内容只推理一次、独立来源记录、硬链接复用、重启索引恢复，以及模型和抽帧设置变化后的缓存失效。
- 验证48小时边界、损坏元数据、外部引用不被清理、永久保存不受临时清理影响、源图变化时拒绝误存。
- 验证预览内容去重及分类筛选、23点上海时区调度、私聊发送失败不影响其他超管。
- 新命令只允许超管私聊；预览默认允许全部分类，可通过配置调整。保存目录使用管理员配置的别名。

本机实际 CPU 基准（两线程；1024×1024合成JPEG；预热后10次；含解码及预处理，不含下载/保存）：

| 启用模型 | 中位耗时 | 最大耗时 |
| --- | ---: | ---: |
| anime | 269.82 ms | 288.35 ms |
| nsfw | 277.40 ms | 362.58 ms |
| anime_real | 49.80 ms | 56.13 ms |
| 三模型 | 555.64 ms | 601.55 ms |

模型加载2.277秒。CPU标识：AMD64 Family 25 Model 68 Stepping 1, AuthenticAMD。
这仅用于工程开销判断，不代表真实图片分类质量。复现脚本：`scripts/benchmark_image_classifier.py`。
两个 ViT 模型固定 batch=1，本轮保留后台逐张推理；不把攒图后的逐张循环称为批量推理。
本轮没有重新导出动态 batch 模型，因此没有声称测量过动态 batch 的性能收益。

临时记录48小时逻辑过期，每分钟物理清理；正常运行时可能额外等待一个清理周期，
停机期间不能执行删除。NapCat 外部缓存不归本插件清理，其实际寿命由 NapCat 决定。
队列仍在内存中，故障停机会丢失尚未处理的任务；本轮没有提供持久消息队列。

以下是先前版本的历史验证记录；三天保留、仅监听群聊和中午清理等描述已由本轮实现取代。

验证日期：2026-11-04。准备机器：Apple M1、Python 3.12，仅使用 CPU。
没有连接 NapCat 或启动机器人服务。

## 模型文件

由两个指定仓库的固定 revision 导出 float32 ONNX，opset 17。
导出依赖版本：PyTorch 2.15.0、Transformers 4.57.6、ONNX 1.23.2、ONNX Runtime 1.31.1。
这些转换依赖仅用于准备机器，机器人运行时只使用 ONNX Runtime。

| 模型 | ONNX SHA-256 | 随机输入最大 logit 误差 |
| --- | --- | --- |
| `real-or-anime-age` | `8b3f8134c06c62f644882c95e507dfbe994e26eaed49663fb0a9d687fa50bc66` | `7.1526e-6` |
| `nsfw_image_detection` | `e7a9df42eb9181e246e91868f1ded70ad74a0840a895d0aae697bd05235e4615` | `3.0994e-6` |

通过 ONNX checker，并实际加载到 `CPUExecutionProvider`。
模型及 manifest 位于本次工作目录的 `data/image_classifier/models/{anime,nsfw}/`，合计约 655 MiB。
模型二进制没有加入 Git；部署时需复制整个模型目录。

## 例图结果

默认阈值均为 0.5。表中概率为 softmax 分数，不能当作真实年龄或内容的确定结论。

| 例图 | 细分类 | P(二次元) | P(NSFW) | NSFW 分类 |
| --- | --- | ---: | ---: | --- |
| 动漫插画 `anime.png` | `anime_non_adult` | 0.998554 | 0.000145 | normal |
| 老虎照片 `tiger.jpg` | `non_anime` | 0.019778 | 0.000138 | normal |
| 人像照片 `portrait.png` | `non_anime` | 0.000178 | 0.207069 | normal |
| 猫照片 `cats.jpg` | `non_anime` | 0.000128 | 0.000131 | normal |
| 绘画 The Storm `art.jpg` | `non_anime` | 0.317513 | 0.000436 | normal |
| 肖像绘画 `painting_portrait.jpg` | `non_anime` | 0.029934 | 0.000111 | normal |

例图下载来源：

- [动漫插画](https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/diffusers/tips_anime_models.png)
- [老虎照片](https://huggingface.co/datasets/mishig/sample_images/resolve/main/tiger.jpg)
- [人像照片](https://huggingface.co/datasets/hf-internal-testing/fixtures_image_utils/resolve/main/lena.png)
- [猫照片](https://huggingface.co/datasets/hf-internal-testing/fixtures_image_utils/resolve/main/cats.jpg)
- [The Storm](https://www.metmuseum.org/art/collection/search/435997)
- [肖像绘画](https://images.metmuseum.org/CRDImages/ep/web-large/DP-14286-001.jpg)

逐张使用原始 Transformers 图像处理器和 PyTorch checkpoint 对照：

- 预处理张量最大绝对误差不超过 `1e-6`。
- 两个模型在六张图上的最高分标签均与原模型一致。
- 二次元模型最大 logit 误差 `1.0729e-5`；NSFW 模型最大误差 `5.7220e-6`。
- 对所有 logits 要求 `rtol=1e-4, atol=1e-4`，断言全部通过。

另将人像照片的 NSFW 阈值设为 0.1，实际 CPU 推理后成功保存到 `real/non_anime/nsfw/`，
验证分流和落盘路径。此项验证阈值逻辑，不表示该照片是真实 NSFW 样本。
上述少量例图验证实现和转换一致性，没有构成分类准确率评估，也没有验证真实 NSFW 正例的召回率。

本次完整报告保存在 `data/image_classifier/validation/report.json`，包含输入 SHA-256、
逐图概率、逐图误差、CPU 耗时和结果 JSON 路径；例图和来源在 `data/image_classifier/examples/`。
耗时受本机并发测试与负载影响，没有作为吞吐基准。

复现原模型对比（需先完成模型导出）：

```sh
uv run --group image-model-export python scripts/verify_image_classifier_models.py <例图路径...>
```

## 接入和回归测试

使用实际 ONNX 模型、模拟群消息和本地 HTTP MockTransport，走完整的
`GroupMessageEvent → event_preprocessor → 后台队列 → 两模型推理 → 原图和 JSON` 链路。
成功得到 `anime/anime_non_adult/normal/` 记录，并核对群 ID、消息 ID 和分类结果。
结果位于本次工作目录的 `data/image_classifier/group-integration-results/`。

新增 **18 项**单元测试通过，包括：

- 三个二次元概率合并、细分类、原始最高分标签和 NSFW 独立阈值。
- RGB/NCHW 预处理、动图抽样及最高风险帧聚合、坏图和大小限制。
- 模型校验和、普通图片、合并转发循环、单张下载失败隔离。
- 保存原图、来源和分数，重复图片分别保留，不刷新旧记录期限。
- 恰满 72 小时与不足 72 小时边界、损坏 JSON、外部目录和符号链接保护。
- 推理异常后继续处理、关闭排空队列、模型缺失和禁用收集时的清理。
- 多个群的消息接入，以及时区固定为北京时间中午 12 点。

全量 **41 项**回归测试通过；Ruff 检查和 `uv lock --check` 通过。
本机已有 ESBR 测试将 `Path.resolve()` 与未解析的临时路径直接比较，
标准全量运行会因 macOS `/var → /private/var` 别名出现一项路径断言失败。
全量验证将 `tempfile.tempdir` 规范为真实路径后运行，未修改无关 ESBR 代码或测试。

## 网页验证（2026-11-05）

新增独立网页后台 `kanami-image-web`，只监听本地 `127.0.0.1:8765`，复用上述 CPU ONNX 模型。
实际 HTTP 上传动漫插画和老虎照片，分别得到 `anime_non_adult` 和 `non_anime`，
概率与之前的离线结果一致。损坏图片返回可读的 422 错误。

新增 **9 项**网页测试通过，覆盖全部分数和模型信息的 API 返回、模型复用、静态资源、
空上传、超限上传（含无 Content-Length 的流式请求）、坏图恢复、像素限制、
模型缺失时的页面状态，以及后台监听完成后才自动打开浏览器。
包含原有测试的全量 **50 项**回归通过，临时目录仍采用上文说明的 macOS 路径规范化。

另以真实 Chromium 浏览器和实际 ONNX 推理验证：

- 批量选择、拖放图片、每张图片显示四个细分类和两个 NSFW 概率。
- JSON 导出包含完整概率和来源模型信息。
- 同一队列中坏图失败后，下一张有效图片仍能成功分析。
- 文件名作为文本显示，含 HTML 字符的文件名不会变成页面内容。
- 清空列表和状态复位；390px 手机布局没有横向溢出。
- JavaScript 无运行错误，并目视检查桌面、手机的空状态与结果布局。

截图、浏览器导出和测试记录位于本次工作目录的 `data/image_classifier/web-validation/`。
网页静态文件完全本地提供，不依赖 CDN、Node.js 或前端构建；网页上传不写入结果归档。

## DeepGHS 模型与独立开关（2026-11-05）

新增 `deepghs/anime_real_cls` 的官方 `mobilenetv3_v1.4_dist` ONNX，标签顺序为 `anime`、`real`。
固定 revision `097a6c1e9e62866a435810c883a45a7cb2c7077d`，文件 16,821,153 字节。
SHA-256 为 `c02d68cc7f56dbcbcd4f41a10df190385c1f963f22c4eefb8d162869800ed3b0`。
输入是 `input: [batch, 3, 384, 384]`，输出是 `output: [batch, 2]`；
ONNX 图最后一层为 Softmax，因此直接使用概率，避免重复 softmax。

适配参考 [imgutils 分类预处理](https://github.com/deepghs/imgutils/blob/main/imgutils/generic/classify.py)
和 [RGB 编码](https://github.com/deepghs/imgutils/blob/main/imgutils/data/encode.py)：
透明像素合成白色、双线性缩放到 384×384、RGB、NCHW、像素除以 255，按 0.5 / 0.5 归一化。
对原有六张例图，适配输入张量与该处理流程逐元素一致，最终概率与直接运行官方 ONNX 完全一致。

| 例图 | P(anime) | P(real) |
| --- | ---: | ---: |
| 动漫插画 | 0.988435 | 0.011565 |
| 老虎照片 | 0.011386 | 0.988614 |
| 人像照片 | 0.016308 | 0.983692 |
| 猫照片 | 0.015576 | 0.984424 |
| The Storm 绘画 | 0.911933 | 0.088067 |
| 肖像绘画 | 0.944244 | 0.055756 |

绘画样本与前一个模型的判断存在差异，因此保存和展示两套独立结果，不合并为单一概率。
目录分类保留原模型优先、关闭原模型时使用新模型的规则，并记录 `dimension_model`。
报告：`data/image_classifier/validation/anime-real-report.json`。
复现：`uv run python scripts/verify_anime_real_model.py <例图路径...>`。

新增 **10 项**单元测试，连同现有测试，全量 **60 项**通过（沿用上文 macOS 临时路径规范化）。
新增检查包括：禁用模型不加载、按图跳过推理、仅运行新模型、仅运行 NSFW、
两套二次元分数保持独立、空/未知选择被拒绝、透明背景预处理、概率不重复 softmax，
以及新增 `unknown` / `unspecified` 归档目录仍会按期清理。
单独通过 CLI 运行新模型，成功保存到 `anime/unspecified/unknown/`，关闭的 NSFW 保持未检测状态。

真实 Chromium 浏览器检查通过：三个开关独立切换、每图八个分数、仅运行新模型、
禁用项显示为未运行、旧结果不随开关改写、JSON 导出只包含该图实际运行的模型信息，
刷新后保留选择、全关时提示至少启用一个模型、仅运行 NSFW 时不捏造二次元结论。
390px 手机页面可以操作所有开关，布局无横向溢出；JavaScript 没有运行错误。
截图和检查记录位于 `data/image_classifier/web-validation-model-switches/`。

## 主动卸载、重新加载与端口调整（2026-11-05）

默认端口改为 `12709`。停止此前本机的独立网页后台，使用不带 `--port` 的命令重新启动，
确认监听 `127.0.0.1:12709`，旧端口 `8765` 已停止监听；没有启动或连接 QQ 机器人。

三个模型各有“卸载 / 加载”按钮。卸载释放 ONNX 会话并保留模型文件，
加载沿用原有校验和、输入输出形状检查与 CPUExecutionProvider。
加载、卸载、上传推理共用同一把锁，模型可用性检查与结果的来源信息也在锁内完成。
模型全部卸载后页面仍可加载模型，启动参数禁用的模型不能由页面重新启用。

新增 **7 项**单元测试，图像分类相关 **44 项**通过，全量 **67 项**通过
（仍沿用前述 macOS 临时目录路径规范化）。覆盖会话对象释放、重复操作、加载失败恢复、
全部卸载后的状态与选择校验、启动禁用限制，以及推理期间卸载必须等待且保留原结果来源信息。
Ruff、JavaScript 语法检查、依赖锁检查和 diff 空白检查均通过。

真实 Chromium 浏览器与实际三个 ONNX 模型验证通过：

- 动漫插画、老虎照片各显示八个分类分数，处理队列期间模型操作按钮暂不可用。
- 依次卸载全部模型后，保留旧结果并提示先加载模型；可单独加载 DeepGHS 继续分析。
- 加载恢复之前的开关选择，包括关闭状态；重新加载全部模型后，同一例图完整分类结果与原结果一致。
- JSON 导出保留每张图片的实际模型来源，刷新保留选择。
- 390px 手机页面无横向溢出；桌面与手机截图已目视检查，JavaScript 无运行错误。

本次浏览器运行中，进程 RSS 从约 678 MiB 降到全部卸载后的约 426 MiB。
这是本次进程的观测值，分配器可能保留缓存，不能据此承诺固定的 RSS 降幅。
测试结束后已恢复三个模型的加载状态。
复现脚本、导出结果、检查记录和截图保存在 `data/image_classifier/web-validation-unload/`。
