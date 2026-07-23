---
name: sansheng-gemini-video
description: 用 Gemini 原生多模态理解本地视频、音频和在线视频链接，联合分析画面、声音与时序；支持 YouTube 免下载直读，并在需要时自动下载 B站、小红书、抖音等链接，或用一次性元宝授权在后台解析微信视频号。用户说“分析视频、看懂视频、录屏质检、视频链接解析、批量验收、逆向视觉音频手法”时使用；只要字幕、只下载不分析或要剪辑出片时不用。
---

# sansheng-gemini-video · 视频之眼

把整段视频交给 Gemini 原生多模态处理，保留画面、声音和时间关系，输出结构化 JSON。不要退回“抽几帧让 Agent 猜”的弱路径。

## 核心路由

先判断输入，再执行最短可行路径：

| 输入 | 路由 | 是否下载 |
|---|---|---|
| 本地视频/音频绝对路径 | inline base64；超上限时 ffmpeg 自动压缩 | 已在本地 |
| 公共 YouTube URL | Gemini 原生 `fileData.fileUri` | 否 |
| B站/小红书/抖音/TikTok/其他网页视频 | `yt-dlp` 下载到临时目录，再 inline 分析 | 是 |
| 上述短视频平台且 `yt-dlp` 失败 | 仅在已配置 `AI_DOUYIN_API_KEY` 时调用解析代理，再下载 | 是 |
| 微信视频号 `weixin.qq.com/sph/...` | 一次性元宝 Cookie 授权后纯后台换直链、下载、分析；已就绪的本地服务仅作兜底 | 是 |

下载到临时目录的文件在分析后自动清理；传 `--download-dir` 或 `--keep-download` 才保留。微信本地服务管理的下载文件不自动删除。

处理非 YouTube URL、配置 Cookie/代理，或排查下载失败时，先读 [references/url-sources.md](references/url-sources.md)。

## 三条硬规则

1. **能直读就不下载** -- Gemini 官方只承诺公共 YouTube URL 可直接作为视频输入；其他网页 URL 不冒充可直读。
2. **整段原生理解** -- 下载只是取材，分析仍把完整媒体交给 Gemini，不以抽帧替代视听时序。
3. **凭证必须显式** -- 默认不读取浏览器 Cookie。微信授权只由用户运行 `wechat_auth.py` 隐藏粘贴一次并用 Windows DPAPI 保存；失效只提示，不自动打开微信/浏览器。配置 `AI_DOUYIN_API_KEY` 视为允许把对应短视频链接发送给该解析服务。

## 快速命令

| 用户意图 | 命令 |
|---|---|
| 分析一个本地文件或 URL | `python scripts/analyze_video.py <源>` |
| 素材筛选 | `python scripts/analyze_video.py <源> --intent screening` |
| 逆向视觉/音频手法 | `python scripts/analyze_video.py <源> --intent reverse` |
| 自定义结构化分析 | `python scripts/analyze_video.py <源> --prompt "..." --schema schema.json` |
| 批量验收本地文件夹 | `python scripts/batch_eval.py <文件夹>` |
| 仅诊断 URL 下载 | `python scripts/source_resolver.py <URL> --download-dir <绝对目录>` |
| 一次性微信后台授权 | `python scripts/wechat_auth.py` |
| 基础体检 | `python scripts/setup_check.py` |
| 要求通用 URL 下载就绪 | `python scripts/setup_check.py --require-url-download` |
| 要求微信视频号链路就绪 | `python scripts/setup_check.py --require-wechat` |

### 完整 CLI

```text
python scripts/analyze_video.py <本地绝对路径|在线视频URL>
  [--intent understand|screening|reverse]
  [--rubric <每行一条标准的txt>]
  [--prompt "自定义指令"] [--schema <responseSchema.json>]
  [--start 1:30] [--end 2:45] [--fps 0.5]
  [--media-resolution low|high] [--no-compress]
  [--download-provider auto|yt-dlp|ai-douyin|wechat-yuanbao|wechat-local]
  [--download-dir <绝对目录>] [--keep-download]
  [--cookies <绝对路径>] [--cookies-from-browser chrome]
  [--download-timeout 600] [--model <id>] [--out <json路径>]
```

## 输出契约

