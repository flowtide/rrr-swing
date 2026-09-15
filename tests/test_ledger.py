"""ledger — 계좌별 append-only JSONL. 이벤트 스키마 검증·역할 게이트·dup_key 멱등·직렬 쓰기."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ledger as lg  # noqa: E402

# 원장 이벤트 스키마 픽스처 — scripts/ledger.py EVENT_SCHEMA 와 이 표를 함께 바꾼다(단일 지점).
EXPECTED_REQUIRED = {
    "plan_created": ["plan_id", "sym", "account_id", "entry_zone", "allocation_pct", "horizon_d", "conviction", "invalidation_conditions"],
    "plan_activated": ["plan_id"],
    # 감시 목록 선언 — 남은 필드는 종목뿐(세션·유형·만료 필터 폐기, 2026-09-15)
    "subscription": ["symbols"],
    "decision": ["decision_id", "trigger", "event_label", "sym", "checklist", "action", "rationale"],
    "order": ["decision_ref", "sym", "side", "px", "qty", "dup_key"],
    "safety_check": ["order_ref", "result"],
    "fill": ["position_id", "fill_px", "qty"],
    "cancel": ["ord", "qty_remaining"],
    "mark": ["date", "sym", "position_id", "qty", "avg_px", "close", "unrealized"],
    "coverage": ["sym", "plan_id", "status", "basis", "recheck_by", "as_of", "evidence"],
    "flag": ["kind", "ts"],
    "exit": ["position_id", "reason", "decision_ref"],
    "rule_change": ["rule_id", "hypothesis", "metric", "n_required", "until", "reject_condition", "registered_at", "applies_from"],
    "review": ["period", "rule_version", "marks_vs_frozen_book", "conversion", "wake_count", "token_usage", "unregistered_pivots", "rollover_count", "overdue_decision"],
    "escalation": ["decision_ref", "reason", "ack_by"],
    "consult": ["consult_id", "sym", "action", "status"],
}
AGENT_EVENTS = {"plan_created", "plan_activated", "subscription", "decision", "order", "coverage", "exit", "rule_change", "escalation", "consult"}
SAFETY_EVENTS = {"safety_check", "fill", "cancel"}
ACCOUNTING_EVENTS = {"mark", "flag", "review"}

CK = {f"CK-{i}": "NA" for i in range(1, 8)}


def decision(**over):
    base = {"decision_id": "d1", "trigger": "event", "event_label": "support_return", "bar_ts": "2026-06-18T10:35:00", "sym": "336260",
            "checklist": CK, "action": "hold", "rationale": "x"}
    base.update(over)
    return base


class Schema(unittest.TestCase):
    def test_schema_table_matches_d0(self):
        self.assertEqual({k: v["required"] for k, v in lg.EVENT_SCHEMA.items()}, EXPECTED_REQUIRED)
        self.assertEqual(set(lg.EVENT_SCHEMA) - AGENT_EVENTS - SAFETY_EVENTS - ACCOUNTING_EVENTS, set())
        self.assertEqual(lg.ROLE_EVENTS["agent"], AGENT_EVENTS)
        self.assertEqual(lg.ROLE_EVENTS["safety"], SAFETY_EVENTS)
        self.assertEqual(lg.ROLE_EVENTS["accounting"], ACCOUNTING_EVENTS)

    def test_decision_enums_and_checklist_keys(self):
        self.assertEqual(lg.validate_event("decision", decision()), [])
        self.assertTrue(lg.validate_event("decision", decision(action="buy")))
        self.assertTrue(lg.validate_event("decision", decision(trigger="cron")))
        self.assertTrue(lg.validate_event("decision", decision(event_label="breakout")))
        self.assertTrue(lg.validate_event("decision", decision(checklist={"CK-1": "x"})))  # CK-2..7 누락
        self.assertEqual(lg.validate_event("decision", decision(event_label="none", trigger="schedule", bar_ts=None)), [])
        self.assertTrue(lg.validate_event("decision", decision(action="enter")))  # enter 는 px·qty 필수
        self.assertEqual(lg.validate_event("decision", decision(action="enter", px="51000", qty=10)), [])

    def test_decision_checklist_na_values_pass(self):
        # CK-1..7 은 증거 라벨(키)일 뿐 — 값이 전부 "NA" 여도 통과한다(결측은 NA 로 적고 단독 근거로 쓰지 않는다). 파일 append 도 같다
        na = {f"CK-{i}": "NA" for i in range(1, 8)}
        self.assertEqual(lg.validate_event("decision", decision(checklist=na)), [])
        self.assertEqual(lg.validate_event("decision", decision(checklist=na, action="exit", px="47000", qty=10)), [])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            lg.append_event(path, "decision", decision(checklist=na), account_id="a", ts="2026-06-18T10:36:00")
            self.assertEqual(lg.validate_file(path), [])

    def test_consult_enums(self):
        # 협의 기록: status 4종·action 3종만. 그 밖은 거부. 값(px·qty·reason)은 스키마가 강제하지 않는다(도구가 검증)
        base = {"consult_id": "c-1", "sym": "336260", "action": "exit", "status": "pending"}
        self.assertEqual(lg.validate_event("consult", base), [])
        for st in ("pending", "agreed", "declined", "timeout"):
            self.assertEqual(lg.validate_event("consult", dict(base, status=st)), [], st)
        self.assertTrue(lg.validate_event("consult", dict(base, status="accepted")))
        self.assertTrue(lg.validate_event("consult", dict(base, action="buy_more")))
        self.assertTrue(lg.validate_event("consult", {k: v for k, v in base.items() if k != "consult_id"}))

    def test_other_enums(self):
        self.assertTrue(lg.validate_event("coverage", {"sym": "336260", "plan_id": "p", "status": "dead", "basis": {}, "recheck_by": "t", "as_of": "t", "evidence": "full"}))
        self.assertEqual(lg.validate_event("flag", {"kind": "expired", "ts": "t", "plan_id": "p"}), [])
        self.assertTrue(lg.validate_event("flag", {"kind": "expired", "ts": "t"}))  # plan_id 또는 position_id 중 하나 필수
        self.assertTrue(lg.validate_event("exit", {"position_id": "x", "reason": "boredom", "decision_ref": "d"}))
        self.assertTrue(lg.validate_event("plan_created", {"plan_id": "p", "sym": "336260", "account_id": "a", "entry_zone": {"lo": "1", "hi": "2"},
                                                            "allocation_pct": 10, "horizon_d": None, "conviction": "높음", "invalidation_conditions": []}))  # null 금지
        self.assertTrue(lg.validate_event("safety_check", {"order_ref": "o", "result": "reject"}))  # reject 는 rule_id 필수
        self.assertTrue(lg.validate_event("unknown", {}))


class AppendAndRoles(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.d.name, "ledger.jsonl")

    def tearDown(self):
        self.d.cleanup()

    def test_append_adds_envelope_and_validates(self):
        rec = lg.append_event(self.path, "decision", decision(), role="agent", account_id="acct1", ts="2026-06-18T10:36:00")
        self.assertEqual(rec["evt"], "decision")
        self.assertEqual(rec["schema"], lg.SCHEMA)
        self.assertEqual(rec["account_id"], "acct1")
        with open(self.path, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f]
        self.assertEqual(len(rows), 1)
        with self.assertRaises(lg.LedgerError):
            lg.append_event(self.path, "decision", decision(action="buy"), role="agent", account_id="acct1")

    def test_role_gate(self):
        with self.assertRaises(lg.LedgerError):
            lg.append_event(self.path, "mark", {"date": "d", "sym": "s", "position_id": "p", "qty": 1, "avg_px": "1", "close": "1", "unrealized": "0"}, role="agent", account_id="a")
        lg.append_event(self.path, "mark", {"date": "d", "sym": "s", "position_id": "p", "qty": 1, "avg_px": "1", "close": "1", "unrealized": "0"}, role="accounting", account_id="a")
        with self.assertRaises(lg.LedgerError):
            lg.append_event(self.path, "decision", decision(), role="accounting", account_id="a")
        with self.assertRaises(lg.LedgerError):
            lg.append_event(self.path, "fill", {"position_id": "p", "fill_px": "1", "qty": 1}, role="agent", account_id="a")

    def test_dup_key_is_idempotent(self):
        order = {"decision_ref": "d1", "sym": "336260", "side": "buy", "px": "51000", "qty": 10, "dup_key": "336260|buy|2026-06-18T10:35:00"}
        first = lg.append_event(self.path, "order", order, role="agent", account_id="a")
        second = lg.append_event(self.path, "order", order, role="agent", account_id="a")
        self.assertIsNotNone(first)
        self.assertIsNone(second)  # 같은 dup_key → 기록 없음
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(sum(1 for _ in f), 1)

    def test_validate_file_reports_bad_lines(self):
        lg.append_event(self.path, "decision", decision(), role="agent", account_id="a")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"evt": "decision", "schema": lg.SCHEMA, "ts": "t", "account_id": "a", "decision_id": "x"}) + "\n")
        problems = lg.validate_file(self.path)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["line"], 2)

    def test_cli_append_validate_tail_selftest(self):
        script = os.path.join(ROOT, "scripts", "ledger.py")
        base = [sys.executable, script, "--ledger", self.path]
        out = subprocess.run(base + ["append", "decision", "--json", json.dumps(decision()), "--account", "a"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        out = subprocess.run(base + ["append", "mark", "--json", json.dumps({"date": "d", "sym": "s", "position_id": "p", "qty": 1, "avg_px": "1", "close": "1", "unrealized": "0"}), "--account", "a"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)  # 역할 게이트
        out = subprocess.run(base + ["append", "mark", "--role", "accounting", "--json", json.dumps({"date": "d", "sym": "s", "position_id": "p", "qty": 1, "avg_px": "1", "close": "1", "unrealized": "0"}), "--account", "a"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        out = subprocess.run(base + ["validate"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        out = subprocess.run(base + ["tail", "-n", "1"], capture_output=True, text=True)
        self.assertIn('"evt": "mark"', out.stdout)
        out = subprocess.run([sys.executable, script, "selftest"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)

class EnvelopeTsOverlapTest(unittest.TestCase):
    """flag{kind, ts} 의 ts 는 봉투 ts 와 같은 값이다 — validate_file 이 봉투 ts 를 본문 검증에 넘겨야 한다."""

    def test_flag_appended_via_append_event_validates_in_file(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "ledger.jsonl")
        rec = lg.append_event(path, "flag", {"kind": "expired", "ts": "2026-06-24T20:30:00", "plan_id": "p1", "sym": "336260", "dup_key": "f1"},
                              role="accounting", account_id="acct1", ts="2026-06-24T20:30:00")
        self.assertEqual(rec["ts"], "2026-06-24T20:30:00")
        self.assertEqual(lg.validate_file(path), [])
