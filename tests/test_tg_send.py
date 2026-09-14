"""tg_send.py 단위 테스트 — 네트워크 0."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import tg_send as ts  # noqa: E402


class TgSend(unittest.TestCase):
    def test_resolve_text_prefers_arg_then_stdin(self):
        self.assertEqual(ts.resolve_text("hi", stdin_text="ignored"), "hi")
        self.assertEqual(ts.resolve_text(None, stdin_text=" from stdin \n"), "from stdin")
        self.assertEqual(ts.resolve_text(None, stdin_text=""), "")

    def test_send_calls_bot_api_with_chat_and_text(self):
        calls = []
        def fake_post(url, payload):
            calls.append((url, payload)); return {"ok": True}
        rc = ts.send("tkn", "111", "hello", post=fake_post)
        self.assertEqual(rc, 0)
        self.assertTrue(calls[0][0].endswith("/sendMessage"))
        self.assertNotIn("tkn", str(calls[0][1]))
        self.assertEqual(calls[0][1], {"chat_id": "111", "text": "hello"})

    def test_exit_codes(self):
        self.assertEqual(ts.send("tkn", "111", "x", post=lambda u, p: {"ok": False}), 1)
        self.assertEqual(ts.send("tkn", "111", "x", post=lambda u, p: (_ for _ in ()).throw(OSError("net"))), 1)
        self.assertEqual(ts.send("", "111", "x", post=lambda u, p: {"ok": True}), 2)
        self.assertEqual(ts.send("tkn", "", "x", post=lambda u, p: {"ok": True}), 2)
        self.assertEqual(ts.send("tkn", "111", "", post=lambda u, p: {"ok": True}), 2)


if __name__ == "__main__":
    unittest.main()
