# sansheng-gemini-video · 给 Claude 一双看视频的眼睛

> 把整段视频交给 Gemini 原生多模态,吐回一份结构化的评估 JSON。

**中文** | [English](./README_EN.md)

## 这是什么

Claude 本身看不了视频,只能从抽出来的几帧里猜。这个 skill 把**整段视频**(画面 + 声音 + 时序)一次性交给 Gemini,返回**机器可读的 JSON**:视频里发生了什么、这段素材达不达标、或者一个创作者的视觉 / 音频手法怎么拆。

它是一个带**视频源解析**的感知层:YouTube 由 Gemini 免下载直读;B站 / 小红书 / 抖音等链接在需要时自动下载;微信视频号可接本地下载解密服务。它不剪辑、不发布,只负责把可用媒体交给 Gemini "看见"。

## 它吐出什么样的东西 —— 先看产出

它不是有界面的工具,产出就是 JSON。三种意图,一个内核 —— 下面几个样例的**字段结构取自真实输出契约**(见 [`references/integration.md`](references/integration.md)),数值为便于理解的示意:

### ① understand —— 看懂一段视频

返回整体摘要 + 一条**时间线**(时间码 / 画面 / 屏上文字)+ UI 元素 + 问题清单。时间线长这样:

| 时间码 | 画面 | 屏上文字 |
|---|---|---|
| 00:00 | 主讲出镜,中景,背景虚化书架 | 标题卡:"3 分钟看懂 X" |
| 00:05 | 切屏幕录制,鼠标点开一个功能面板 | "第一步" |
| 00:12 | 回到出镜,手势强调,下三分之一字幕 | "重点在这" |

### ② screening —— 按验收标准给素材打分

拿一条素材对着你的评分标准(rubric)逐条打分:每条给**证据 + 判定 + 分数**,再汇总 `usable` / `overall_score` / `suggested_action`。`batch_eval.py` 跑一整个文件夹,直接给你一张选片表:

| 文件 | 可用? | 总分 | 建议 |
|---|---|---|---|
| clip_01.mp4 | ✅ | 8.5 | 保留 —— 画面稳、收音干净 |
| clip_02.mp4 | ⚠️ | 5.0 | 可用但需裁 —— 前 3 秒晃 |
| clip_03.mp4 | ❌ | 2.5 | 重拍 —— 过曝、跳帧 |

### ③ reverse —— 逆向拆解创作者的视觉 / 音频手法

供学习 / 找灵感用,拆 `visualCraft`(镜头时间线、调色、节奏、招牌动作)和 `audioCraft`(BGM、音效、语调、卡点),外加"内容信号 → 战术"的跨维对照:

```jsonc
{
  "summary": "...",
  "visualCraft": {
    "shotTimeline": [
      {"timecode": "00:03", "shot": "画面上是什么(讲话 ↔ 视觉)", "technique": "转场 / 运镜 / 下三分之一 / 分屏 / 变焦 / B-roll", "on_screen_text": "..."}
    ],
    "colorGrading": "色板 / 调色",
    "rhythm": "快切 / 留白 / 卡点",
    "signatureMoves": ["招牌视觉习惯"]
  },
  "audioCraft": {
    "bgm": "音乐风格 / 情绪 / 有无",
    "sfx": [{"timecode": "00:05", "desc": "音效"}],
    "voiceProsody": "语速 / 停顿 / 重音",
    "syncPoints": [{"timecode": "00:08", "what": "卡在鼓点 / 重音峰"}]
  },
  "crossDimSignals": [
    {"timecode": "00:03", "contentSignal": "制造反差", "visualTactic": "快速变焦 + 红框", "audioTactic": "鼓点"}
  ]
}
```

> 字段顺序刻意把观察(`shot`)排在判断(`technique`)前面 —— Gemini 按字段顺序生成,先记录"看到什么"再下"这是什么手法"的结论。

每次结果都带一个 `usage` 块(token / 估算成本 / 秒数)。key 只从环境变量 / `.env` 读,从不打印;报错信息里的 `?key=` 会被抹成 `***`。

## 什么时候用

对 Claude 说 *"分析这个视频"* *"分析这个录屏"* *"这段视频能不能用"* *"把这个文件夹的素材批量筛一遍"*,或直接丢一个本地视频 / YouTube / B站 / 小红书 / 抖音 / 微信视频号链接问里面是什么 —— Claude 接起这个 skill 跑 `analyze_video.py`。

**不适合**:只下载不分析、只要字幕 / 文字摘要(用字幕工具)、剪辑 / 出片。

## 安装

作为 Claude Code plugin(推荐):

```bash
claude plugin marketplace add sandypoli-boop/sansheng-gemini-video
claude plugin install sansheng-gemini-video
```

或手动:clone 后软链进 `~/.claude/skills/`:

```bash
git clone https://github.com/sandypoli-boop/sansheng-gemini-video.git
ln -s "$PWD/sansheng-gemini-video" ~/.claude/skills/sansheng-gemini-video
```

然后重启 Claude Code。

### 国内加速下载

GitHub 直连不畅时，给 clone 地址前面加一层公共镜像即可（下载源码 zip 同理）：

```bash
# 加速 clone（把 gh-proxy.com 换成 ghfast.top 即备用镜像）
git clone https://gh-proxy.com/https://github.com/sandypoli-boop/sansheng-gemini-video.git
```

插件市场方式暂无稳定国内镜像；网络不畅时用上面的加速 clone + 软链。

## 更新

