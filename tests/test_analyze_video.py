from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analyze_video as av  # noqa: E402


class FakeGeminiResponse:
    status_code = 200
    text = ""

    def json(self):
        return {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps({"summary": "ok", "timeline": []})}]}}
            ],
            "usageMetadata": {"totalTokenCount": 10, "promptTokenCount": 8, "candidatesTokenCount": 2},
        }


class AnalyzeVideoUrlIntegrationTests(unittest.TestCase):
    @patch.object(av, "post_with_retry", return_value=FakeGeminiResponse())
    @patch.object(av, "load_key", return_value="AIza-test")
    def test_downloaded_url_is_inlined_analyzed_and_cleaned(self, _key, post):
        with tempfile.TemporaryDirectory() as temp:
            media = Path(temp) / "downloaded.mp4"
            media.write_bytes(b"video")
            resolved = Mock()
            resolved.analysis_source = str(media)
            resolved.metadata = {
                "kind": "url_downloaded",
                "platform": "bilibili",
                "route": "download",
                "resolver": "yt-dlp",
            }

            with patch.object(av, "resolve_source", return_value=resolved) as resolve:
                result = av.analyze_one("https://www.bilibili.com/video/BVdemo")

            resolve.assert_called_once()
            resolved.cleanup.assert_called_once()
            request_body = post.call_args.args[1]
            self.assertIn("inlineData", request_body["contents"][0]["parts"][0])
            self.assertEqual(result["analysis"]["summary"], "ok")
            self.assertEqual(result["source"]["resolver"], "yt-dlp")


if __name__ == "__main__":
    unittest.main()
