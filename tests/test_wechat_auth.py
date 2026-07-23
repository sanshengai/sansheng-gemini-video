from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import wechat_auth as auth  # noqa: E402


@unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
class WechatAuthTests(unittest.TestCase):
    def test_dpapi_roundtrip_and_clear(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "credential.dpapi"
            secret = "session=not-a-real-cookie-value"
            auth.store_cookie(secret, target)
            self.assertNotEqual(target.read_bytes(), secret.encode("utf-8"))
            self.assertEqual(auth.load_cookie(target), secret)
            self.assertTrue(auth.clear_cookie(target))
            self.assertIsNone(auth.load_cookie(target))

    def test_rejects_multiline_cookie(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, "单行"):
            auth.store_cookie("session=not-a-real-cookie-value\nsecond=value", Path(temp) / "x")


if __name__ == "__main__":
    unittest.main()