升级到新版，取决于你当初怎么装的：

- **插件市场装的**：`claude plugin marketplace update` 刷新市场，再 `claude plugin update sansheng-gemini-video`
- **clone + 软链装的**：进本仓目录 `git pull`（软链即时生效，不必重装、不必重连）

**怎么知道有新版**：看本仓 [Releases](../../releases)；点仓库右上角 **Watch → Custom → Releases**，发新版时 GitHub 会通知你。每版改了什么见 [CHANGELOG](CHANGELOG.md)。

## 快速上手

```bash
pip install requests            # 唯一必需第三方依赖(裸 REST,不用 SDK)
# ffmpeg 可选 —— 只在需要自动压缩超大本地视频时用
# yt-dlp 可选 —— 分析 B站/小红书/抖音等非 YouTube 链接时用
pip install -U yt-dlp
cp .env.example .env            # 然后填 key(见下)
python scripts/setup_check.py   # 环境 / key 健康检查
python scripts/analyze_video.py "C:\path\to\clip.mp4"
python scripts/analyze_video.py "https://www.bilibili.com/video/BV..."
```

在线链接路由、Cookie 护栏和微信视频号本地服务要求见 [`references/url-sources.md`](references/url-sources.md)。默认下载到临时目录并在分析后清理;用 `--download-dir` 或 `--keep-download` 才保留。

## 我该用哪种 key

脚本按前缀**自动识别** key,不用手动切:

| Key | 前缀 | 端点 | 额外设置 |
|-----|------|------|---------|
| **AI Studio key**(大多数人) | `AIza…` | `generativelanguage.googleapis.com` | 无 —— 设好 key 即可 |
| **Vertex AI Express key** | `AQ.…` | Vertex(`aiplatform.googleapis.com`) | 另需设 `GEMINI_VERTEX_PROJECT` |

在环境变量或 `.env` 里设 `GOOGLE_API_KEY`(或 `GEMINI_API_KEY`)。默认模型 `gemini-3.5-flash` 若你的 key 不可用,设 `GEMINI_MODEL` 换一个。见 [`.env.example`](.env.example)。

> **AI Studio 路径提醒**:Vertex Express(`AQ.`)路径经过实战验证。AI Studio(`AIza`)路径已实现并做了单元检查(端点分发 + key 加载),但**还没跑过一条真实视频的端到端**。用 AI Studio key 遇到问题,请[提 issue](../../issues),并试着把 `GEMINI_MODEL` 换成你的 key 能调的模型。

## 成本控制

视频 token = 帧数(fps × 时长)× 每帧 token(取决于分辨率)。最大的杠杆是 **fps**(录屏 0.2-0.5 就够);默认分辨率 `low`(只有要读屏上小字才调 `high`)。长视频 / YouTube 一定传 `--start/--end` + 低 `--fps`。详见 [`references/visual-grounding-limits.md`](references/visual-grounding-limits.md)。

## 编程调用

`analyze_one()` 可被 import;默认临时下载与压缩文件都会在 `finally` 清理。高 token 分析建议放进子 agent 跑,只回摘要。接口 + `reverse` 输出契约在 [`references/integration.md`](references/integration.md)。

## 致谢 · Credits

依赖健康检查(`setup_check.py`,静默 `--check` + 分级退出码)的设计借鉴自 **[bradautomates/claude-video](https://github.com/bradautomates/claude-video)**(MIT);短视频解析代理降级路径参考 **[imlewc/video-to-subtitle-summary-skill](https://github.com/imlewc/video-to-subtitle-summary-skill)**(MIT)。微信视频号通过 **[ltaoo/wx_channels_download](https://github.com/ltaoo/wx_channels_download)** 的本地 HTTP API 接棒,不复制其源码。

本 skill 仅有一个必需第三方运行依赖 `requests`(裸 REST 调 Gemini,不捆绑 SDK);`ffmpeg` 为可选(压缩超大视频),`yt-dlp` 为非 YouTube URL 下载主路。微信视频号上游需用户单独安装并初始化;各项目保留自身许可,本仓不捆绑其代码。

## 配套文章 · Article

配套讲解的公众号文章即将发布,发布后补上链接。

## 关于作者 · About the author

<p align="center">
  <a href="https://sanshengai.top"><strong>🌐 网站 sanshengai.top</strong></a> ·
  <a href="https://namecard.xiaoyuzhoufm.com/nnl8x"><strong>🎧 小宇宙</strong></a> ·
  <a href="https://weibo.com/u/7546221967"><strong>微博</strong></a> ·
  <a href="https://www.xiaohongshu.com/user/profile/5c716b6d000000001000f5c4"><strong>小红书</strong></a> ·
  <a href="mailto:sandypoli@gmail.com"><strong>✉️ 邮箱</strong></a>
</p>

我是**叁笙**,用 AI 做内容、也用 AI 造工具。这个 skill 是我做个人站「[叁笙早安 AI](https://sanshengai.top)」的内容时,在真实工作流里一点点磨出来、再清洗脱敏开源的。觉得有用,欢迎来[网站](https://sanshengai.top)逛逛,或**扫码关注公众号「叁笙早安AI」**(公众号没有跳转链接,扫码最快):

<p align="center">
  <img src="assets/qrcode-gongzhonghao.png" alt="微信公众号 叁笙早安AI" width="200">
  <br><sub>微信扫码关注 · 叁笙早安AI</sub>
</p>

## License

[MIT](LICENSE) © 2026 叁笙 (sansheng)
