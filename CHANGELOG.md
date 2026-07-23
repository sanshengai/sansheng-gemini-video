# Changelog

本项目的变更记录。版本号遵循 [semver](https://semver.org/lang/zh-CN/)。

## [未发布]

**新增在线视频整链条：**

- 公共 YouTube URL 继续由 Gemini 原生直读，不下载。
- B站、小红书、抖音、TikTok 与其他网页视频默认用 `yt-dlp` 下载到临时目录，再交 Gemini；可显式传 Cookie，默认绝不读取浏览器登录态。
- `yt-dlp` 失败时，仅在已配置 `AI_DOUYIN_API_KEY` 的情况下，对支持的短视频平台启用解析代理降级。
- 微信视频号 `weixin.qq.com/sph/...` 接入本机 `wx_channels_download` HTTP API，等待已解析、下载、解密文件落盘后继续分析；本地服务未初始化时给出明确根因。
- 新增 `source_resolver.py`、URL 路由单元测试、下载保留选项、平台/解析器审计元数据与专项体检参数。

## [0.1.1] -- 2026-07-08

**修复**:配置读取改为「环境变量 → `.env` → 默认」优先级。

此前 `.env.example` 承诺可以把 `GEMINI_VERTEX_PROJECT`(以及 model / location / inline 上限)写进 `.env`,但代码只从 shell 环境变量读取,导致填进 `.env` 不生效 -- Vertex Express(`AQ.` key)用户即使在 `.env` 填了项目 ID,仍会报"没设 GEMINI_VERTEX_PROJECT"。现在一个 `.env` 即可配齐 key + 项目 + 调优项,`.env.example` 的承诺与代码一致。

无破坏性变更。AI Studio(`AIza`)端点路径仍待更多联网实测(见 README caveat)。

## [0.1.0] -- 2026-07-08

给 Claude Code 一双**看视频的眼睛**：把整段视频（本地录屏 / 本地视频 / YouTube）交给 Gemini 原生多模态（画面 + 音频 + 时序一起看），吐回结构化评估 JSON 供 Claude 决策，不再靠"抽静态帧瞎猜"。

**这一版包含：**
- 三意图：understand（看懂一段视频）/ screening（批量文件夹逐段验收）/ reverse（视听逆向，出逐镜头 shotTimeline）
- 三类输入：本地视频 inline base64、YouTube 原生 URL、批量文件夹
- 结构化输出（responseSchema）+ 成本控制（mediaResolution / fps 裁剪 / 起止裁剪）+ 超上限自动 ffmpeg 压缩
- 端点按 key 前缀自动分流：Vertex Express（`AQ.`）/ AI Studio（`AIza`）

字段契约、装法与 setup 自检见 README。这是叁笙做视频工作流时磨出来、清洗脱敏后开源的 Claude Code 技能。

[0.1.1]: https://github.com/sandypoli-boop/sansheng-gemini-video/releases/tag/v0.1.1
[0.1.0]: https://github.com/sandypoli-boop/sansheng-gemini-video/releases/tag/v0.1.0
