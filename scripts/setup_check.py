#!/usr/bin/env python
"""依赖 / 凭证体检 -- 在跑 analyze_video.py 之前先确认环境就绪。

设计原则(借鉴 bradautomates/claude-video 的 setup --check):
- `--check` 模式成功时**零输出**(不刷屏),失败按缺什么给**分级 exit code**,
  方便上层脚本/Claude 用退出码判断,而不是解析文本。
- API key **只检测是否存在,绝不打印其值、绝不自动写盘** -- 缺了就提示用户自己设。
- Windows 友好:用 `python`(MS Store 的 `python3` 是 stub),用 shutil.which 查二进制。

Exit codes(--check 模式):
  0  全部就绪
  2  缺 requests(analyze_video.py 唯一第三方运行依赖;本 skill 走裸 REST,不用 google-genai SDK)
  3  缺 API key(与 analyze_video.load_key 同源:环境变量 GOOGLE_API_KEY / GEMINI_API_KEY,或 skill 目录 / cwd 的 .env)
  4  缺 ffmpeg/ffprobe(可选项,仅在 --require-ffmpeg 时算失败)
  5  Python 版本过低
  6  缺 yt-dlp(--require-url-download)
  7  微信视频号后台授权与本地兜底均不可用(--require-wechat)
"""
import argparse
import importlib.util
import os
import shutil
import socket
import sys
import urllib.parse
from pathlib import Path

from wechat_auth import load_cookie as load_saved_yuanbao_cookie

MIN_PY = (3, 10)
# 必须与 analyze_video.load_key 同源:运行时读 GOOGLE_API_KEY 或 GEMINI_API_KEY
# (AI Studio 的 AIza 或 Vertex Express 的 AQ. key 都可)。
KEY_NAMES = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
# 搜索路径与 load_key 一致:环境变量 + skill 目录 / cwd 的 .env(env 变量优先)。
KEY_ENV_PATHS = (
    Path(__file__).resolve().parent.parent / ".env",
    Path.cwd() / ".env",
)


def _has_key() -> bool:
    """检测 GOOGLE_API_KEY / GEMINI_API_KEY(与 analyze_video.load_key 同源):先看环境变量,
    再看 skill 目录 / cwd 的 .env(只读,绝不打印值)。"""
    if any(os.environ.get(n) for n in KEY_NAMES):
        return True
    for env_path in KEY_ENV_PATHS:
        try:
            if env_path.is_file():
                for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    s = line.strip()
                    for n in KEY_NAMES:
                        if s.startswith(n + "=") and s.split("=", 1)[1].strip().strip("\"'"):
                            return True
        except OSError:
            pass
    return False


def _env_value(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    for env_path in KEY_ENV_PATHS:
        try:
            if env_path.is_file():
                for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    s = line.strip()
                    if s.startswith(name + "="):
                        return s.split("=", 1)[1].strip().strip("\"'") or None
        except OSError:
            pass
    return None


def _wechat_api_ready() -> bool:
    """只探测本机端口，不向远程 resolver 发送请求。"""
    base = _env_value("GEMINI_VIDEO_WECHAT_API_BASE") or "http://127.0.0.1:2022"
    parsed = urllib.parse.urlparse(base)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False


def _yuanbao_cookie_ready() -> bool:
    if _env_value("GEMINI_VIDEO_YUANBAO_COOKIE"):
        return True
    cookie_file = _env_value("GEMINI_VIDEO_YUANBAO_COOKIE_FILE")
    if cookie_file:
        path = Path(cookie_file).expanduser()
        if path.is_absolute() and path.is_file():
            try:
                return bool(path.read_text(encoding="utf-8").strip())
            except OSError:
                return False
    return bool(load_saved_yuanbao_cookie())
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=0.3):
            return True
    except OSError:
        return False


