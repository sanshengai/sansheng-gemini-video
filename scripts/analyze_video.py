#!/usr/bin/env python
"""sansheng-gemini-video 核心:视频源(本地/在线视频) -> Gemini 原生多模态分析 -> 结构化评估 JSON。
走裸 REST(requests)+ ?key=,不依赖 google-genai SDK。按 key 前缀自动分流两种后端:
AI Studio key(AIza 前缀 -> generativelanguage 端点)/ Vertex AI Express key(AQ. 前缀 -> Vertex 端点,需 GEMINI_VERTEX_PROJECT)。
YouTube 由 Gemini 原生读取;其他链接经 source_resolver 下载/解密后以内联文件分析。
analyze_one() 可被 batch_eval.py 或你自己的代码 import 复用。"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import requests

from source_resolver import is_youtube_url, resolve_source

# ---------- .env 兜底加载(环境变量优先;仅本进程内存读取,绝不写盘/打印)----------
# .env 兜底位置:本 skill 目录、当前工作目录。所有配置(含 GEMINI_VERTEX_PROJECT)都可写进 .env,
# 不必非得设成 shell 环境变量 -- 与 .env.example 的承诺保持一致。
_ENV_FILES = (Path(__file__).resolve().parent.parent / ".env", Path.cwd() / ".env")


def _parse_env_files():
    """把 .env 兜底位置解析成 dict(先出现的文件优先)。绝不写盘、绝不打印值。"""
    data = {}
    for env in _ENV_FILES:
        try:
            if env.is_file():
                for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                    s = line.strip()
                    if not s or s.startswith("#") or "=" not in s:
                        continue
                    k, v = s.split("=", 1)
                    data.setdefault(k.strip(), v.strip().strip("\"'"))
        except Exception:
            pass
    return data


_DOTENV = _parse_env_files()


def _cfg(name, default=None):
    """读配置:环境变量优先,其次 .env 兜底,再默认。(API key 不走这里,见 load_key。)"""
    v = os.environ.get(name)
    if v is None or v == "":
        v = _DOTENV.get(name)
    return v if (v is not None and v != "") else default


# ---------- 配置(单一来源;环境变量优先,其次 .env,再默认)----------
# 仅 Vertex Express key(AQ. 前缀)需要项目/区域;AI Studio key(AIza 前缀)无需,留空即可。
VERTEX_PROJECT = _cfg("GEMINI_VERTEX_PROJECT")              # GCP 项目 ID(仅 Vertex Express key 需要;可写进 .env)
VERTEX_LOCATION = _cfg("GEMINI_VERTEX_LOCATION", "global")  # Gemini 3.x 在 Vertex 挂 global,不是 us-central1
DEFAULT_MODEL = _cfg("GEMINI_MODEL", "gemini-3.5-flash")    # 按你的 key 能用的模型设;env/.env 可覆盖
INLINE_MAX_MB = float(_cfg("GEMINI_INLINE_MAX_MB", "70"))   # inline base64 上限(官方 2026-01 升到 100MB base64,取 70 留余量,超了才压缩)
FLASH_INPUT_PRICE = 1.5    # gemini-3.5-flash 约 $1.5/1M input token
FLASH_OUTPUT_PRICE = 6.0   # 约 $4-6/1M output token(reverse/understand 输出长,不能按 input 价算,否则系统性低估)

VIDEO_MIME = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    ".webm": "video/webm", ".avi": "video/x-msvideo", ".wmv": "video/x-ms-wmv",
    ".flv": "video/x-flv", ".3gp": "video/3gpp", ".mpeg": "video/mpeg", ".mpg": "video/mpeg",
}

# 音频 MIME(Gemini 3.x 原生支持音频输入;超 70MB 走 compress_audio_for_inline 纯音频档,不进视频压缩梯子)
AUDIO_MIME = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".flac": "audio/flac", ".aiff": "audio/aiff", ".aif": "audio/aiff",
    ".wma": "audio/x-ms-wma",
}

DEFAULT_RUBRIC = [
    "画面清晰,无明显卡顿/黑屏/花屏",
    "操作步骤完整连贯,关键步骤没有跳过",
    "屏幕上的关键信息(按钮/网址/文字)清楚可读",
    "没有误操作、走错路或需要重来的片段",
    "时长合理,没有大段无意义的等待或空白",
]


_KEY_NAMES = ("GOOGLE_API_KEY", "GEMINI_API_KEY")


def load_key():
    """读 Gemini API key(AI Studio 的 AIza 或 Vertex Express 的 AQ.):先环境变量
    GOOGLE_API_KEY / GEMINI_API_KEY,再 skill 目录 / cwd 的 .env(_DOTENV 已解析)。不打印值。"""
    for name in _KEY_NAMES:
        v = os.environ.get(name)
        if v:
            return v.strip()
    for name in _KEY_NAMES:
        v = _DOTENV.get(name)
        if v:
            return v.strip()
    return None


def endpoint(key, model):
    """按 key 前缀自动分流后端(请求体两种后端一致,只有 URL 不同):
    - Vertex AI Express key(AQ. 前缀):Vertex 端点,需 GEMINI_VERTEX_PROJECT(+ location)。
    - 其他(AI Studio key,AIza… 前缀):公开 generativelanguage 端点,无需项目。"""
    if key.startswith("AQ."):
        if not VERTEX_PROJECT:
            raise RuntimeError(
                "检测到 Vertex Express key(AQ. 前缀),但没设 GEMINI_VERTEX_PROJECT。"
                "请设环境变量 GEMINI_VERTEX_PROJECT=<你的 GCP 项目 ID>;"
                "或改用 AI Studio key(AIza 前缀,无需项目)。")
        host = ("aiplatform.googleapis.com" if VERTEX_LOCATION == "global"
                else f"{VERTEX_LOCATION}-aiplatform.googleapis.com")
        return (f"https://{host}/v1/projects/{VERTEX_PROJECT}/locations/{VERTEX_LOCATION}"
                f"/publishers/google/models/{model}:generateContent?key={key}")
    return (f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
            f":generateContent?key={key}")


def _redact(text):
    """脱敏:把 URL query 里的 ?key=<密钥> 替换成 ?key=*** -- 防 API key 随网络异常/响应文本
    泄漏到 stderr / 落盘 _summary.json / 上层 agent 上下文(key 拼在请求 URL 里,异常串会带出来)。"""
    return re.sub(r"(key=)[^&\s)\"']+", r"\1***", str(text))


def is_youtube(s):
    return is_youtube_url(s)


def to_offset(s):
    """时间码 -> Gemini 的 '<秒>s' 偏移。只收 '90s' 纯秒 或 '1:30'/'1:02:45' 冒号格式;
    畸形输入(1m30s/abcs/1:3a)在发请求前就抛 ValueError,不静默把垃圾 offset 发去换一次付费 400。"""
    s = s.strip()
    if re.fullmatch(r"\d+s", s):
        return s
    if re.fullmatch(r"\d+(:\d+)+", s):
        sec = 0
        for x in s.split(":"):
            sec = sec * 60 + int(x)
        return f"{sec}s"
    raise ValueError(f"时间码格式不对: {s!r}(用 90s 或 1:30 / 1:02:45)")


def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


# 自动压缩档位:逐档加重直到压进 inline 上限。录屏/演示降到 2fps@720 不伤可读性;
# 实在压不下再砍到 1fps@480。Gemini 本就按帧采样,降帧降清不影响它看懂操作流程。
_COMPRESS_LADDER = [(720, 2, 28), (480, 1, 30)]


def compress_for_inline(src, target_mb):
    """把超过 inline 上限的本地视频转码压到 target_mb 以内,返回临时压缩文件 Path。
    压不下去抛 ValueError。调用方负责删返回的临时文件(analyze_one 在 finally 里删)。"""
    src = Path(src)
    out = Path(tempfile.gettempdir()) / f"gvid_{uuid.uuid4().hex[:8]}_{src.stem}.mp4"
    last_mb = None
    for scale, fps, crf in _COMPRESS_LADDER:
        cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", f"scale=-2:{scale}",
               "-r", str(fps), "-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast",
               "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.CalledProcessError, OSError) as e:
            if out.exists():
                out.unlink()
            raise ValueError(f"ffmpeg 压缩失败({src.name}): {e}")
        last_mb = out.stat().st_size / 1024 / 1024
        if last_mb <= target_mb:
            return out
    if out.exists():
        out.unlink()
    raise ValueError(
        f"{src.name} 压到最低档({_COMPRESS_LADDER[-1][0]}p/{_COMPRESS_LADDER[-1][1]}fps)仍 "
        f"{last_mb:.1f}MB > {target_mb}MB。建议 --start/--end 裁短片段,或走 GCS(超出本 skill)。")


# 纯音频压缩档位:逐档降码率转码成 mp3,直到压进 inline 上限。语音/BGM 64k 已够 Gemini 听懂。
_AUDIO_COMPRESS_LADDER = [64, 48, 32]


def compress_audio_for_inline(src, target_mb):
    """把超过 inline 上限的本地音频转码(纯音频档 -vn -c:a libmp3lame)压到 target_mb 以内,
    返回临时压缩文件 Path。压不下去抛 ValueError。调用方负责删返回的临时文件(analyze_one 在 finally 里删)。
    -- 音频若走 compress_for_inline 的视频档(-vf scale/-c:v libx264)会因无视频流让 ffmpeg 报错,故单独分流。"""
    src = Path(src)
    out = Path(tempfile.gettempdir()) / f"gvid_{uuid.uuid4().hex[:8]}_{src.stem}.mp3"
    last_mb = None
    for kbps in _AUDIO_COMPRESS_LADDER:
        cmd = ["ffmpeg", "-y", "-i", str(src), "-vn", "-c:a", "libmp3lame",
               "-b:a", f"{kbps}k", "-ac", "1", str(out)]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.CalledProcessError, OSError) as e:
            if out.exists():
                out.unlink()
            raise ValueError(f"ffmpeg 音频压缩失败({src.name}): {e}")
        last_mb = out.stat().st_size / 1024 / 1024
        if last_mb <= target_mb:
            return out
    if out.exists():
        out.unlink()
    raise ValueError(
        f"{src.name} 压到最低码率({_AUDIO_COMPRESS_LADDER[-1]}k 单声道)仍 "
        f"{last_mb:.1f}MB > {target_mb}MB。建议 --start/--end 裁短片段,或走 GCS(超出本 skill)。")


def build_video_part(src, auto_compress=True):
    """已归一输入转成 Gemini part。失败抛 ValueError(供 batch 捕获)。
    本地超 inline 上限时:auto_compress + 有 ffmpeg -> 自动转码压缩;否则抛带手动命令的错误。
    压缩产生的临时文件路径放 meta['_tmp'],由 analyze_one 用完删除。"""
    if is_youtube(src):
        return {"fileData": {"fileUri": src, "mimeType": "video/*"}}, {"kind": "youtube", "ref": src}
    if re.match(r"https?://", src, re.I):
        raise ValueError(f"链接没有经过 source_resolver 归一: {src}")
    p = Path(src)
    if not p.is_absolute():
        raise ValueError(f"必须用绝对路径: {src}")
    if not p.exists():
        raise ValueError(f"文件不存在: {src}")
    size_mb = p.stat().st_size / 1024 / 1024
    is_audio = p.suffix.lower() in AUDIO_MIME   # 音频要走纯音频压缩档,不能进视频转码梯子(会因无视频流让 ffmpeg 报错)
    meta = {"kind": "local", "ref": str(p), "size_mb": round(size_mb, 1)}
    use_path = p
    if size_mb > INLINE_MAX_MB:
        if auto_compress and ffmpeg_available():
            use_path = (compress_audio_for_inline(p, INLINE_MAX_MB) if is_audio
                        else compress_for_inline(p, INLINE_MAX_MB))
            meta["compressed"] = True
            meta["compressed_mb"] = round(use_path.stat().st_size / 1024 / 1024, 1)
            meta["_tmp"] = str(use_path)
        else:
            hint = "" if ffmpeg_available() else "装 ffmpeg 后本 skill 会自动压缩;或"
            manual = ("ffmpeg -i in.wav -vn -c:a libmp3lame -b:a 64k -ac 1 out.mp3" if is_audio
                      else "ffmpeg -i in.mp4 -r 2 -vf scale=-2:720 -c:v libx264 -crf 28 out.mp4")
            raise ValueError(
                f"{p.name} 有 {size_mb:.1f}MB,超 inline 上限 {INLINE_MAX_MB}MB。{hint}"
                f"先手动压缩: {manual}")
    ext = use_path.suffix.lower()
    mime = VIDEO_MIME.get(ext) or AUDIO_MIME.get(ext) or "video/mp4"
    data = base64.b64encode(use_path.read_bytes()).decode()
    return ({"inlineData": {"mimeType": mime, "data": data}}, meta)


# 通用理解:字段顺序=先观察后判断
UNDERSTAND_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "timeline": {"type": "array", "items": {"type": "object", "properties": {
            "timecode": {"type": "string"}, "visual": {"type": "string"}, "on_screen_text": {"type": "string"},
        }, "required": ["timecode", "visual"]}},
        "ui_elements": {"type": "array", "items": {"type": "string"}},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "timeline"],
}
UNDERSTAND_PROMPT = (
    "你在客观分析一段视频,只描述实际看到/听到的,不要脑补。用 MM:SS 标时间点。"
    "输出:整体概要 summary;时间线 timeline(每段 timecode + 画面 visual + 屏幕文字 on_screen_text);"
    "关键界面元素 ui_elements(按钮/菜单/网址/文字);画面或音频问题 issues(卡顿/黑屏/静音/误操作,没有就空数组)。"
    "所有描述字段一律用简体中文(屏幕上原文是英文的可保留原文)。")

# 素材验收:evidence 在 verdict 前 = 强制先看后判
SCREENING_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "criteria": {"type": "array", "items": {"type": "object", "properties": {
            "criterion": {"type": "string"},
            "evidence": {"type": "string"},
            "verdict": {"type": "string", "enum": ["meets", "partial", "fails", "not_observed"]},
            "score": {"type": "integer"},
        }, "required": ["criterion", "evidence", "verdict", "score"]}},
        "usable": {"type": "boolean"},
        "overall_score": {"type": "integer"},
        "top_issues": {"type": "array", "items": {"type": "string"}},
        "suggested_action": {"type": "string"},
    },
    "required": ["summary", "criteria", "usable", "overall_score"],
}


def screening_prompt(rubric):
    crit = "\n".join(f"- {c}" for c in rubric)
    return ("你在客观验收一段视频素材是否达标。只依据实际看到/听到的判断,不脑补,用 MM:SS 标证据时间点。\n"
            f"验收标准:\n{crit}\n"
            "对每条标准给:criterion(标准原文)、evidence(证据=中性描述 + MM:SS)、"
            "verdict(meets/partial/fails/not_observed)、score(0-5)。"
            "再给 summary、usable(整体能否用作素材)、overall_score(0-5)、top_issues、"
            "suggested_action(可用 / 裁剪哪段 / 重录及原因)。一律简体中文。")


# reverse(逆向拆解):拆一个视频创作者的 ⑤视觉制作 ⑥音频手法 + 跨维度"内容信号→手法"原料
# (供同行学习/借鉴;选题/口播/叙事等软维度请另用字幕分析,本脚本不碰)。
# 字段顺序刻意把 shot/技法等"观察"放在前,先看后判同 understand/screening。
REVERSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "visualCraft": {  # ⑤ 视觉制作
            "type": "object",
            "properties": {
                "shotTimeline": {"type": "array", "items": {"type": "object", "properties": {
                    "timecode": {"type": "string"},
                    "shot": {"type": "string"},            # 画面内容(口播↔画面对应)
                    "technique": {"type": "string"},       # 视觉手法:转场/动效/字幕条/分屏/缩放/B-roll/实拍
                    "on_screen_text": {"type": "string"},
                }, "required": ["timecode", "shot", "technique"]}},
                "colorGrading": {"type": "string"},        # 调色色调风格
                "rhythm": {"type": "string"},              # 剪辑节奏:快剪/留白/卡点
                "signatureMoves": {"type": "array", "items": {"type": "string"}},  # 标志性视觉招牌
            },
            "required": ["shotTimeline"],
        },
        "audioCraft": {  # ⑥ 音频
            "type": "object",
            "properties": {
                "bgm": {"type": "string"},                 # BGM 风格/情绪/有无
                "sfx": {"type": "array", "items": {"type": "object", "properties": {
                    "timecode": {"type": "string"}, "desc": {"type": "string"},
                }, "required": ["timecode", "desc"]}},
                "voiceProsody": {"type": "string"},        # 语速/停顿/重音(辅助 ④语气)
                "syncPoints": {"type": "array", "items": {"type": "object", "properties": {
                    "timecode": {"type": "string"}, "what": {"type": "string"},
                }, "required": ["timecode", "what"]}},      # 音画卡点/重音峰同步
            },
            "required": ["bgm"],
        },
        "crossDimSignals": {"type": "array", "items": {"type": "object", "properties": {
            "timecode": {"type": "string"},
            "contentSignal": {"type": "string"},           # 这个时刻在表达什么:制造反差/引出步骤/埋悬念/强调
            "visualTactic": {"type": "string"},            # 对应视觉手法
            "audioTactic": {"type": "string"},             # 对应音频手法
        }, "required": ["contentSignal", "visualTactic"]}},
    },
    "required": ["summary", "visualCraft", "audioCraft"],
}
REVERSE_PROMPT = (
    "【输出语言:简体中文】所有分析描述一律用简体中文,即使视频里的画面/字幕是英文也要用中文转述"
    "(屏幕英文原文可照抄进 on_screen_text)。\n"
    "你在逆向拆解一个短视频博主的视觉与音频制作手法(给同行学习借鉴),只依据实际看到/听到的,不脑补,"
    "用 MM:SS 标时间点。聚焦两个维度即可 -- 选题/口播文案/叙事这些软维度由别处的字幕分析负责,你不用管。\n"
    "【⑤视觉制作 visualCraft】shotTimeline=口播↔画面对应表(逐镜头:timecode + 画面内容 shot + "
    "视觉手法 technique[转场/动效/字幕条/分屏/缩放/B-roll/实拍] + 屏幕文字 on_screen_text);"
    "colorGrading=调色色调风格;rhythm=剪辑节奏(快剪/留白/卡点);signatureMoves=标志性视觉招牌。\n"
    "【⑥音频 audioCraft】bgm=背景音乐风格/情绪/有无;sfx=音效点(timecode + 描述);"
    "voiceProsody=语速停顿重音特征;syncPoints=音画卡点/重音峰同步点。\n"
    "【crossDimSignals】把'内容信号→手法'拆出来:每条给 contentSignal(此刻在表达什么:制造反差/引出步骤/"
    "埋悬念/强调重点)、visualTactic(视觉怎么配合)、audioTactic(音频怎么配合)、timecode。"
    "这是给下游做多维度手法分析的原料。\n一律简体中文(屏幕英文原文可保留)。")


def post_with_retry(url, body, tries=3):
    last = None
    for i in range(tries):
        try:
            r = requests.post(url, json=body, timeout=300)
            if r.status_code in (429, 500, 503) and i < tries - 1:
                time.sleep(5 * (i + 1))
                continue
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"请求失败(重试 {tries} 次): {_redact(last)}")


def analyze_one(source, intent="understand", rubric=None, start=None, end=None,
                fps=None, media_resolution="low", model=None, custom_prompt=None,
                custom_schema=None, auto_compress=True, download_provider="auto",
                download_dir=None, keep_download=False, cookies=None,
                cookies_from_browser=None, download_timeout=600):
    """核心:分析一个视频源,返回结构化 result dict。供脚本与其他 skill 复用。
    intent: understand(通用理解)/ screening(素材验收)/ reverse(逆向拆解 ⑤视觉⑥音频)。
    custom_prompt: 调用方自定义分析指令(给了就不走内置 intent 的 prompt)。
    custom_schema: 配合 custom_prompt 传入自定义 responseSchema,强制结构化输出;不传则 custom_prompt
                   走自由文本。让调用方(你的编排层)定义自己要的分析结构,本脚本保持通用、
                   不内置某个上层的业务字段(分析什么由调用方出指令)。
    auto_compress: 本地视频超 inline 上限时自动 ffmpeg 压缩(需 ffmpeg)。
    download_provider: URL 路由(auto/yt-dlp/ai-douyin/wechat-yuanbao/wechat-local);YouTube 始终原生直读。
    cookies / cookies_from_browser: 仅显式传入时交给 yt-dlp,绝不默认读取浏览器 Cookie。"""
    model = model or DEFAULT_MODEL
    key = load_key()
    if not key:
        raise RuntimeError("没有 API key:设环境变量 GOOGLE_API_KEY 或 GEMINI_API_KEY,或写进 skill 目录 / cwd 的 .env")
    resolved = None
    tmp_path = None
    try:
        resolved = resolve_source(
            source,
            provider=download_provider,
            download_dir=download_dir,
            keep_download=keep_download,
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
            timeout=download_timeout,
            ai_douyin_key=_cfg("AI_DOUYIN_API_KEY"),
            ai_douyin_base=_cfg("AI_DOUYIN_API_BASE", "https://ai-douyin.top9.cc"),
            wechat_api_base=_cfg("GEMINI_VIDEO_WECHAT_API_BASE", "http://127.0.0.1:2022"),
        )
        vpart, meta = build_video_part(resolved.analysis_source, auto_compress=auto_compress)
        tmp_path = meta.pop("_tmp", None)   # 压缩临时文件,出 result 前摘掉,finally 删
        if resolved.metadata:
            meta.update(resolved.metadata)
        vm = {}
        if start:
            vm["startOffset"] = to_offset(start)
        if end:
            vm["endOffset"] = to_offset(end)
        if fps:
            vm["fps"] = fps
        if vm:
            vpart["videoMetadata"] = vm

        if custom_prompt:
            prompt, schema = custom_prompt, custom_schema   # 上层可同时给 schema 强制结构化;不给则自由文本
        elif intent == "screening":
            prompt, schema = screening_prompt(rubric or DEFAULT_RUBRIC), SCREENING_SCHEMA
        elif intent == "reverse":
            prompt, schema = REVERSE_PROMPT, REVERSE_SCHEMA
        else:
            prompt, schema = UNDERSTAND_PROMPT, UNDERSTAND_SCHEMA

        gen_cfg = {"mediaResolution": f"MEDIA_RESOLUTION_{media_resolution.upper()}"}
        if schema:
            gen_cfg["responseMimeType"] = "application/json"
            gen_cfg["responseSchema"] = schema
        body = {"contents": [{"role": "user", "parts": [vpart, {"text": prompt}]}], "generationConfig": gen_cfg}

        t0 = time.time()
        r = post_with_retry(endpoint(key, model), body)
        dt = time.time() - t0
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {_redact(r.text[:400])}")
        d = r.json()
        try:
            text = d["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            raise RuntimeError(f"返回解析失败: {_redact(json.dumps(d)[:400])}")

        um = d.get("usageMetadata", {})
        total_tok = um.get("totalTokenCount", 0)
        # input/output 分单价计:input(含视频帧)按 input 价、output 按更贵的 output 价。
        # 缺 candidatesTokenCount 时回退 total-prompt 反推,再兜底全按 input 价(不高估)。
        in_tok = um.get("promptTokenCount", 0)
        out_tok = um.get("candidatesTokenCount")
        if out_tok is None:
            out_tok = max(total_tok - in_tok, 0) if (in_tok and total_tok) else 0
        est_cost = (in_tok / 1_000_000 * FLASH_INPUT_PRICE
                    + out_tok / 1_000_000 * FLASH_OUTPUT_PRICE)
        result = {
            "schema_version": "1.0",
            "source": {**meta, "model": model, "intent": intent, "media_resolution": media_resolution},
            "usage": {"total_tokens": total_tok, "input_tokens": in_tok, "output_tokens": out_tok,
                      "est_cost_usd": round(est_cost, 4),
                      "seconds": round(dt, 1), "detail": um.get("promptTokensDetails", [])},
        }
        if schema:
            try:
                result["analysis"] = json.loads(text)
            except Exception:
                result["analysis_raw"] = text
                result["warnings"] = ["结构化解析失败,已存原始文本"]
        else:
            result["analysis_text"] = text
        return result
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass
        if resolved is not None:
            resolved.cleanup()


def main():
    ap = argparse.ArgumentParser(description="Gemini 原生视频分析(AI Studio / Vertex Express,按 key 自动分流)")
    ap.add_argument("source", help="本地视频绝对路径 或在线视频链接")
    ap.add_argument("--intent", choices=["understand", "screening", "reverse"], default="understand",
                    help="understand=通用理解;screening=素材验收打分(配 --rubric);reverse=逆向拆解创作者的⑤视觉⑥音频手法")
    ap.add_argument("--rubric", default=None, help="screening 用:验收标准文件(每行一条),留空用内置通用档")
    ap.add_argument("--prompt", default=None, help="自定义指令(配合 --schema 可结构化;单给 --prompt 走自由文本)")
    ap.add_argument("--schema", default=None,
                    help="自定义 responseSchema 的 JSON 文件,配合 --prompt 强制结构化输出"
                         "(上层 skill 用它定义自己要的分析结构,本 skill 不内置业务字段)")
    ap.add_argument("--start", default=None, help="片段起点 1:30 / 90s")
    ap.add_argument("--end", default=None, help="片段终点")
    ap.add_argument("--fps", type=float, default=None, help="采样帧率,录屏建议 0.2-0.5 省 token")
    ap.add_argument("--media-resolution", choices=["low", "high"], default="low")
    ap.add_argument("--no-compress", dest="auto_compress", action="store_false",
                    help="超 inline 上限不自动压缩,直接报错给手动命令(默认自动 ffmpeg 压缩)")
    ap.add_argument("--download-provider", choices=["auto", "yt-dlp", "ai-douyin", "wechat-yuanbao", "wechat-local"],
                    default="auto", help="非 YouTube 链接的下载/解析路由,默认自动")
    ap.add_argument("--download-dir", default=None,
                    help="保留下载文件的绝对目录;留空默认只存临时目录,分析后自动清理")
    ap.add_argument("--keep-download", action="store_true",
                    help="保留下载文件;未给 --download-dir 时保存到当前目录 _video_downloads")
    ap.add_argument("--cookies", default=None,
                    help="显式交给 yt-dlp 的 cookies.txt 绝对路径;默认不读 Cookie")
    ap.add_argument("--cookies-from-browser", default=None,
                    help="显式允许 yt-dlp 从指定浏览器读取 Cookie,如 chrome;默认不读取")
    ap.add_argument("--download-timeout", type=int, default=600, help="下载/等待落盘超时秒数")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rubric = None
    if args.rubric:
        if not Path(args.rubric).is_file():
            raise SystemExit(f"--rubric 文件不存在: {args.rubric}"
                             "(传了 --rubric 就必须能读到;留空才走内置通用档,不静默回退)")
        rubric = [ln.strip() for ln in Path(args.rubric).read_text(encoding="utf-8").splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]

    custom_schema = None
    if args.schema:
        if not Path(args.schema).is_file():
            raise SystemExit(f"--schema 文件不存在: {args.schema}")
        try:
            custom_schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise SystemExit(f"--schema 不是合法 JSON: {e}")

    try:
        result = analyze_one(args.source, intent=args.intent, rubric=rubric, start=args.start,
                             end=args.end, fps=args.fps, media_resolution=args.media_resolution,
                             model=args.model, custom_prompt=args.prompt, custom_schema=custom_schema,
                             auto_compress=args.auto_compress,
                             download_provider=args.download_provider,
                             download_dir=args.download_dir,
                             keep_download=args.keep_download,
                             cookies=args.cookies,
                             cookies_from_browser=args.cookies_from_browser,
                             download_timeout=args.download_timeout)
    except (ValueError, RuntimeError) as e:
        raise SystemExit(str(e))

    base = re.split(r"[/\\?]", args.source.rstrip("/"))[-1] or "video"
    out_path = Path(args.out) if args.out else Path(f"{base}.eval.{uuid.uuid4().hex[:8]}.json")
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out_path)
    u = result["usage"]
    print(f"[完成] {out_path}  tokens={u['total_tokens']} 成本=${u['est_cost_usd']} 用时={u['seconds']}s")
    prev = result.get("analysis") or result.get("analysis_text") or result.get("analysis_raw")
    print(json.dumps(prev, ensure_ascii=False, indent=2)[:700] if isinstance(prev, (dict, list)) else str(prev)[:700])


if __name__ == "__main__":
    main()
