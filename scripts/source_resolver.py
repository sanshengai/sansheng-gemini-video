#!/usr/bin/env python
"""把在线视频链接归一成本地媒体文件，供 Gemini 视频分析复用。

路由原则：
- YouTube 公共链接由 Gemini 原生读取，不下载。
- 微信视频号分享链接优先使用用户一次性保存的元宝 Cookie 纯后台解析；已经就绪的
  wx_channels_download 本地 API 只作末级兜底，脚本绝不自行启动微信或修改代理。
- 其他链接优先 yt-dlp；已显式配置 AI Douyin key 时可作短视频平台降级。

本模块不自动读取浏览器 Cookie；只有调用方显式授权的凭证才会使用。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

from wechat_auth import load_cookie as load_saved_yuanbao_cookie


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
WECHAT_HOSTS = {"weixin.qq.com", "channels.weixin.qq.com", "finder.video.qq.com"}
AI_DOUYIN_PLATFORMS = {"bilibili", "xiaohongshu", "douyin", "tiktok"}
_ENV_FILES = (Path(__file__).resolve().parent.parent / ".env", Path.cwd() / ".env")
YUANBAO_PARSE_URL = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
CHANNELS_FEED_URL = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
YUANBAO_CHAT_PATH = "naQivTmsDa/cf4d0079-ed1b-4c55-a3f3-2ca1379727d1"
YUANBAO_USER_ID = "b9575f6b0a8c4a55a08096904a5ef20a"
YUANBAO_DEVICE_ID = "1921b001708100d7fa31002b9646bd0cc15a3e2e1f"
YUANBAO_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


class SourceResolutionError(ValueError):
    """在线视频源无法归一时抛出。"""


def _config_value(name: str, default: str | None = None) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    for env_path in _ENV_FILES:
        try:
            if env_path.is_file():
                for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    value = line.strip()
                    if value.startswith(name + "="):
                        return value.split("=", 1)[1].strip().strip("\"'") or default
        except OSError:
            pass
    return default


@dataclass
class ResolvedSource:
    """归一后的分析源；临时下载由 cleanup() 回收。"""

    analysis_source: str
    metadata: dict[str, Any]
    _temp_dir: tempfile.TemporaryDirectory | None = None

    def cleanup(self) -> None:
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None


def is_http_url(value: str) -> bool:
    return bool(re.match(r"^https?://", value, re.I))


def _host(value: str) -> str:
    return (urllib.parse.urlparse(value).hostname or "").lower()


def is_youtube_url(value: str) -> bool:
    host = _host(value)
    return host in YOUTUBE_HOSTS or host.endswith(".youtube.com")


def detect_platform(value: str) -> str:
    host = _host(value)
    path = urllib.parse.urlparse(value).path.lower()
    if is_youtube_url(value):
        return "youtube"
    if host in WECHAT_HOSTS or host.endswith(".weixin.qq.com"):
        if "/sph/" in path or host != "weixin.qq.com":
            return "wechat-channels"
    if host == "b23.tv" or host.endswith(".bilibili.com"):
        return "bilibili"
    if host == "xhslink.com" or host.endswith(".xiaohongshu.com"):
        return "xiaohongshu"
    if host.endswith(".douyin.com") or host == "douyin.com" or host.endswith(".iesdouyin.com"):
        return "douyin"
    if host.endswith(".tiktok.com") or host == "tiktok.com":
        return "tiktok"
    return "web"


def _redact_url(value: str) -> str:
    """日志只保留域名和路径后缀，不泄漏签名查询串。"""
    parsed = urllib.parse.urlparse(value)
    suffix = Path(parsed.path).suffix or ""
    return f"{parsed.hostname or 'unknown-host'}{suffix}"


def _redact_urls_in_text(value: str) -> str:
    """清理下载器错误里的 URL 查询串，避免签名/token 随 stderr 外泄。"""
    return re.sub(
        r"https?://[^\s\"']+",
        lambda match: _redact_url(match.group(0)),
        str(value),
        flags=re.I,
    )


def _prepare_download_dir(download_dir: str | None, keep_download: bool):
    if download_dir:
        root = Path(download_dir).expanduser()
        if not root.is_absolute():
            raise SourceResolutionError(f"--download-dir 必须是绝对路径: {download_dir}")
        root.mkdir(parents=True, exist_ok=True)
        return root, None
    if keep_download:
        root = Path.cwd() / "_video_downloads"
        root.mkdir(parents=True, exist_ok=True)
        return root, None
    holder = tempfile.TemporaryDirectory(prefix="gvid_source_")
    return Path(holder.name), holder


def _yt_dlp_prefix() -> list[str]:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    raise SourceResolutionError(
        "需要下载该链接，但未找到 yt-dlp。请先安装 yt-dlp，或改传本地视频文件。"
    )


def download_with_ytdlp(
    url: str,
    root: Path,
    *,
    timeout: int = 600,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[Path, dict[str, Any]]:
    """用 yt-dlp 下载单个 URL，返回最终媒体路径和非敏感元数据。"""
    prefix = f"gvid_{uuid.uuid4().hex[:8]}"
    output_template = root / f"{prefix}.%(ext)s"
    cmd = [
        *_yt_dlp_prefix(),
        "--no-playlist",
        "--no-progress",
        "--newline",
        "--print",
        "after_move:filepath",
        "-o",
        str(output_template),
    ]
    if shutil.which("ffmpeg"):
        cmd.extend(["--merge-output-format", "mp4"])
    if cookies:
        cookie_path = Path(cookies).expanduser()
        if not cookie_path.is_absolute() or not cookie_path.is_file():
            raise SourceResolutionError(f"--cookies 必须指向存在的绝对路径: {cookies}")
        cmd.extend(["--cookies", str(cookie_path)])
    if cookies_from_browser:
        cmd.extend(["--cookies-from-browser", cookies_from_browser])
    cmd.append(url)

    try:
        proc = runner(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise SourceResolutionError(f"yt-dlp 下载超时({timeout}s): {_redact_url(url)}") from exc
    except OSError as exc:
        raise SourceResolutionError(f"yt-dlp 启动失败: {exc}") from exc
    if proc.returncode != 0:
        detail = _redact_urls_in_text(proc.stderr or proc.stdout or "未知错误").strip()[-800:]
        raise SourceResolutionError(f"yt-dlp 失败: {detail}")

    candidates = []
    for line in (proc.stdout or "").splitlines():
        path = Path(line.strip().strip('"'))
        if path.is_file():
            candidates.append(path)
    if not candidates:
        candidates = [p for p in root.glob(f"{prefix}.*") if p.is_file() and not p.name.endswith(".part")]
    if not candidates:
        raise SourceResolutionError("yt-dlp 返回成功但未找到下载后的媒体文件")
    media = max(candidates, key=lambda p: p.stat().st_size)
    return media, {"resolver": "yt-dlp", "downloaded_size_mb": round(media.stat().st_size / 1024 / 1024, 1)}


def _ai_douyin_endpoint(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/api/v1"):
        return base + "/video/download-url"
    if base.endswith("/api"):
        return base + "/v1/video/download-url"
    return base + "/api/v1/video/download-url"


def fetch_ai_douyin_candidates(
    url: str,
    *,
    api_key: str,
    api_base: str,
    timeout: int = 60,
) -> list[str]:
    """调用已显式配置的 AI Douyin 解析代理，返回去重后的下载候选。"""
    try:
        response = requests.post(
            _ai_douyin_endpoint(api_base),
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json={"url": url},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise SourceResolutionError(f"AI Douyin 解析请求失败: {exc}") from exc
    if response.status_code == 401:
        raise SourceResolutionError("AI Douyin API key 无效")
    if response.status_code == 402:
        raise SourceResolutionError("AI Douyin 余额不足")
    if not 200 <= response.status_code < 300:
        raise SourceResolutionError(f"AI Douyin 解析失败: HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceResolutionError("AI Douyin 返回的不是合法 JSON") from exc

    raw = payload.get("download_urls") or []
    if not isinstance(raw, list):
        raw = []
    if payload.get("download_url"):
        raw.append(payload["download_url"])
    result = []
    seen = set()
    for candidate in raw:
        value = str(candidate).strip()
        if is_http_url(value) and value not in seen:
            seen.add(value)
            result.append(value)
    if not result:
        raise SourceResolutionError("AI Douyin 未返回 download_url")
    return result


def download_first_candidate(
    candidates: list[str],
    output: Path,
    *,
    timeout: int = 120,
    headers: dict[str, str] | None = None,
) -> Path:
    """流式下载首个可用候选；错误中不打印带签名的完整 URL。"""
    errors = []
    for index, candidate in enumerate(candidates, 1):
        partial = output.with_suffix(output.suffix + ".part")
        partial.unlink(missing_ok=True)
        try:
            with requests.get(
                candidate,
                headers={"User-Agent": USER_AGENT, **(headers or {})},
                stream=True,
                allow_redirects=True,
                timeout=(10, timeout),
            ) as response:
                response.raise_for_status()
                with partial.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            if partial.stat().st_size == 0:
                raise OSError("下载结果为空")
            partial.replace(output)
            return output
        except (requests.RequestException, OSError) as exc:
            partial.unlink(missing_ok=True)
            errors.append(f"候选 {index}({_redact_url(candidate)}): {exc}")
    raise SourceResolutionError("所有解析代理候选均下载失败: " + "; ".join(errors))


def load_yuanbao_cookie() -> str | None:
    """按显式配置 -> 私密文件 -> Windows DPAPI 的顺序读取元宝 Cookie。"""
    direct = _config_value("GEMINI_VIDEO_YUANBAO_COOKIE")
    if direct:
        return direct.strip()
    cookie_file = _config_value("GEMINI_VIDEO_YUANBAO_COOKIE_FILE")
    if cookie_file:
        path = Path(cookie_file).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise SourceResolutionError("GEMINI_VIDEO_YUANBAO_COOKIE_FILE 必须指向存在的绝对路径")
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    return load_saved_yuanbao_cookie()


def _yuanbao_headers(cookie: str) -> dict[str, str]:
    """元宝 Web 解析接口所需请求头；Cookie 只驻留内存，不写日志。

    x-* 值来自公开 SPH Web 协议，并非用户凭证；真正的会话权限只来自 Cookie。
    """
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": "https://yuanbao.tencent.com",
        "Referer": f"https://yuanbao.tencent.com/chat/{YUANBAO_CHAT_PATH}",
        "User-Agent": YUANBAO_USER_AGENT,
        "Sec-Ch-Ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "T-Userid": YUANBAO_USER_ID,
        "X-Agentid": YUANBAO_CHAT_PATH,
        "X-Commit-Tag": "72282a0d",
        "X-Device-Id": YUANBAO_DEVICE_ID,
        "X-Hy106": "",
        "X-Hy92": "e963067ffa31002b9646bd0c03000008b1951a",
        "X-Hy93": YUANBAO_DEVICE_ID,
        "X-Id": YUANBAO_USER_ID,
        "X-Instance-Id": "5",
        "X-Language": "zh-CN",
        "X-OS_Version": "Mac OS(10.15.7)-Blink",
        "X-Platform": "mac",
        "X-Requested-With": "XMLHttpRequest",
        "X-Source": "web",
        "X-Web-Third-Source": "main",
        "X-Webdriver": "0",
        "X-Webversion": "2.69.0",
        "X-Ybuitest": "0",
        "Cookie": cookie,
    }


def _validate_wechat_share_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != "weixin.qq.com"
        or not re.fullmatch(r"/sph/[A-Za-z0-9_-]+/?", parsed.path)
    ):
        raise SourceResolutionError("仅接受 https://weixin.qq.com/sph/<ID> 格式的视频号分享链接")


def _is_allowed_wechat_media_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    host = (parsed.hostname or "").lower()
    return parsed.scheme.lower() == "https" and (
        host == "finder.video.qq.com" or host.endswith(".video.qq.com")
    )


def fetch_yuanbao_video_candidates(
    url: str,
    *,
    cookie: str,
    timeout: int = 60,
) -> tuple[list[str], dict[str, Any], str]:
    """纯后台把视频号分享链接换成腾讯 CDN 直链，不启动微信或系统代理。"""
    _validate_wechat_share_url(url)
    try:
        parsed_response = requests.post(
            YUANBAO_PARSE_URL,
            headers=_yuanbao_headers(cookie),
            json={"type": "video_channel_url", "url": url, "scene": 1},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise SourceResolutionError(f"元宝后台解析请求失败: {exc}") from exc
    if parsed_response.status_code in {401, 403}:
        raise SourceResolutionError(
            "元宝 Cookie 已失效；请重新运行 python scripts/wechat_auth.py 更新一次性授权"
        )
    if not 200 <= parsed_response.status_code < 300:
        raise SourceResolutionError(f"元宝后台解析失败: HTTP {parsed_response.status_code}")
    try:
        parsed_payload = parsed_response.json()
    except ValueError as exc:
        raise SourceResolutionError("元宝后台解析返回的不是合法 JSON") from exc
    parse_data = parsed_payload.get("data") or {}
    playable_url = str(parse_data.get("playable_url") or "")
    playable = urllib.parse.urlparse(playable_url)
    query = urllib.parse.parse_qs(playable.query)
    general_token = (query.get("token") or [""])[0]
    export_id = (query.get("eid") or [""])[0] or str(parse_data.get("wx_export_id") or "")
    if not general_token or not export_id:
        message = str(parsed_payload.get("msg") or "缺少 token/eid")
        raise SourceResolutionError(f"元宝未能识别该分享链接: {message}")

    rid = f"{int(time.time()):x}-{secrets.token_hex(4)}"
    page_url = "https://channels.weixin.qq.com/finder-preview/pages/feed"
    referer = (
        page_url
        + "?entry_card_type=48&comment_scene=39&appid=0&token="
        + urllib.parse.quote(general_token, safe="")
        + "&entry_scene=0&eid="
        + urllib.parse.quote(export_id, safe="")
    )
    try:
        feed_response = requests.post(
            CHANNELS_FEED_URL,
            params={"_rid": rid, "_pageUrl": page_url},
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Content-Type": "application/json",
                "Origin": "https://channels.weixin.qq.com",
                "Referer": referer,
                "Sec-Ch-Ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"macOS"',
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "User-Agent": YUANBAO_USER_AGENT,
            },
            json={"baseReq": {"generalToken": general_token}, "exportId": export_id},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise SourceResolutionError(f"视频号详情请求失败: {exc}") from exc
    if not 200 <= feed_response.status_code < 300:
        raise SourceResolutionError(f"视频号详情请求失败: HTTP {feed_response.status_code}")
    try:
        feed_payload = feed_response.json()
    except ValueError as exc:
        raise SourceResolutionError("视频号详情返回的不是合法 JSON") from exc
    if feed_payload.get("errCode") not in {None, 0}:
        raise SourceResolutionError(f"视频号详情解析失败: {feed_payload.get('errMsg') or '未知错误'}")
    data = feed_payload.get("data") or {}
    feed = data.get("feedInfo") or {}
    candidates = []
    for candidate in (
        (feed.get("h264VideoInfo") or {}).get("videoUrl"),
        feed.get("videoUrl"),
        (feed.get("h265VideoInfo") or {}).get("videoUrl"),
    ):
        value = str(candidate or "").strip()
        if _is_allowed_wechat_media_url(value) and value not in candidates:
            candidates.append(value)
    if not candidates:
        raise SourceResolutionError("视频号详情中没有可下载的视频直链")
    safe_meta = {
        "author": str((data.get("authorInfo") or {}).get("nickname") or ""),
        "description": str(feed.get("description") or ""),
    }
    return candidates, safe_meta, referer


def resolve_wechat_yuanbao(
    url: str,
    *,
    cookie: str,
    download_dir: str | None = None,
    keep_download: bool = False,
    timeout: int = 600,
) -> ResolvedSource:
    """元宝 Cookie -> 视频直链 -> 本地文件；全程后台且不操作微信 UI。"""
    root, holder = _prepare_download_dir(download_dir, keep_download)
    try:
        candidates, parsed_meta, referer = fetch_yuanbao_video_candidates(
            url, cookie=cookie, timeout=min(timeout, 60)
        )
        output = root / f"gvid_{uuid.uuid4().hex[:8]}.mp4"
        media = download_first_candidate(
            candidates,
            output,
            timeout=min(timeout, 300),
            headers={"Referer": referer},
        )
        metadata = {
            "kind": "url_downloaded",
            "ref": url,
            "platform": "wechat-channels",
            "route": "download",
            "resolver": "yuanbao-cookie",
            "downloaded_size_mb": round(media.stat().st_size / 1024 / 1024, 1),
            **parsed_meta,
        }
        if holder is None:
            metadata["download_path"] = str(media)
        return ResolvedSource(str(media), metadata, holder)
    except Exception:
        if holder is not None:
            holder.cleanup()
        raise


def _wechat_local_ready(api_base: str) -> bool:
    try:
        response = requests.get(api_base.rstrip("/") + "/api/status", timeout=2)
        payload = response.json() if response.status_code == 200 else {}
        return bool((((payload.get("data") or {}).get("channels") or {}).get("available")))
    except (requests.RequestException, ValueError):
        return False


def resolve_wechat_local(
    url: str,
    *,
    api_base: str = "http://127.0.0.1:2022",
    timeout: int = 600,
    poll_interval: float = 1.0,
) -> ResolvedSource:
    """调用 wx_channels_download 本地 API，等待已解密媒体落盘。"""
    endpoint = api_base.rstrip("/") + "/api/task/create_channels"
    try:
        response = requests.post(endpoint, json={"url": url, "mp3": False, "cover": False}, timeout=30)
    except requests.RequestException as exc:
        raise SourceResolutionError(
            "微信视频号链接需要本机 wx_channels_download 服务完成解析和解密；"
            f"当前无法连接 {api_base}。请先安装并初始化该服务，或改传本地视频。"
        ) from exc
    if not 200 <= response.status_code < 300:
        raise SourceResolutionError(f"wx_channels_download 返回 HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceResolutionError("wx_channels_download 返回的不是合法 JSON") from exc
    if payload.get("code") != 0:
        raise SourceResolutionError(f"wx_channels_download 解析失败: {payload.get('msg') or '未知错误'}")
    data = payload.get("data") or {}
    file_path = data.get("file_path")
    if not file_path and data.get("path") and data.get("name"):
        file_path = str(Path(data["path"]) / data["name"])
    if not file_path:
        raise SourceResolutionError("wx_channels_download 未返回下载文件路径")
    media = Path(file_path)
    if not media.is_absolute():
        raise SourceResolutionError(f"wx_channels_download 返回了非绝对路径: {file_path}")

    deadline = time.monotonic() + timeout
    last_size = -1
    stable_polls = 0
    while time.monotonic() < deadline:
        try:
            size = media.stat().st_size
        except OSError:
            size = -1
        if size > 0 and size == last_size:
            stable_polls += 1
            if stable_polls >= 2:
                return ResolvedSource(
                    str(media),
                    {
                        "kind": "url_downloaded",
                        "ref": url,
                        "platform": "wechat-channels",
                        "route": "download",
                        "resolver": "wx_channels_download",
                        "download_path": str(media),
                        "downloaded_size_mb": round(size / 1024 / 1024, 1),
                    },
                )
        else:
            stable_polls = 0
        last_size = size
        time.sleep(poll_interval)
    raise SourceResolutionError(f"等待微信视频号下载完成超时({timeout}s): {media}")


def resolve_source(
    source: str,
    *,
    provider: str = "auto",
    download_dir: str | None = None,
    keep_download: bool = False,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    timeout: int = 600,
    ai_douyin_key: str | None = None,
    ai_douyin_base: str = "https://ai-douyin.top9.cc",
    wechat_api_base: str = "http://127.0.0.1:2022",
) -> ResolvedSource:
    """把任意受支持输入归一为 Gemini 可分析的本地文件或 YouTube URL。"""
    if not is_http_url(source):
        return ResolvedSource(source, {})
    platform = detect_platform(source)
    if platform == "youtube":
        return ResolvedSource(
            source,
            {"kind": "youtube", "ref": source, "platform": "youtube", "route": "gemini-direct"},
        )
    if provider not in {"auto", "yt-dlp", "ai-douyin", "wechat-yuanbao", "wechat-local"}:
        raise SourceResolutionError(f"未知下载 provider: {provider}")
    if platform == "wechat-channels":
        if provider not in {"auto", "wechat-yuanbao", "wechat-local"}:
            raise SourceResolutionError("微信视频号链接只能使用 wechat-yuanbao 或 wechat-local provider")
        if provider in {"auto", "wechat-yuanbao"}:
            cookie = load_yuanbao_cookie()
            if cookie:
                try:
                    return resolve_wechat_yuanbao(
                        source,
                        cookie=cookie,
                        download_dir=download_dir,
                        keep_download=keep_download,
                        timeout=timeout,
                    )
                except SourceResolutionError:
                    if provider == "wechat-yuanbao":
                        raise
            elif provider == "wechat-yuanbao":
                raise SourceResolutionError(
                    "微信视频号后台解析尚未授权；请先运行 python scripts/wechat_auth.py。不会自动打开微信或浏览器。"
                )
        if provider == "wechat-local" or _wechat_local_ready(wechat_api_base):
            return resolve_wechat_local(source, api_base=wechat_api_base, timeout=timeout)
        raise SourceResolutionError(
            "微信视频号需要一次性元宝 Cookie 授权；请运行 python scripts/wechat_auth.py。"
            "本次未启动微信、浏览器、系统代理或登录窗口。"
        )
    if provider == "wechat-local":
        raise SourceResolutionError("wechat-local provider 只支持微信视频号链接")

    root, holder = _prepare_download_dir(download_dir, keep_download)
    errors = []
    try:
        if provider in {"auto", "yt-dlp"}:
            try:
                media, dl_meta = download_with_ytdlp(
                    source,
                    root,
                    timeout=timeout,
                    cookies=cookies,
                    cookies_from_browser=cookies_from_browser,
                )
                meta = {
                    "kind": "url_downloaded",
                    "ref": source,
                    "platform": platform,
                    "route": "download",
                    **dl_meta,
                }
                if holder is None:
                    meta["download_path"] = str(media)
                return ResolvedSource(str(media), meta, holder)
            except SourceResolutionError as exc:
                errors.append(str(exc))
                if provider == "yt-dlp":
                    raise

        if provider in {"auto", "ai-douyin"} and platform in AI_DOUYIN_PLATFORMS:
            if not ai_douyin_key:
                if provider == "ai-douyin":
                    raise SourceResolutionError("ai-douyin provider 需要配置 AI_DOUYIN_API_KEY")
            else:
                try:
                    candidates = fetch_ai_douyin_candidates(
                        source, api_key=ai_douyin_key, api_base=ai_douyin_base
                    )
                    output = root / f"gvid_{uuid.uuid4().hex[:8]}.mp4"
                    media = download_first_candidate(candidates, output, timeout=min(timeout, 300))
                    meta = {
                        "kind": "url_downloaded",
                        "ref": source,
                        "platform": platform,
                        "route": "download",
                        "resolver": "ai-douyin",
                        "downloaded_size_mb": round(media.stat().st_size / 1024 / 1024, 1),
                    }
                    if holder is None:
                        meta["download_path"] = str(media)
                    return ResolvedSource(str(media), meta, holder)
                except SourceResolutionError as exc:
                    errors.append(str(exc))

        detail = " | ".join(errors) or "没有可用下载器"
        hint = ""
        if platform in {"bilibili", "xiaohongshu", "douyin", "tiktok"}:
            hint = "；如平台要求登录，请显式传 --cookies 或 --cookies-from-browser，或配置 AI_DOUYIN_API_KEY"
        raise SourceResolutionError(f"链接下载失败: {detail}{hint}")
    except Exception:
        if holder is not None:
            holder.cleanup()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="把在线视频链接下载/归一为 Gemini 可分析的本地媒体")
    parser.add_argument("source")
    parser.add_argument(
        "--provider",
        choices=["auto", "yt-dlp", "ai-douyin", "wechat-yuanbao", "wechat-local"],
        default="auto",
    )
    parser.add_argument("--download-dir", default=None)
    parser.add_argument("--cookies", default=None)
    parser.add_argument("--cookies-from-browser", default=None)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    try:
        resolved = resolve_source(
            args.source,
            provider=args.provider,
            download_dir=args.download_dir,
            keep_download=True,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            timeout=args.timeout,
            ai_douyin_key=_config_value("AI_DOUYIN_API_KEY"),
            ai_douyin_base=_config_value("AI_DOUYIN_API_BASE", "https://ai-douyin.top9.cc"),
            wechat_api_base=_config_value("GEMINI_VIDEO_WECHAT_API_BASE", "http://127.0.0.1:2022"),
        )
    except SourceResolutionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"source": resolved.analysis_source, "metadata": resolved.metadata}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
