"""flags — 회계 시간 규율: 스토리 front matter(local/stories/<symbol>.md: horizon_d·recheck_by·status·activated_at)·포지션 → flag(expired·recheck_due). 결정 0, 1회/일 멱등."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import flags as fl  # noqa: E402
import ledger as lg  # noqa: E402

STORY = """---
symbol: 336260
plan_id: p-336260-1
thesis: 테스트 논지
entry_zone: {lo: 46000, hi: 47500}
target: 52000
invalidation_conditions:
  - "46000 종가 이탈"
  - 논지 훼손
horizon_d: 5
recheck_by: 2026-06-24
conviction: 중간
status: active
activated_at: 2026-06-17T21:00:00
as_of: 2026-06-17
---
# 336260 — 스토리
본문.
"""


def mark_row(trade_date, syms):
    return {"trade_date": trade_date, "ts": f"{trade_date}T20:30:00",
            "positions": [{"sym": s, "qty": 10, "avg_px": 1000.0, "close_px": 1000.0, "pl_amt": 0.0, "halted": False, "close_source": "broker"} for s in syms],
            "totals": {"nav": 10000.0}}


class FlagsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.stories = os.path.join(self.d, "stories")
        os.makedirs(self.stories)
        self.story_path = os.path.join(self.stories, "336260.md")
        with open(self.story_path, "w", encoding="utf-8") as f:
            f.write(STORY)
        self.marks = os.path.join(self.d, "positions.jsonl")
        self.ledger = os.path.join(self.d, "ledger.jsonl")
        self.cfg = {"account_id": "acct1", "position_expiry_days": 3}

    def paths(self):
        return fl.Paths(stories=self.stories, marks=self.marks, ledger=self.ledger)

    def write_story(self, text):
        with open(self.story_path, "w", encoding="utf-8") as f:
            f.write(text)

    def compute(self, date, **over):
        kw = dict(date=date, stories=fl.load_stories(self.stories), marks=[], ledger_events=[], config=self.cfg)
        kw.update(over)
        return fl.compute_flags(**kw)

    # --- front matter / 달력 -------------------------------------------------
    def test_front_matter_parse_subset(self):
        fm = fl.parse_front_matter(STORY)
        self.assertEqual(fm["plan_id"], "p-336260-1")
        self.assertEqual(fm["horizon_d"], 5)
        self.assertEqual(fm["entry_zone"], {"lo": 46000, "hi": 47500})
        self.assertEqual(fm["invalidation_conditions"], ["46000 종가 이탈", "논지 훼손"])
        self.assertEqual(fm["status"], "active")
        self.assertEqual(fm["recheck_by"], "2026-06-24")

    def test_load_stories_flat_dir_sym_from_front_matter_or_filename(self):
        stories = fl.load_stories(self.stories)
        self.assertEqual(len(stories), 1)
        st = stories[0]
        self.assertEqual((st["sym"], st["plan_id"], st.get("account_id")), ("336260", "p-336260-1", None))  # 계좌 디렉터리 없음(clone 당 계좌 하나)
        with open(os.path.join(self.stories, "005930.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: draft\n---\n")
        with open(os.path.join(self.stories, "README.txt"), "w", encoding="utf-8") as f:
            f.write("ignored")
        self.assertEqual([s["sym"] for s in fl.load_stories(self.stories)], ["005930", "336260"])
        self.assertEqual(fl.load_stories(os.path.join(self.d, "none")), [])

    def test_story_with_other_account_id_is_skipped(self):
        self.write_story(STORY.replace("plan_id: p-336260-1\n", "plan_id: p-336260-1\naccount_id: other\n"))
        self.assertEqual(self.compute("2026-06-30"), [])
        self.write_story(STORY.replace("plan_id: p-336260-1\n", "plan_id: p-336260-1\naccount_id: acct1\n"))
        self.assertIn("expired", [f["kind"] for f in self.compute("2026-06-30")])

    def test_trading_days_after_and_next(self):
        self.assertEqual(fl.trading_days_after("2026-06-17", 5), "2026-06-24")  # 목·금·월·화·수
        self.assertEqual(fl.trading_days_after("2026-06-17", 5, holidays=("2026-06-19",)), "2026-06-25")
        self.assertEqual(fl.next_trading_day("2026-06-19"), "2026-06-22")
        self.assertEqual(fl.trading_days_between("2026-06-24", "2026-06-24"), 0)
        self.assertEqual(fl.trading_days_between("2026-06-24", "2026-06-29"), 3)

    # --- expired -----------------------------------------------------------------
    def test_expired_on_due_day_not_before(self):
        self.assertEqual(self.compute("2026-06-23"), [])
        exp = [f for f in self.compute("2026-06-24") if f["kind"] == "expired"]
        self.assertEqual(len(exp), 1)
        self.assertEqual(exp[0]["plan_id"], "p-336260-1")
        self.assertEqual(exp[0]["sym"], "336260")
        self.assertEqual((exp[0]["anchor"], exp[0]["horizon_d"], exp[0]["due"], exp[0]["days_overdue"]), ("2026-06-17", 5, "2026-06-24", 0))
        self.assertEqual(exp[0]["horizon_source"], "story")

    def test_holiday_shifts_expiry_not_recheck(self):
        kinds = sorted(f["kind"] for f in self.compute("2026-06-24", holidays=("2026-06-19",)))
        self.assertEqual(kinds, ["recheck_due"])
        self.assertIn("expired", [f["kind"] for f in self.compute("2026-06-25", holidays=("2026-06-19",))])

    def test_inactive_statuses_no_flags(self):
        for st in ("draft", "expired", "cancelled", "invalidated"):
            self.write_story(STORY.replace("status: active", f"status: {st}"))
            self.assertEqual(self.compute("2026-06-30"), [], st)
        self.write_story(STORY.replace("status: active", "status: triggered"))
        self.assertIn("expired", [f["kind"] for f in self.compute("2026-06-30")])

    def test_story_without_horizon_uses_config_default(self):
        self.write_story(STORY.replace("horizon_d: 5\n", ""))
        exp = [f for f in self.compute("2026-06-22") if f["kind"] == "expired"]  # 06-17 + 3 거래일
        self.assertEqual(len(exp), 1)
        self.assertEqual((exp[0]["horizon_d"], exp[0]["horizon_source"]), (3, "config_default"))
        self.assertEqual(self.compute("2026-06-19"), [])

    def test_story_anchor_falls_back_to_ledger_activation(self):
        self.write_story(STORY.replace("activated_at: 2026-06-17T21:00:00\n", ""))
        act = {"evt": "plan_activated", "ts": "2026-06-18T21:00:00", "account_id": "acct1", "plan_id": "p-336260-1"}
        exp = [f for f in self.compute("2026-06-25", ledger_events=[act]) if f["kind"] == "expired"]
        self.assertEqual(len(exp), 1)
        self.assertEqual((exp[0]["anchor"], exp[0]["anchor_source"]), ("2026-06-18", "ledger_plan_activated"))
        self.assertEqual([f for f in self.compute("2026-06-24", ledger_events=[act]) if f["kind"] == "expired"], [])

    # --- recheck_due ---------------------------------------------------------------
    def test_recheck_due_boundary(self):
        self.assertNotIn("recheck_due", [f["kind"] for f in self.compute("2026-06-23")])
        self.assertEqual(sorted(f["kind"] for f in self.compute("2026-06-24")), ["expired", "recheck_due"])
        rc = [f for f in self.compute("2026-06-24") if f["kind"] == "recheck_due"][0]
        self.assertEqual((rc["plan_id"], rc["due"], rc["recheck_source"]), ("p-336260-1", "2026-06-24", "story"))

    def test_coverage_recheck_by_for_position(self):
        rows = [mark_row("2026-06-01", ["005930"])]
        cov = {"evt": "coverage", "ts": "2026-06-01T21:00:00", "account_id": "acct1", "sym": "005930", "plan_id": "p-005930-0", "status": "valid",
               "basis": "x", "recheck_by": "2026-06-03", "as_of": "2026-06-01", "evidence": "full"}
        rc = [f for f in self.compute("2026-06-03", marks=rows, ledger_events=[cov]) if f["kind"] == "recheck_due"]
        self.assertEqual(len(rc), 1)
        self.assertEqual((rc[0]["position_id"], rc[0]["due"], rc[0]["recheck_source"]), ("pos:005930", "2026-06-03", "ledger_coverage"))
        self.assertEqual([f for f in self.compute("2026-06-02", marks=rows, ledger_events=[cov]) if f["kind"] == "recheck_due"], [])
        cov2 = dict(cov, ts="2026-06-02T21:00:00", recheck_by="2026-06-10")  # 최신 coverage 가 미루면 그 값
        self.assertEqual([f for f in self.compute("2026-06-03", marks=rows, ledger_events=[cov, cov2]) if f["kind"] == "recheck_due"], [])

    # --- 포지션 ------------------------------------------------------------------
    def test_position_without_plan_anchor_is_first_mark_of_current_run(self):
        rows = [mark_row("2026-05-20", ["005930"]), mark_row("2026-05-21", []), mark_row("2026-06-01", ["005930"]), mark_row("2026-06-02", ["005930"])]
        exp = [f for f in self.compute("2026-06-04", marks=rows) if f["kind"] == "expired" and f["sym"] == "005930"]  # 06-01 + 3
        self.assertEqual(len(exp), 1)
        self.assertEqual((exp[0]["position_id"], exp[0]["anchor"], exp[0]["anchor_source"], exp[0]["horizon_source"]), ("pos:005930", "2026-06-01", "first_mark", "config_default"))
        self.assertNotIn("plan_id", exp[0])
        self.assertEqual([f for f in self.compute("2026-06-03", marks=rows) if f["sym"] == "005930"], [])

    def test_position_with_story_single_flag_both_ids(self):
        rows = [mark_row("2026-06-18", ["336260"])]
        exp = [f for f in self.compute("2026-06-24", marks=rows) if f["kind"] == "expired"]
        self.assertEqual(len(exp), 1)
        self.assertEqual((exp[0]["plan_id"], exp[0]["position_id"]), ("p-336260-1", "pos:336260"))

    def test_sold_position_not_flagged(self):
        rows = [mark_row("2026-06-01", ["005930"]), mark_row("2026-06-05", [])]
        self.assertEqual([f for f in self.compute("2026-06-10", marks=rows) if f["sym"] == "005930"], [])

    # --- 원장 기록 ----------------------------------------------------------------
    def test_flag_events_validate_and_dup_key(self):
        for f in self.compute("2026-06-24", marks=[mark_row("2026-06-18", ["336260"])]):
            self.assertEqual(lg.validate_event("flag", f), [])
            self.assertEqual(f["dup_key"], f"flag|2026-06-24|{f['kind']}|acct1|336260")
            self.assertEqual(f["ts"][:10], "2026-06-24")

    def test_run_writes_once_per_day_and_decides_nothing(self):
        with open(self.marks, "w", encoding="utf-8") as f:
            f.write(json.dumps(mark_row("2026-06-18", ["336260"])) + "\n")
        with open(self.story_path, "rb") as f:
            before = f.read()
        r1 = fl.run(self.paths(), date="2026-06-24", config=self.cfg)
        self.assertEqual((r1["appended"], r1["dup"]), (2, 0))
        r2 = fl.run(self.paths(), date="2026-06-24", config=self.cfg)
        self.assertEqual((r2["appended"], r2["dup"]), (0, 2))
        with open(self.ledger, encoding="utf-8") as f:
            recs = [json.loads(ln) for ln in f]
        self.assertEqual(len(recs), 2)
        self.assertEqual({r["evt"] for r in recs}, {"flag"})
        self.assertEqual({r["account_id"] for r in recs}, {"acct1"})
        self.assertEqual(lg.validate_file(self.ledger), [])
        with open(self.story_path, "rb") as f:
            self.assertEqual(f.read(), before)  # 스토리 파일 무변경(결정 0)
        r3 = fl.run(self.paths(), date="2026-06-25", config=self.cfg)  # 다음 날도 1회(기한 경과 집계용)
        self.assertEqual(r3["appended"], 2)
        self.assertEqual([f["days_overdue"] for f in r3["flags"] if f["kind"] == "expired"], [1])

    def test_cli_selftest_dry_run_and_write(self):
        script = os.path.join(ROOT, "scripts", "flags.py")
        self.assertEqual(subprocess.run([sys.executable, script, "--selftest"], capture_output=True, text=True).returncode, 0)
        with open(self.marks, "w", encoding="utf-8") as f:
            f.write(json.dumps(mark_row("2026-06-18", ["336260"])) + "\n")
        cfgp = os.path.join(self.d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f)
        base = [sys.executable, script, "--date", "2026-06-24", "--stories", self.stories, "--marks", self.marks, "--ledger", self.ledger, "--config", cfgp]
        out = subprocess.run(base + ["--dry-run"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        j = json.loads(out.stdout)
        self.assertEqual(sorted(f["kind"] for f in j["flags"]), ["expired", "recheck_due"])
        self.assertEqual(j["appended"], 0)
        self.assertFalse(os.path.exists(self.ledger))
        out = subprocess.run(base, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["appended"], 2)
        with open(self.ledger, encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
