# 搜图

从「鱼鱼找鸡 1.0.0」（小鲸鱼，MIT）抽离搜图业务，以 Python / NoneBot2 重写。
不安装原插件，不运行其 Node/OneBot 启动器，不接入其 VPN、系统代理或进程管理。
使用仓库已有的 httpx、Pillow、Playwright，无新增依赖，加载插件不请求外部服务。
原实现的许可保留在 [LICENSE.upstream](LICENSE.upstream)。

## 命令

- 引用图片发送 `#搜图`，或把命令与图片一起发送。引用图优先，每次查第一张。
- 支持前置艾特，例如 `@12345678 #搜图`，实际 QQ 艾特消息段和文本形式均可。
- `#搜图 https://example.com/image.jpg`：下载公开图片并查询，可供 URL 型引擎使用。
- `#搜图 帮助`：查看用法。原触发词不注册。
- `/disable image_search`、`/enable image_search`：使用仓库 Core 群模块开关及黑名单。

接受查询后在原命令上贴 OK 表情，不发送开始搜索的文字消息；贴表情失败仍继续查询。
结果最多附三张预览，优先使用匹配结果的缩略图；取不到缩略图时显示查询原图并明确标注。
所有预览均保持比例，宽、高不超过 512px，小图不放大。
引擎不可用及具体原因只在触发者是超管时显示；普通用户的结果隐藏这些提示。
所有引擎失败时，普通用户只收到通用的暂未获得结果提示，诊断信息仍保留在日志中。

结果沿用「平台／名字／账号和主页链接」版式。支持 Twitter/X、微博、B站、Instagram、
Pixiv 的 URL 账号证据；帖子链接归一为账号主页，过滤平台功能页面。
按不同来源页面中的名字共识过滤混入账号；Pixiv 相似度低于 80% 不作为作者结果。
已有直接账号后，可按作者名补查其他平台，最多补两条，明确标注「推断，未核实」。
百度账号标注「相似图，未核实」。动画截图独立显示番名、集数、起始时间和 AniList 链接，
相似度低于 87% 不展示。没有账号时仍显示相似图源，并区分无匹配与引擎请求失败。

## 引擎

| ID | 方式 | 说明 |
| --- | --- | --- |
| `lens` | 浏览器上传图片 | 默认启用；解析视觉结果、完全匹配及不透明跳转卡片 |
| `saucenao` | HTTP 上传图片 | 默认启用；免费匿名接口，可能限流 |
| `tracemoe` | HTTP 上传图片 | 默认启用；动画截图匹配 |
| `iqdb` | HTTP 上传图片 | 可选；动漫图库，每个结果独立解析相似度 |
| `baidu` | 浏览器上传图片 | 可选；只保留可识别的账号链接 |
| `bing` | 浏览器使用公网图片 URL | 可选 |
| `yandex` | 浏览器使用公网图片 URL | 可选 |
| `google` | 浏览器 Lens uploadbyurl | 可选；有图片 URL 时使用，直接上传请选 `lens` |

HTTP 引擎并发，浏览器引擎串行；单个引擎失败仍返回其他引擎的结果。
实际可用性取决于网络、额度、人机验证及网站页面变化，不自动绕过人机验证。
若日志提示域名解析为本机、内网或保留地址，请检查本机 DNS / hosts。
显式配置浏览器代理后，固定搜图站点及其静态资源域名交由代理解析，兼容本地 Fake-IP / hosts；
用户图片地址、其他结果站点及 IP 地址仍执行本地公网检查。
必应网页搜索无账号结果时补查谷歌；不调用大模型或付费搜索 API。

## 配置

配置统一放仓库 `.env`，示例见根目录 `.env.example`：

- `IMAGE_SEARCH_ENGINES`：JSON 数组，默认 `["lens","saucenao","tracemoe"]`。
- `IMAGE_SEARCH_TIMEOUT`：每个引擎超时秒数，默认 45；账号网页补查总计最多 35 秒。
- `IMAGE_SEARCH_BROWSER_CHANNEL`：空为 Playwright Chromium；可设 `msedge` 或 `chrome`。
  也可通过 `IMAGE_SEARCH_BROWSER_EXECUTABLE` 指定浏览器路径，优先于 channel。
- `IMAGE_SEARCH_PROXY`：可选浏览器代理，例如 `http://127.0.0.1:7890`。
  不启动代理、不更改系统配置；HTTP 引擎仍按 httpx 默认环境代理设置访问。
- `IMAGE_SEARCH_STRICT_CONSENSUS`：默认 true；false 保留名字不一致的候选账号。
- `IMAGE_SEARCH_INFER_ACCOUNTS`：默认 true，可关闭作者名网页补查。
- `IMAGE_SEARCH_MAX_ACCOUNTS`：默认 3。
- `IMAGE_SEARCH_FOUND_IMAGE`：可选命中附图本地路径；相对路径从仓库根目录计算。
  `IMAGE_SEARCH_SEND_ON_MISS=true` 时无账号也附图；图文发送失败降级为纯文字。
- `IMAGE_SEARCH_COOLDOWN`：同群同用户默认 30 秒；同会话只允许一项查询，最多同时接收 4 项。
- `IMAGE_SEARCH_PER_USER_DAY`、`IMAGE_SEARCH_GLOBAL_DAY`：默认 20 / 100，0 为无限制。
  按北京时间重置；超管免冷却和配额，缓存命中不扣次数。有效图片发起查询即计次。
- `IMAGE_SEARCH_CACHE_DAYS`、`IMAGE_SEARCH_CACHE_MAX`：默认 30 天 / 500 项，天数 0 关闭缓存。
  缓存键包含图片内容、引擎及结果配置；任何引擎失败时不缓存，避免网络故障长期遮住恢复结果。
- `IMAGE_SEARCH_PUBLIC_UPLOAD`：默认 false；开启后可把无公网 URL 的图片上传至 Litterbox，
  保存 1 小时，供必应、Yandex、谷歌 URL 搜图使用。未开启时这些引擎会说明缺少公网 URL。

反查需要向第三方引擎发送图片；临时图床须显式启用。
图片最多 15 MB / 4000 万像素，本地转为最长边 1600 的 JPEG，移除 EXIF 后上传。
URL 仅接受公开 HTTP(S) 标准端口，拒绝本机/内网地址，逐跳检查重定向；上传域名写死在代码中。
不会把 QQ 私有图片 URL 自动作为公共 URL 转交 URL 型引擎。

`data/image_search/state.json` 存放配额和缓存；`data/image_search/browser/` 存放独立浏览器资料。
以上目录均被 Git 忽略。浏览器首次需要时启动并复用，NoneBot 关闭时回收本插件创建的浏览器。
使用仓库通常的 Chromium 安装步骤即可；无需运行原插件安装脚本。

## 验证

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_image_search.py
.venv/Scripts/ruff.exe check --no-cache src/kanamibot/plugins/image_search tests/test_image_search.py
```
