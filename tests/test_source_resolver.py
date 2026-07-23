from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import source_resolver as sr  # noqa: E402


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def json(self):
        return self._payload


class SourceResolverTests(unittest.TestCase):
    def test_detect_platforms(self):
        cases = {
            "https://youtu.be/abc": "youtube",
            "https://www.bilibili.com/video/BV123": "bilibili",
            "https://b23.tv/demo": "bilibili",
            "https://xhslink.com/m/demo": "xiaohongshu",
            "https://v.douyin.com/demo": "douyin",
            "https://weixin.qq.com/sph/Ak7BW4Lsbd": "wechat-channels",
            "https://example.com/video.mp4": "web",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(sr.detect_platform(url), expected)

    def test_youtube_stays_remote_for_gemini(self):
        url = "https://www.youtube.com/watch?v=demo"
        resolved = sr.resolve_source(url)
        self.assertEqual(resolved.analysis_source, url)
        self.assertEqual(resolved.metadata["route"], "gemini-direct")
        self.assertEqual(resolved.metadata["platform"], "youtube")

    @patch.object(sr, "_yt_dlp_prefix", return_value=["yt-dlp"])
    def test_ytdlp_download_returns_created_file(self, _prefix):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def fake_runner(cmd, **_kwargs):
                template = Path(cmd[cmd.index("-o") + 1])
                media = Path(str(template).replace("%(ext)s", "mp4"))
                media.write_bytes(b"video")
                return SimpleNamespace(returncode=0, stdout=str(media), stderr="")

            media, meta = sr.download_with_ytdlp(
                "https://example.com/video", root, runner=fake_runner
            )
            self.assertTrue(media.is_file())
            self.assertEqual(meta["resolver"], "yt-dlp")

    @patch.object(sr.requests, "post")
    def test_ai_douyin_candidates_are_deduped(self, post):
        post.return_value = FakeResponse(
            {
                "download_urls": ["https://cdn.example/a.mp4", "https://cdn.example/a.mp4"],
                "download_url": "https://cdn.example/b.mp4?token=secret",
            }
        )
        result = sr.fetch_ai_douyin_candidates(
            "https://xhslink.com/m/demo", api_key="secret", api_base="https://resolver.example"
        )
        self.assertEqual(
            result,
            ["https://cdn.example/a.mp4", "https://cdn.example/b.mp4?token=secret"],
        )
        self.assertEqual(post.call_args.kwargs["headers"]["X-API-Key"], "secret")

    @patch.object(sr.requests, "post")
    def test_wechat_local_waits_for_decrypted_file(self, post):
        with tempfile.TemporaryDirectory() as temp:
            media = Path(temp) / "wechat.mp4"
            media.write_bytes(b"decrypted-video")
            post.return_value = FakeResponse(
                {"code": 0, "msg": "成功", "data": {"id": "task", "file_path": str(media)}}
            )
            resolved = sr.resolve_wechat_local(
                "https://weixin.qq.com/sph/demo", poll_interval=0, timeout=1
            )
            self.assertEqual(resolved.analysis_source, str(media))
            self.assertEqual(resolved.metadata["resolver"], "wx_channels_download")
            self.assertEqual(resolved.metadata["download_path"], str(media))

    @patch.object(sr.requests, "post", side_effect=sr.requests.ConnectionError("offline"))
    def test_wechat_local_reports_real_dependency(self, _post):
        with self.assertRaisesRegex(sr.SourceResolutionError, "wx_channels_download"):
            sr.resolve_source("https://weixin.qq.com/sph/demo")

    def test_auto_falls_back_to_configured_proxy(self):
        with patch.object(
            sr, "download_with_ytdlp", side_effect=sr.SourceResolutionError("yt-dlp blocked")
        ), patch.object(
            sr,
            "fetch_ai_douyin_candidates",
            return_value=["https://cdn.example/video.mp4"],
        ), patch.object(sr, "download_first_candidate") as download:

            def fake_download(_candidates, output, **_kwargs):
                output.write_bytes(b"video")
                return output

            download.side_effect = fake_download
            resolved = sr.resolve_source(
                "https://www.xiaohongshu.com/explore/demo", ai_douyin_key="configured"
            )
            try:
                self.assertEqual(resolved.metadata["resolver"], "ai-douyin")
                self.assertTrue(Path(resolved.analysis_source).is_file())
            finally:
                resolved.cleanup()

    def test_cookie_file_must_be_explicit_and_absolute(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(sr.SourceResolutionError, "绝对路径"):
                sr.download_with_ytdlp(
                    "https://example.com/video", Path(temp), cookies="cookies.txt"
                )

    @patch.object(sr, "_yt_dlp_prefix", return_value=["yt-dlp"])
    def test_ytdlp_failure_redacts_signed_url(self, _prefix):
        failure = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ERROR https://cdn.example/video.mp4?token=secret&expires=1 failed",
        )
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(sr.SourceResolutionError) as caught:
            sr.download_with_ytdlp(
                "https://example.com/video",
                Path(temp),
                runner=Mock(return_value=failure),
            )
        message = str(caught.exception)
        self.assertNotIn("secret", message)
        self.assertNotIn("expires", message)
        self.assertIn("cdn.example.mp4", message)


if __name__ == "__main__":
    unittest.main()
