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
"""
import argparse
import importlib.util
import os
import shutil
import sys
from pathlib import Path

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

    return results, fail_code, (ffmpeg_ok and ffprobe_ok)


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
    args = ap.parse_args()

    results, fail_code, ffmpeg_ready = run_checks()
    if args.require_ffmpeg and ffmpeg_ready is False and fail_code == 0:
        fail_code = 4

    if args.check:
        if fail_code:
            missing = [name for name, ok, _ in results if not ok]
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
    if fail_code or (args.require_ffmpeg and not ffmpeg_ready):
        print("环境未就绪,按上面修复后重跑。")
        sys.exit(fail_code or 4)
    print("环境就绪,可以跑 analyze_video.py。")
    sys.exit(0)


if __name__ == "__main__":
    main()
