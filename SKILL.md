---
name: sansheng-gemini-video
description: Use when 用户需要理解本地视频、录屏或 YouTube 链接里发生的内容，或要做视频质检、素材筛选；触发词：分析视频、看懂视频、录屏质检、视频里发生了什么。只下载、只要字幕摘要或逆向博主打法时不用此 Skill。
compatibility: >
  Python 3.10+ + requests(唯一第三方依赖,走裸 REST,不需要 google-genai SDK);ffmpeg 可选
  (大文件自动压缩)。按 key 前缀自动分流两种后端:AI Studio key(AIza 前缀 -> generativelanguage
  端点,零额外配置)/ Vertex AI Express key(AQ. 前缀 -> Vertex 端点,需 env GEMINI_VERTEX_PROJECT)。
  key 读环境变量 GOOGLE_API_KEY / GEMINI_API_KEY,或 skill 目录 / cwd 的 .env。
metadata:
  author: 叁笙 (sansheng)
  version: "1.0"
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
---

# sansheng-gemini-video · Claude 的"视频之眼"

Claude 原生看不了视频,只能逐帧抽图瞎猜。本 skill 把这件事**交给真正能看视频的 Gemini**:
任何视频源整段交给 Gemini 原生多模态(画面 + 音频 + 时序一起理解),吐回**结构化评估 JSON**
给 Claude 决策。

**核心定位**:底层视觉/音频**感知能力层**。不做编排、不做下载、不出成片。只回答"这段视频里
到底发生了什么、符不符合要求",把答案结构化交给上层(你 / 你自己的编排脚本)决策。

## 两种后端,按 key 自动分流(零手动切换)

`analyze_video.py` 按 key 前缀自动选端点,请求体两种后端完全一致:

| key 类型 | 前缀 | 端点 | 额外配置 |
|---|---|---|---|
| **AI Studio key**(多数人用这个) | `AIza…` | `generativelanguage.googleapis.com` | 无,拿到 key 即用 |
| **Vertex AI Express key** | `AQ.…` | `aiplatform.googleapis.com`(Vertex) | env `GEMINI_VERTEX_PROJECT=<你的 GCP 项目 ID>` |

> Express key 走 google-genai **SDK** 会报 "API keys not supported, need OAuth";本 skill 一律走
> 裸 REST + `?key=`,两种 key 都稳。模型默认 `gemini-3.5-flash`,按你的 key 能用的模型用
> env `GEMINI_MODEL` 覆盖。配置见 `.env.example`。

## 三条铁律

1. **只感知,不编排** -- 不下载(交 yt-dlp)、不剪辑、不发布。
2. **原生看整段,绝不退回抽帧让 Claude 猜** -- 本地走 inline、YouTube 走原生 URL,都把整段视频
   喂给 Gemini,保留运动与时序。
3. **输出是给机器接棒的结构化 JSON,不是给人看的散文**。

## 它能看什么 -- 三类输入,一套内核

| 输入 | 怎么喂 Gemini | 要下载吗 | 裁片段 |
|------|---------------|----------|--------|
| 本地录屏 / 本地视频文件 | inline base64(源文件 < 上限);超了自动/手动 ffmpeg 压缩 | 已是本地 | ✅ `--start/--end` |
| 本地音频文件(mp3/wav/m4a/aac/ogg/flac/aiff/wma) | inline base64;Gemini 原生听音频 | 已是本地 | ✅ `--start/--end` |
| YouTube URL | 原生 `fileData.fileUri`,在线看免下载(取决于你的 key/模型是否支持) | ❌ | ✅ |

> inline 上限按**源文件体积**判(默认 70MB,env `GEMINI_INLINE_MAX_MB` 可调),非 base64 膨胀后的体积。
> 音频走纯音频压缩档;其他直链(Vimeo/B站等)请先用 yt-dlp 下载到本地再传。

## 快速开始(子命令)

| 子命令 | 等效操作 |
|--------|----------|
| `分析视频 <路径或YouTube链接>` | 单视频通用理解 -> `python scripts/analyze_video.py <源>` |
| `视频素材筛选 <路径> [--rubric 标准文件]` | 单视频按验收标准打分 -> `analyze_video.py <源> --intent screening` |
| `批量验收 <文件夹>` | 一个文件夹逐段验收 + 汇总选用决策(**头号用例**)-> `scripts/batch_eval.py <文件夹>` |
| `逆向拆解 <路径或YouTube链接>` | 拆⑤视觉⑥音频手法 -> `analyze_video.py <源> --intent reverse`(契约见 `references/integration.md`) |
| `依赖体检` | `python scripts/setup_check.py` |

## 命令行接口

