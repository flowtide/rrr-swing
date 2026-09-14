"""subscribe.py 단위 테스트 — 네트워크 0."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import subscribe as sb  # noqa: E402


class SetShowClear(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.d.name, "subscriptions.json")
        self.ledger = os.path.join(self.d.name, "ledger.jsonl")

    def tearDown(self):
        self.d.cleanup()

    def test_set_writes_declaration(self):
        rec = sb.set_subscription(self.path, account_id="acct1", symbols=["336260", "003230"],
                                  events=["support_return", "resistance_break"], sessions=["REG_KRX_NXT"],
                                  expires="2026-06-18T20:00:00", now="2026-06-17T21:00:00")
        saved = json.load(open(self.path, encoding="utf-8"))
        self.assertEqual(saved["symbols"], ["336260", "003230"])
        self.assertEqual(saved["event_types"], ["support_return", "resistance_break"])
        self.assertEqual(saved["sessions"], ["REG_KRX_NXT"])
        self.assertEqual(saved["expires_at"], "2026-06-18T20:00:00")
        self.assertEqual(saved["declared_at"], "2026-06-17T21:00:00")
        self.assertEqual(rec["account_id"], "acct1")

    def test_set_rejects_unknown_event_type(self):
        with self.assertRaises(ValueError):
            sb.set_subscription(self.path, account_id="a", symbols=["336260"], events=["buy_now"],
                                sessions=["REG_KRX_NXT"], expires="2026-06-18T20:00:00")

    def test_set_rejects_bad_symbol_or_session_or_expiry(self):
        with self.assertRaises(ValueError):
            sb.set_subscription(self.path, account_id="a", symbols=["33626"], events=["heartbeat"], sessions=["REG_KRX_NXT"], expires="2026-06-18T20:00:00")
        with self.assertRaises(ValueError):
            sb.set_subscription(self.path, account_id="a", symbols=["336260"], events=["heartbeat"], sessions=["LUNCH"], expires="2026-06-18T20:00:00")
        with self.assertRaises(ValueError):
            sb.set_subscription(self.path, account_id="a", symbols=["336260"], events=["heartbeat"], sessions=["REG_KRX_NXT"], expires="tomorrow")

    def test_append_ledger_writes_subscription_event_line(self):
        sb.set_subscription(self.path, account_id="acct1", symbols=["336260"], events=["macro"], sessions=["POST_NXT"],
                            expires="2026-06-18T20:00:00", now="2026-06-17T21:00:00", ledger_path=self.ledger)
        rows = [json.loads(x) for x in open(self.ledger, encoding="utf-8")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["evt"], "subscription")
        self.assertEqual(rows[0]["account_id"], "acct1")
        self.assertEqual(rows[0]["symbols"], ["336260"])
        self.assertEqual(rows[0]["event_types"], ["macro"])
        self.assertEqual(rows[0]["ts"], "2026-06-17T21:00:00")

    def test_show_and_clear(self):
        sb.set_subscription(self.path, account_id="a", symbols=["336260"], events=["heartbeat"], sessions=["REG_KRX_NXT"], expires="2026-06-18T20:00:00")
        self.assertEqual(sb.show(self.path)["symbols"], ["336260"])
        sb.clear(self.path)
        self.assertEqual(sb.show(self.path)["symbols"], [])

    def test_watch_flag_removed_from_cli(self):
        with self.assertRaises(SystemExit):
            sb.main(["set", "--watch", "--symbols", "336260", "--events", "support_return", "--expires", "2026-06-18T20:00:00"])
        self.assertFalse(hasattr(sb, "register_watch"))


if __name__ == "__main__":
    unittest.main()