- `understand`：`summary + timeline(timecode/visual/on_screen_text) + ui_elements + issues`。
- `screening`：`criteria[evidence → verdict → score] + usable + overall_score + top_issues + suggested_action`。
- `reverse`：`visualCraft + audioCraft + crossDimSignals`；详细契约见 [references/integration.md](references/integration.md)。
- `source` 会记录 `route`、`platform`、`resolver`、是否压缩及下载体积，方便审计实际走了哪条链。
- 每份结果带 `usage`：token、估算成本和耗时。

字段顺序刻意让 `evidence/shot` 等观察先于 `verdict/technique`，强制先落证据再判断。

## 分辨率与成本

- 默认 `media-resolution=low`；仅需读屏幕小字且源 ≥720p 时升 `high`。
- 录屏建议 `fps=0.2-0.5`，口播建议 `fps=1.0`；长视频优先裁 `--start/--end`。
- 本地源超过 `GEMINI_INLINE_MAX_MB` 时，视频按 720p/2fps → 480p/1fps 梯次压缩；音频走独立码率梯次。
- 精确视觉定位、坐标或打码前必须读 [references/visual-grounding-limits.md](references/visual-grounding-limits.md)。

## 配置

脚本按“环境变量 → skill 目录 `.env` → 当前目录 `.env` → 默认值”读取：

- Gemini 必需：`GOOGLE_API_KEY` 或 `GEMINI_API_KEY`。
- Vertex Express：`GEMINI_VERTEX_PROJECT`，可选 `GEMINI_VERTEX_LOCATION`。
- 模型/inline 上限：`GEMINI_MODEL`、`GEMINI_INLINE_MAX_MB`。
- 短视频解析代理（可选降级）：`AI_DOUYIN_API_KEY`、`AI_DOUYIN_API_BASE`。
- 微信后台授权：推荐 `python scripts/wechat_auth.py`（Windows DPAPI）；CI/非 Windows 可显式用 `GEMINI_VIDEO_YUANBAO_COOKIE` / `GEMINI_VIDEO_YUANBAO_COOKIE_FILE`。
- 微信本地服务（仅末级兜底）：`GEMINI_VIDEO_WECHAT_API_BASE`，默认 `http://127.0.0.1:2022`。

不要打印、落盘或回传任何 key、Cookie、签名下载 URL。

## 可靠性

- Gemini HTTP 429/500/503 自动退避重试三次。
- `yt-dlp`、解析代理、元宝授权、微信本地服务的错误分别保留，不把“登录态不足”误报成“不支持平台”。
- 微信默认链全程后台；401/403 只提示更新授权，不自动弹窗、不改系统代理、不尝试把密文送给 Gemini。
- `batch_eval` 单段失败时记录 error 并继续，不中断整批。
- `analyze_one()` 可程序化调用；临时下载与压缩文件均在 `finally` 清理。

## 不适用

- 只下载、不分析：直接使用 `yt-dlp` 或平台下载器。
- 只要字幕/纯文字摘要：使用字幕或 ASR 工具。
- 剪辑、出片、发布：使用 `sandy-video`。
- 像素级自动打码：Gemini 只适合区域级定位，必须抽帧人工核验。

## 第三方边界与致谢

- `yt-dlp`：外部可执行依赖，不捆绑代码。
- 微信视频号后台链参考 `ltaoo/wx_channels_download` v260531 公开协议并保留来源说明；不捆绑其受 Commons Clause 附加条件约束的源码/二进制。本地 API 仅作可选兜底。
- AI Douyin 降级路径参考 MIT 项目 `imlewc/video-to-subtitle-summary-skill` 的“解析候选 → 流式下载”设计；本仓保留来源说明，不复制凭证。
- `setup_check.py` 的分级退出码思路借鉴 MIT 项目 `bradautomates/claude-video`。

## 文件索引

| 文件 | 作用 |
|---|---|
| `scripts/analyze_video.py` | URL/本地源 → Gemini 分析主入口 |
| `scripts/source_resolver.py` | URL 平台识别、下载路由、微信后台解析与本地兜底 |
| `scripts/wechat_auth.py` | 元宝 Cookie 一次性授权与 Windows DPAPI 凭证存储 |
| `scripts/batch_eval.py` | 本地文件夹逐段验收与汇总 |
| `scripts/setup_check.py` | Gemini、ffmpeg、yt-dlp、微信后台授权/本地兜底体检 |
| `references/url-sources.md` | URL 下载路由、Cookie 与平台限制 |
| `references/integration.md` | 程序化调用与输出契约 |
| `references/visual-grounding-limits.md` | 视觉定位能力边界 |
| `.env.example` | 配置样例 |