```
python scripts/analyze_video.py <本地绝对路径 | YouTube链接>
       [--intent understand|screening|reverse]  # 默认 understand
       [--rubric <每行一条标准的txt>]      # screening 用,留空走内置通用录屏验收档
       [--prompt "自定义指令"]             # 自定义指令(配 --schema 可结构化;单给走自由文本)
       [--schema <responseSchema.json>]   # 配合 --prompt 强制结构化输出,调用方定义自己要的结构
       [--start 1:30] [--end 2:45]        # 只看片段,砍 token
       [--fps 0.5]                        # 采样帧率,录屏建议 0.2-0.5
       [--media-resolution low|high]      # 默认 low
       [--no-compress]                    # 超 inline 上限不自动压缩,报错给手动命令(默认自动 ffmpeg 压)
       [--model <id>] [--out <json路径>]

python scripts/batch_eval.py <文件夹> [--rubric ...] [--pattern "*.mp4"] [--out-dir ...]
```

## 输出 JSON(结构化评估)

- **understand** 出 `summary + timeline(timecode/visual/on_screen_text) + ui_elements + issues`。
- **screening** 出 `summary + criteria[criterion/evidence/verdict/score] + usable + overall_score + top_issues + suggested_action`。
- **reverse** 出 `summary + visualCraft(shotTimeline/colorGrading/rhythm/signatureMoves) + audioCraft(bgm/sfx/voiceProsody/syncPoints) + crossDimSignals` -- 拆一个视频创作者的视觉/音频手法(契约见 [`references/integration.md`](references/integration.md))。

字段顺序刻意让 `evidence/shot` 等观察在 `verdict/technique` 判断之前 -- Gemini 按字段顺序生成,
先落证据再下判断 = 强制先看后判。每份带 `usage`(token/成本/耗时);本地超 inline 上限的源会被
自动压缩,`source.compressed` 标记。`batch_eval` 额外在 `<文件夹>/_eval/` 落每段 `.eval.json` +
`_summary.json`,并打印汇总表,交给 Claude 做横向选用决策。

## 成本与分辨率控制(关键)

视频 token = 帧数(fps × 时长)× 每帧 token(media_resolution 定)。

- **视频默认 `low` 分辨率**;仅"读屏幕小字/代码/密钥前缀"且源 ≥720p 才升 `high`(见 `references/visual-grounding-limits.md`)。
- **先降 fps 是最大省钱杠杆**:录屏 0.2-0.5、口播 1.0;高 fps 只配 `--start/--end` 用在短片段。
- **YouTube/长视频默认全量很贵**,务必加 `--start/--end` 裁剪 + 低 fps。
- **大文件超 inline 上限自动压缩**:有 ffmpeg 时超上限自动转码(720p/2fps -> 仍超则 480p/1fps,临时文件用完即删);无 ffmpeg 或加 `--no-compress` 才退回手动命令提示。

## 错误处理与可靠性

- HTTP 429/500/503 自动退避重试 3 次;其他错误返回清晰原因。
- 结构化解析失败回退原始文本 + `warnings`,不裸崩。
- `batch_eval` 单段失败记录 error 继续跑,不中断整批。
- 本地路径强制绝对路径;超 inline 上限给压缩命令提示。
- key **只从 env / `.env` 读,绝不打印**;错误信息里 `?key=` 一律 redact 成 `?key=***`。

## 程序化调用 / 集成

`analyze_one()` 幂等、无副作用,可从你自己的代码 import 复用;高 token 视频输出建议用子 agent
隔离(agent 内跑分析、只回摘要,不把整包 base64 / 长 JSON 灌回主上下文)。接口与 `reverse`
输出契约见 [`references/integration.md`](references/integration.md)。

## When NOT to Use

- 只下载不分析 -> `yt-dlp`;只要字幕 / 纯文字摘要 -> 用字幕/摘要类工具。
- 需要**精确自动打码/像素级裁剪**:Gemini 坐标是**区域级**准、像素级有偏差,不能盲信自动打码 --
  必须按 `references/visual-grounding-limits.md §1` 加一道抽帧人工核像素。

## 运行时约定

- 破折号一律用 `--`。中文为主。
- 模型名单一来源 env `GEMINI_MODEL`,缺省 `gemini-3.5-flash`(按你的 key 能用的模型设)。
- key 走 env `GOOGLE_API_KEY` / `GEMINI_API_KEY`,或 skill 目录 / cwd 的 `.env`(拷 `.env.example`)。

## Credits

依赖体检脚本(`setup_check.py`)的 `--check` 分级退出码设计借鉴自
[bradautomates/claude-video](https://github.com/bradautomates/claude-video)(MIT)。

## 文件索引

| 文件 | 作用 |
|------|------|
| `scripts/analyze_video.py` | 单视频核心(understand/screening/reverse,`analyze_one` 可 import,本地超限自动压缩) |
| `scripts/batch_eval.py` | 批量文件夹逐段验收 + 汇总 |
| `scripts/setup_check.py` | 依赖 / key 体检(`--check` 静默 + 分级退出码) |
| `references/integration.md` | 程序化调用接口 + `reverse` 输出契约 |
| `references/visual-grounding-limits.md` | 视觉定位坐标能力边界 + media_resolution/fps 指引(做定位/打码前必读) |
| `.env.example` | 环境变量样例(拷成 `.env` 填自己的 key) |
