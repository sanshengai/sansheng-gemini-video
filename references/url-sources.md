# 在线视频 URL 路由

处理非 YouTube URL、配置 Cookie/解析代理，或下载失败时读本页。

## 决策顺序

1. 公共 YouTube URL -- 直接交 Gemini，不下载。
2. 微信视频号 URL -- 优先用一次性保存的元宝 Web Cookie 在后台换取直链并下载；不启动微信、不改代理。
3. 元宝 Cookie 未配置/失效，但用户已经自行保持 `wx_channels_download` 本地前端连接 -- 才调用本地 API 兜底。
4. 其他 URL -- 先 `yt-dlp`。
5. B站/小红书/抖音/TikTok 的 `yt-dlp` 主路失败，且用户已配置 `AI_DOUYIN_API_KEY` -- 调解析代理拿候选，再流式下载。
6. 全部失败 -- 汇总真实失败原因并停止；不要退回抽帧、录屏猜测或假称已解析。

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

上述 `--cookies` 只供 `yt-dlp`。微信视频号使用独立的元宝授权，不会读取日常浏览器 Cookie：

```powershell
python scripts/wechat_auth.py
python scripts/wechat_auth.py --status
```

Windows 默认把 Cookie 用当前用户 DPAPI 加密到 `%LOCALAPPDATA%\sansheng-gemini-video\yuanbao-cookie.dpapi`。凭证只在当前用户内存中解密，不进命令行、日志、分析 JSON 或 Git 仓库；失效后才需要重新运行一次授权命令。

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

### 默认后台链（推荐）

当前适配器参考 <https://github.com/ltaoo/wx_channels_download> v260531 的公开 `parse_sph` 协议，直接在本机内存中完成两段请求：

1. 带用户自己保存的元宝 Web Cookie 调 `yuanbao.tencent.com/api/weixin/get_parse_result`，把 `/sph/` 分享链接换成 `token/eid`。
2. 调 `channels.weixin.qq.com/finder-preview/api/feed/get_feed_info` 获取腾讯 CDN 视频直链。
3. 下载到临时目录，交 Gemini 分析，完成后清理。

该链不启动微信、不打开浏览器、不安装证书、不修改系统代理。首次使用时，用户在元宝 Web 登录后，从浏览器开发者工具复制任一元宝请求的完整 `Cookie` 请求头值，再运行 `wechat_auth.py` 隐藏粘贴一次。脚本绝不自动读取浏览器资料。

```powershell
python scripts/wechat_auth.py
python scripts/analyze_video.py "https://weixin.qq.com/sph/..."
```

也可在 CI/非 Windows 环境显式配置 `GEMINI_VIDEO_YUANBAO_COOKIE` 或 `GEMINI_VIDEO_YUANBAO_COOKIE_FILE`；两者都不得提交到仓库。

这不是腾讯面向第三方承诺的公开下载 API，没有 SLA；Cookie 会过期，元宝 Web 请求头/接口也可能改版。401/403 时停止并提示更新授权，绝不自动弹登录框或静默切到公共解析站。

### 已就绪的桌面链（末级兜底）

仅当用户已经自行启动并连接 <https://github.com/ltaoo/wx_channels_download> 本地前端时，`auto` 才会把它作为兜底；Skill 自己不会启动微信、代理或下载器。可用接口：

- 默认地址：`http://127.0.0.1:2022`
- 创建任务：`POST /api/task/create_channels`
- 请求体：`{"url":"<分享链接>","mp3":false,"cover":false}`
- 成功后等待 API 返回的绝对 `file_path` 落盘并稳定，再交 Gemini。

```dotenv
GEMINI_VIDEO_WECHAT_API_BASE=http://127.0.0.1:2022
```

```powershell
python scripts/setup_check.py --require-wechat
python scripts/analyze_video.py "https://weixin.qq.com/sph/..." --download-provider wechat-local
```

### 为什么桌面链不自动启动

桌面上游会触及微信会话、根证书和系统代理，可能抢焦点并影响用户正在进行的工作。因此它只接受用户显式准备好的既有连接，不得由 Skill 自动拉起、自动登录或修改系统网络设置。

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
| `ltaoo/wx_channels_download` | 参考公开 SPH HTTP 协议；可选调用本地 API | 保留来源说明，不捆绑其源码/二进制；上游为 MIT + Commons Clause 条件 |
| `JoeanAmier/XHS-Downloader` | 仅作能力对照，未集成 | GPL-3.0，避免把其实现复制进本 MIT 仓 |
