"""report.py — console 운영자 채널: stdout + local/reports/<date>.md 누적. 네트워크 0."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import report as rp  # noqa: E402


class Report(unittest.TestCase):
    def test_appends_timestamped_entry_and_returns_text(self):
        with tempfile.TemporaryDirectory() as d:
            path = rp.write_report("준비 완료", reports_dir=d, now="2026-06-18T10:50:00")
            path2 = rp.write_report("두 번째\n줄", reports_dir=d, now="2026-06-18T11:00:00")
            self.assertEqual(path, path2)
            self.assertTrue(path.endswith("2026-06-18.md"))
            body = open(path, encoding="utf-8").read()
            self.assertIn("## 10:50:00\n\n준비 완료\n", body)
            self.assertIn("## 11:00:00\n\n두 번째\n줄\n", body)

    def test_resolve_text_prefers_arg_then_stdin(self):
        self.assertEqual(rp.resolve_text("a", stdin_text="b"), "a")
        self.assertEqual(rp.resolve_text(None, stdin_text=" b \n"), "b")
        self.assertEqual(rp.main(["--text", "", "--reports-dir", "/tmp"]), 2)
