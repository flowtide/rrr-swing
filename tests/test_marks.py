"""marks — 회계: 전 포지션 종가 마크, 동결북, halted carry, 소급, 원장 mark(accounting 역할), 멱등."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ledger as lg  # noqa: E402
import marks as mk  # noqa: E402


def payload(rows, entr="000001000000"):
    return {"entr": entr, "d2_entra": entr, "stk_acnt_evlt_prst": rows}


def row(sym="A005930", qty="000000000010", avg="000000100000", cur="000000100000", name="삼성전자"):
    q, a, c = int(qty), int(avg), int(cur)
    return {"stk_cd": sym, "stk_nm": name, "rmnd_qty": qty, "avg_prc": avg, "cur_prc": cur,
            "evlt_amt": str(q * c).zfill(12), "pur_amt": str(q * a).zfill(12), "pl_amt": str(q * (c - a)), "pl_rt": "0.00"}


class Build(unittest.TestCase):
    def test_no_trade_day_has_zero_activity_and_frozen_book(self):
        m1 = mk.build_mark(payload([row()]), prev=None, trade_date="2026-09-01", ts="2026-09-01T20:30:00")
        self.assertEqual(m1["totals"]["nav"], 2_000_000)
        self.assertIsNone(m1["frozen_book"])
        m2 = mk.build_mark(payload([row(cur="000000110000")]), prev=m1, trade_date="2026-09-02", ts="2026-09-02T20:30:00")
        self.assertEqual(m2["totals"]["nav"], 2_100_000)
        self.assertEqual(m2["activity"], 0.0)
        self.assertEqual(m2["frozen_book"], 2_100_000)
        self.assertEqual(m2["positions"][0]["close_source"], "broker")

    def test_supplier_close_overrides_and_missing_close_is_halted_carry(self):
        m1 = mk.build_mark(payload([row()]), prev=None, trade_date="2026-09-01")
        m2 = mk.build_mark(payload([row(cur="000000000000")]), prev=m1, trade_date="2026-09-02", closes={}, close_source="supplier_daily_candle")
        p = m2["positions"][0]
        self.assertTrue(p["halted"])
        self.assertEqual(p["close_px"], 100_000)  # 직전 마크 종가 carry(0 이나 NA 아님)
        self.assertEqual(p["close_source"], "carry")
        m3 = mk.build_mark(payload([row(cur="000000000000")]), prev=m2, trade_date="2026-09-03", closes={"005930": 123_000}, close_source="supplier_daily_candle")
        self.assertEqual(m3["positions"][0]["close_px"], 123_000)
        self.assertEqual(m3["positions"][0]["close_source"], "supplier_daily_candle")
        self.assertFalse(m3["positions"][0]["halted"])

    def test_ledger_mark_events_one_per_position(self):
        m = mk.build_mark(payload([row(), row(sym="A000660", qty="000000000005", avg="000000200000", cur="000000210000", name="하이닉스")]), prev=None, trade_date="2026-09-01")
        events = mk.mark_events(m)
        self.assertEqual([e["sym"] for e in events], ["005930", "000660"])
        self.assertEqual(events[1]["unrealized"], "50000")
        self.assertTrue(all(lg.validate_event("mark", e) == [] for e in events))


class Files(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.paths = mk.Paths(marks=os.path.join(self.d.name, "positions.jsonl"), ledger=os.path.join(self.d.name, "ledger.jsonl"),
                              frozen=os.path.join(self.d.name, "frozen_book.json"))

    def tearDown(self):
        self.d.cleanup()

    def test_write_freeze_report_and_idempotence(self):
        rc = mk.write_mark(payload([row()]), self.paths, account_id="acct1", trade_date="2026-09-01", ts="2026-09-01T20:30:00")
        self.assertEqual(rc, 0)
        self.assertEqual(mk.write_mark(payload([row()]), self.paths, account_id="acct1", trade_date="2026-09-01"), 3)  # 같은 거래일 재실행 → skip
        with open(self.paths.ledger, encoding="utf-8") as f:
            ledger_rows = [json.loads(x) for x in f]
        self.assertEqual([r["evt"] for r in ledger_rows], ["mark"])
        self.assertEqual(ledger_rows[0]["account_id"], "acct1")
        mk.freeze(self.paths, trade_date="2026-09-01")
        with open(self.paths.frozen, encoding="utf-8") as f:
            frozen = json.load(f)
        self.assertEqual(frozen["trade_date"], "2026-09-01"); self.assertEqual(frozen["nav"], 2_000_000)
        mk.write_mark(payload([row(cur="000000110000")]), self.paths, account_id="acct1", trade_date="2026-09-02")
        rep = mk.report(self.paths)
        self.assertEqual(rep["marks"], 2)
        self.assertEqual(rep["frozen_book"]["nav"], 2_000_000)
        self.assertEqual(rep["latest"]["nav"], 2_100_000)
        self.assertEqual(rep["vs_frozen_book"], 100_000)
        self.assertEqual(rep["missing_cells"], [])

    def test_backfill_date_and_missing_cells(self):
        mk.write_mark(payload([row()]), self.paths, account_id="a", trade_date="2026-09-03")
        mk.write_mark(payload([row()]), self.paths, account_id="a", trade_date="2026-09-01")  # 소급
        rep = mk.report(self.paths)
        self.assertEqual(rep["dates"], ["2026-09-01", "2026-09-03"])
        self.assertEqual(rep["missing_cells"], [{"date": "2026-09-02", "sym": "005930"}])  # 사이 거래일 마크 누락

    def test_cli_selftest_and_from_json(self):
        script = os.path.join(ROOT, "scripts", "marks.py")
        self.assertEqual(subprocess.run([sys.executable, script, "--selftest"], capture_output=True, text=True).returncode, 0)
        pj = os.path.join(self.d.name, "eval.json"); json.dump(payload([row()]), open(pj, "w"))
        out = subprocess.run([sys.executable, script, "--write", "--from-json", pj, "--date", "2026-09-01", "--marks", self.paths.marks,
                              "--ledger", self.paths.ledger, "--frozen", self.paths.frozen, "--account", "a"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('"nav": 2000000', out.stdout.replace(".0", ""))


if __name__ == "__main__":
    unittest.main()
