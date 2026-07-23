# 在线视频 URL 路由

处理非 YouTube URL、配置 Cookie/解析代理，或下载失败时读本页。

## 决策顺序

1. 公共 YouTube URL -- 直接交 Gemini，不下载。
2. 微信视频号 URL -- 调本地 `wx_channels_download` API；它负责解析、下载、解密。
3. 其他 URL -- 先 `yt-dlp`。
4. B站/小红书/抖音/TikTok 的 `yt-dlp` 主路失败，且用户已配置 `AI_DOUYIN_API_KEY` -- 调解析代理拿候选，再流式下载。
5. 全部失败 -- 汇总真实失败原因并停止；不要退回抽帧、录屏猜测或假称已解析。

## 为什么只有 YouTube 免下载

Gemini 官方视频理解文档列出的 URL 输入是“公共 YouTube URL”；普通外部视频 URL 不属于该承诺。其他平台必须先变成 Gemini 可读的文件，再用 inline/File API/GCS 路径分析。

官方依据：<https://ai.google.dev/gemini-api/docs/video-understanding>

## yt-dlp 主路

本机需有 `yt-dlp`；脚本只下载单条视频，不展开播放列表。若系统存在 ffmpeg，会要求合并成 MP4。

```powershell
python scripts/setup_check.py --require-url-download
python scripts/analyze_video.py "<URL>"
```

平台常见失败不是同一种根因：

| 症状 | 常见根因 | 处理 |
|---|---|---|
| B站 HTTP 412 | 风控/WBI/登录态 | 更新 `yt-dlp`；确需登录内容时由用户显式传 Cookie |
| 小红书 `No video formats` | 笔记是图文、分享 token 过期或需登录 | 先确认是视频笔记；刷新分享链接；再考虑显式 Cookie/代理降级 |
| 短链只跳首页 | 平台改了短链格式或 token 已失效 | 获取新的分享链接，不把首页当视频 |
| 下载成功但无文件 | 提取器/合并异常 | 查看完整 stderr；不要只看退出码 0 |

`yt-dlp` 的 Bilibili 与 XiaoHongShu 提取器会随平台变化持续更新；支持列表与当前实现以其仓库为准：

- <https://github.com/yt-dlp/yt-dlp>
- <https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py>

## Cookie 护栏

默认不读取浏览器 Cookie。只有用户明确要求使用登录态时才传：

```powershell
python scripts/analyze_video.py "<URL>" --cookies "C:\绝对路径\cookies.txt"
python scripts/analyze_video.py "<URL>" --cookies-from-browser chrome
```

- `--cookies` 必须是存在的绝对路径。
- 不把 Cookie 内容写进日志、JSON 或回复。
- 不默认读浏览器资料，不在失败后自动尝试所有浏览器。
- 会员、私密、地区限制内容仍受账号权限与平台规则约束。

## AI Douyin 降级

只在用户已配置 `AI_DOUYIN_API_KEY` 时启用；这意味着对应视频链接会发送到该第三方解析服务。未配置时 `auto` 路由不会偷偷调用。

```dotenv
AI_DOUYIN_API_KEY=
AI_DOUYIN_API_BASE=https://ai-douyin.top9.cc
```

该路径参考 MIT 项目 <https://github.com/imlewc/video-to-subtitle-summary-skill>。解析服务可能计费、限流或改变接口；`yt-dlp` 仍是默认主路。

## 微信视频号

### 底层约束

`weixin.qq.com/sph/...` 不是普通公开 MP4。详情接口可能返回带 `decodeKey/urlToken` 的加密媒体，必须经过视频号专用下载/解密链。通用 `yt-dlp`、普通 HTTP 下载和 Gemini URL 输入都不能替代这一步。

当前适配器调用 <https://github.com/ltaoo/wx_channels_download> 的本地 API：

- 默认地址：`http://127.0.0.1:2022`
- 创建任务：`POST /api/task/create/channels`
- 请求体：`{"url":"<分享链接>","mp3":false,"cover":false}`
- 成功后等待 API 返回的绝对 `file_path` 落盘并稳定，再交 Gemini。

```dotenv
GEMINI_VIDEO_WECHAT_API_BASE=http://127.0.0.1:2022
```

```powershell
python scripts/setup_check.py --require-wechat
python scripts/analyze_video.py "https://weixin.qq.com/sph/..."
```

### 为什么不自动安装

该上游需要安装/初始化微信客户端相关能力，部分模式涉及本机证书、代理或登录态。它们会改变系统网络环境或触及账号会话，不能由本 skill 静默安装。用户完成上游初始化后，本 skill 负责从“链接”到“已解密文件”再到“Gemini 分析”的自动接棒。

上游 2026-05 的 issue 已验证 `/sph/` 新格式可通过详情接口识别，但拿到的仍可能是加密链接，后来版本才补齐对应处理：<https://github.com/ltaoo/wx_channels_download/issues/402>

## 文件保留

- 默认：下载进系统临时目录，分析完成后清理。
- `--download-dir <绝对目录>`：保留在指定目录。
- `--keep-download`：未指定目录时保留到当前目录 `_video_downloads`。
- 微信本地服务下载到其自管目录：本 skill 不删除外部服务管理的文件。

## 许可证边界

| 项目 | 用法 | 许可证处理 |
|---|---|---|
| `yt-dlp/yt-dlp` | 外部可执行依赖 | 不捆绑源码 |
| `imlewc/video-to-subtitle-summary-skill` | 参考解析候选/流式下载设计 | MIT，保留来源说明 |
| `ltaoo/wx_channels_download` | 调本地 HTTP API | 不复制源码；上游为 MIT + Commons Clause 条件 |
| `JoeanAmier/XHS-Downloader` | 仅作能力对照，未集成 | GPL-3.0，避免把其实现复制进本 MIT 仓 |