def run_checks():
    """返回 (results, first_failure_code)。results 是 [(name, ok, hint)] 列表。"""
    results = []
    fail_code = 0

    py_ok = sys.version_info[:2] >= MIN_PY
    results.append(("Python >= 3.10", py_ok,
                    f"当前 {sys.version_info.major}.{sys.version_info.minor},请升级到 3.10+"))
    if not py_ok and not fail_code:
        fail_code = 5

    # 走裸 REST,唯一第三方运行依赖是 requests(不需要 google-genai SDK)
    requests_ok = importlib.util.find_spec("requests") is not None
    results.append(("requests(analyze_video 唯一第三方依赖)", requests_ok, "pip install requests"))
    if not requests_ok and not fail_code:
        fail_code = 2

    key_ok = _has_key()
    results.append(("Gemini API key(GOOGLE_API_KEY / GEMINI_API_KEY)", key_ok,
                    "设环境变量 GOOGLE_API_KEY(或 GEMINI_API_KEY)= 你的 AI Studio key(AIza 前缀)"
                    "或 Vertex Express key(AQ. 前缀);或写进 skill 目录 / cwd 的 .env。本脚本不会替你写入"))
    if not key_ok and not fail_code:
        fail_code = 3

    ffmpeg_ok = shutil.which("ffmpeg") is not None
    ffprobe_ok = shutil.which("ffprobe") is not None
    results.append(("ffmpeg(可选,预处理/机检)", ffmpeg_ok, "Windows: choco install ffmpeg"))
    results.append(("ffprobe(可选,读时长/分辨率)", ffprobe_ok, "随 ffmpeg 一起安装"))

    ytdlp_ok = shutil.which("yt-dlp") is not None or importlib.util.find_spec("yt_dlp") is not None
    results.append(("yt-dlp(非 YouTube 链接下载)", ytdlp_ok, "python -m pip install -U yt-dlp"))

    ai_douyin_ok = bool(_env_value("AI_DOUYIN_API_KEY"))
    results.append(("AI Douyin key(可选短视频平台降级)", ai_douyin_ok,
                    "仅需代理降级时配置 AI_DOUYIN_API_KEY;未配置不影响 yt-dlp 主路"))

    wechat_background_ok = _yuanbao_cookie_ready()
    wechat_local_ok = _wechat_api_ready()
    wechat_ok = wechat_background_ok or wechat_local_ok
    results.append(("微信视频号后台授权/本地兜底", wechat_ok,
                    "推荐运行 python scripts/wechat_auth.py 一次性保存元宝 Cookie;"
                    "不会自动打开微信或修改系统代理。本地 API 仅作已就绪的可选兜底"))

    return results, fail_code, (ffmpeg_ok and ffprobe_ok), ytdlp_ok, wechat_ok


def main():
    try:  # Windows 控制台默认 GBK,强制 UTF-8 输出防中文乱码
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="sansheng-gemini-video 依赖体检")
    ap.add_argument("--check", action="store_true",
                    help="静默模式:成功零输出,失败给分级 exit code")
    ap.add_argument("--require-ffmpeg", action="store_true",
                    help="把 ffmpeg/ffprobe 也算作硬依赖(默认可选)")
    ap.add_argument("--require-url-download", action="store_true",
                    help="把 yt-dlp 算作硬依赖(分析 B站/小红书/抖音等 URL 时使用)")
    ap.add_argument("--require-wechat", action="store_true",
                    help="要求微信视频号后台 Cookie 或已就绪的本地服务至少一种可用")
    args = ap.parse_args()

    results, fail_code, ffmpeg_ready, ytdlp_ready, wechat_ready = run_checks()
    if args.require_ffmpeg and ffmpeg_ready is False and fail_code == 0:
        fail_code = 4
    if args.require_url_download and not ytdlp_ready and fail_code == 0:
        fail_code = 6
    if args.require_wechat and not wechat_ready and fail_code == 0:
        fail_code = 7

    if args.check:
        if fail_code:
            required_names = {
                "Python >= 3.10",
                "requests(analyze_video 唯一第三方依赖)",
                "Gemini API key(GOOGLE_API_KEY / GEMINI_API_KEY)",
            }
            if args.require_ffmpeg:
                required_names.update({"ffmpeg(可选,预处理/机检)", "ffprobe(可选,读时长/分辨率)"})
            if args.require_url_download:
                required_names.add("yt-dlp(非 YouTube 链接下载)")
            if args.require_wechat:
                required_names.add("微信视频号后台授权/本地兜底")
            missing = [name for name, ok, _ in results if not ok and name in required_names]
            print(f"FAIL: 缺少 {', '.join(missing)}", file=sys.stderr)
        sys.exit(fail_code)

    print("sansheng-gemini-video 环境体检")
    print("=" * 40)
    for name, ok, hint in results:
        mark = "OK " if ok else "-- "
        print(f"[{mark}] {name}")
        if not ok:
            print(f"        修复: {hint}")
    print("=" * 40)
    if fail_code:
        print("环境未就绪,按上面修复后重跑。")
        sys.exit(fail_code)
    print("环境就绪,可以跑 analyze_video.py。")
    sys.exit(0)


if __name__ == "__main__":
    main()
