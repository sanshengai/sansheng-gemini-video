#!/usr/bin/env python
"""一次性保存/读取元宝 Web Cookie。

Windows 默认使用当前用户 DPAPI 加密，密文仅能由同一 Windows 用户解开。
脚本不读取浏览器 Cookie，也不在 stdout/stderr 打印凭证内容。
"""
from __future__ import annotations

import argparse
import ctypes
import getpass
import os
import sys
from ctypes import wintypes
from pathlib import Path


APP_DIR = "sansheng-gemini-video"
COOKIE_FILE = "yuanbao-cookie.dpapi"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def default_cookie_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_DIR / COOKIE_FILE
    return Path.home() / ".local" / "share" / APP_DIR / COOKIE_FILE


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    value = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return value, buffer


def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI 凭证库当前只支持 Windows；其他系统请使用环境变量")
    incoming, keepalive = _blob(data)
    outgoing = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(incoming),
        "sansheng-gemini-video Yuanbao cookie",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(outgoing),
    )
    del keepalive
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(outgoing.pbData)


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI 凭证库当前只支持 Windows；其他系统请使用环境变量")
    incoming, keepalive = _blob(data)
    outgoing = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(incoming), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(outgoing)
    )
    del keepalive
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(outgoing.pbData)


def _validate(cookie: str) -> str:
    value = cookie.strip()
    if len(value) < 20 or "=" not in value:
        raise ValueError("Cookie 看起来不完整；请粘贴请求头中 Cookie 的完整值")
    if "\r" in value or "\n" in value:
        raise ValueError("Cookie 必须是单行值")
    return value


def store_cookie(cookie: str, path: Path | None = None) -> Path:
    target = path or default_cookie_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_protect(_validate(cookie).encode("utf-8")))
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def load_cookie(path: Path | None = None) -> str | None:
    target = path or default_cookie_path()
    if not target.is_file():
        return None
    try:
        return _validate(_unprotect(target.read_bytes()).decode("utf-8"))
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return None


def clear_cookie(path: Path | None = None) -> bool:
    target = path or default_cookie_path()
    if not target.exists():
        return False
    target.unlink()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="保存元宝 Web Cookie（Windows DPAPI 当前用户加密）")
    parser.add_argument("--status", action="store_true", help="只检查是否已有可读取凭证")
    parser.add_argument("--clear", action="store_true", help="清除本机已保存凭证")
    parser.add_argument("--from-file", default=None, help="从绝对路径读取 Cookie；文件内容不会打印")
    args = parser.parse_args()

    target = default_cookie_path()
    if args.status:
        ready = bool(load_cookie(target))
        print("READY" if ready else "MISSING")
        return 0 if ready else 1
    if args.clear:
        print("CLEARED" if clear_cookie(target) else "MISSING")
        return 0

    if args.from_file:
        source = Path(args.from_file).expanduser()
        if not source.is_absolute() or not source.is_file():
            print("--from-file 必须指向存在的绝对路径", file=sys.stderr)
            return 2
        cookie = source.read_text(encoding="utf-8").strip()
    else:
        cookie = getpass.getpass("粘贴元宝请求头 Cookie（输入隐藏）: ")
    try:
        saved = store_cookie(cookie, target)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"保存失败: {exc}", file=sys.stderr)
        return 2
    print(f"SAVED: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
