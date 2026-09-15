"""review — 주간 회계: 마크 vs 동결북, 이벤트 유형×action×mark20 전환율, wake·토큰, unregistered_pivot·rollover·overdue_decision, 무결성 지표표(integrity)·NA 규칙."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ledger as lg  # noqa: E402
import review as rv  # noqa: E402

ACCT = "acct1"
# 여기는 독립 오라클이다 — 기대 어휘를 직접 적고, 정본과 같은지 따로 못박는다(아래
# test_vocabulary_matches_canonical_source). 프로덕션에서 그냥 가져오면 어휘가 잘못 바뀌어도
# 테스트가 같이 따라가 아무것도 잡지 못한다.
LABELS = ["support_enter", "support_return", "support_break",
          "resistance_enter", "resistance_return", "resistance_break", "heartbeat"]
ACTIONS = ["enter", "add", "hold", "exit", "no_action"]
CK_FULL = {f"CK-{i}": "ok" for i in range(1, 8)}


def ev(evt, ts, **d):
    return {"evt": evt, "ts": ts, "account_id": ACCT, "schema": lg.SCHEMA, **d}


def dec(i, ts, label="support_return", action="enter", sym="336260", trigger="event", ck=None, **extra):
    d = {"decision_id": f"d{i}", "trigger": trigger, "event_label": label, "sym": sym, "checklist": CK_FULL if ck is None else ck, "action": action, "rationale": "r"}
    d.update(extra)
    return ev("decision", ts, **d)


def mark(date, sym, close, qty=10):
    return ev("mark", f"{date}T20:30:00", date=date, sym=sym, position_id=f"pos:{sym}", qty=qty, avg_px="1000", close=str(close), unrealized="0",
              dup_key=f"mark|{date}|{sym}")


def mark_row(trade_date, syms, nav=10000.0):
    return {"trade_date": trade_date, "ts": f"{trade_date}T20:30:00",
            "positions": [{"sym": s, "qty": 10, "avg_px": 1000.0, "close_px": 1000.0, "pl_amt": 0.0, "halted": False, "close_source": "broker"} for s in syms],
            "totals": {"nav": nav}}


def digest(ts, n=1):
    return {"ts": ts, "kind": "digest", "digest_id": f"{ACCT}|{ts}", "bar_ts": ts, "raw_event_count": n, "source": "rrr_stream", "delivered_to": "tmux"}


class ReviewComputeTest(unittest.TestCase):
    def compute(self, events, *, marks_rows=(), frozen=None, delivery=(), config=None, start="2026-06-01", end="2026-06-30", **kw):
        cfg = {"account_id": ACCT, "daily_token_cap": 1000}
        cfg.update(config or {})
        return rv.compute(list(events), marks_rows=list(marks_rows), frozen=frozen, delivery_rows=list(delivery), config=cfg, start=start, end=end, **kw)

    # --- 전환율 ---------------------------------------------------------------------
    def test_vocabulary_matches_canonical_source(self):
        """원장·리뷰의 어휘는 inbound_core 하나에서 온다.

        heartbeat 가 빠지면 그 이벤트로 내린 decision 이 전환율 행렬에서 조용히
        빠지고 label_missing 으로 잡힌다. tick 이 남으면 폐기된 타입이 통과한다.
        """
        self.assertEqual(list(lg.ZONE_EVENTS), LABELS)
        self.assertEqual(list(lg.ACTIONS), ACTIONS)
        self.assertEqual(list(rv.LABELS), LABELS)
        self.assertIn("heartbeat", lg.ZONE_EVENTS)
        # SUBSCRIBABLE_EVENT_TYPES 는 폐기됐다 — 유형으로 배달을 거르지 않으므로
        # "구독 가능한 유형"이라는 개념 자체가 없어졌다(감시 목록은 종목만 본다).
        self.assertFalse(hasattr(lg, "SUBSCRIBABLE_EVENT_TYPES"))

    def test_conversion_cells_cover_label_by_action_and_na_for_empty(self):
        r = self.compute([])
        self.assertEqual(len(r["conversion"]), len(LABELS) * len(ACTIONS))
        self.assertEqual(set(r["conversion"]), {f"{l}|{a}" for l in LABELS for a in ACTIONS})
        self.assertTrue(all(v == "NA" for v in r["conversion"].values()))

    def test_conversion_mark20_up_from_decision_px(self):
        events = [dec(1, "2026-06-01T10:35:00", px="1000"), mark("2026-06-29", "336260", 1100)]  # 06-01 + 20 거래일 = 06-29
        cell = self.compute(events)["conversion"]["support_return|enter"]
        self.assertEqual(cell, {"n": 1, "evaluated": 1, "up": 1, "ratio": 1.0, "partial": 0})

    def test_conversion_partial_when_window_incomplete(self):
        events = [dec(1, "2026-06-01T10:35:00", px="1000"), mark("2026-06-05", "336260", 900), mark("2026-06-10", "336260", 950)]
        cell = self.compute(events)["conversion"]["support_return|enter"]
        self.assertEqual(cell, {"n": 1, "evaluated": 1, "up": 0, "ratio": 0.0, "partial": 1})  # 최신 마크(06-10) 대비, partial 표시

    def test_conversion_reference_px_from_same_day_mark_when_no_px(self):
        events = [dec(1, "2026-06-01T10:35:00", action="hold"), mark("2026-06-01", "336260", 1000), mark("2026-06-29", "336260", 1200)]
        cell = self.compute(events)["conversion"]["support_return|hold"]
        self.assertEqual((cell["n"], cell["evaluated"], cell["up"]), (1, 1, 1))

    def test_conversion_counts_but_cannot_evaluate_without_marks(self):
        cell = self.compute([dec(1, "2026-06-01T10:35:00", action="no_action")])["conversion"]["support_return|no_action"]
        self.assertEqual(cell, {"n": 1, "evaluated": 0, "up": 0, "ratio": "NA", "partial": 0})

    def test_conversion_only_event_trigger_and_period(self):
        events = [dec(1, "2026-06-01T10:35:00", trigger="schedule"), dec(2, "2026-07-01T10:35:00"), dec(3, "2026-05-31T10:35:00")]
        r = self.compute(events)
        self.assertEqual(r["conversion"]["support_return|enter"], "NA")
        self.assertEqual(r["decision_count"], 1)  # 기간 안 결정 1(schedule)

    # --- 승급 산식 -------------------------------------------------------------------
    def test_checklist_completeness_and_label_missing(self):
        ck6 = {f"CK-{i}": "ok" for i in range(1, 7)}
        events = [dec(1, "2026-06-01T10:35:00"), dec(2, "2026-06-01T10:40:00", ck=ck6), dec(3, "2026-06-01T10:45:00", label="none"),
                  dec(4, "2026-06-01T10:50:00", trigger="schedule", label="none")]
        p = self.compute(events)["integrity"]
        self.assertEqual(p["checklist_completeness"], {"num": 3, "den": 4, "pct": 75.0})
        self.assertEqual(p["event_label_missing"], {"num": 1, "den": 3})  # trigger=event 3건 중 none 1건
        self.assertEqual(p["label_coverage"], {"seen": ["support_return"], "missing": [l for l in LABELS if l != "support_return"], "n_seen": 1, "of": len(LABELS)})

    def test_checklist_na_counts_as_filled(self):
        p = self.compute([dec(1, "2026-06-01T10:35:00", ck={f"CK-{i}": "NA" for i in range(1, 8)})])["integrity"]
        self.assertEqual(p["checklist_completeness"], {"num": 1, "den": 1, "pct": 100.0})

    def test_code_origin_orders(self):
        events = [ev("order", "2026-06-01T10:36:00", decision_ref="d1", sym="336260", side="buy", px="1000", qty=1, dup_key="o1"),
                  ev("order", "2026-06-01T10:37:00", sym="336260", side="buy", px="1000", qty=1, dup_key="o2")]
        self.assertEqual(self.compute(events)["integrity"]["code_origin_orders"], {"num": 1, "den": 2, "orders": ["o2"]})

    def test_safety_reject_na_when_zero_rejects(self):
        r = self.compute([])
        self.assertEqual(r["safety_reject_by_rule"], "NA")
        self.assertEqual(r["integrity"]["strategy_predicate_reject"], "NA")
        self.assertEqual(r["integrity"]["registry_hash"], "NA")
        self.assertEqual(r["integrity"]["reconciliation_mismatch"], "NA")

    def test_safety_reject_distribution_and_strategy_predicate(self):
        events = [ev("safety_check", "2026-06-01T10:36:00", order_ref="o1", result="reject", rule_id="account_alloc_cap", attempt_no=1),
                  ev("safety_check", "2026-06-01T10:37:00", order_ref="o1", result="reject", rule_id="ops_retry_limit", attempt_no=2),
                  ev("safety_check", "2026-06-01T10:38:00", order_ref="o2", result="reject", rule_id="zone_distance", attempt_no=1),
                  ev("safety_check", "2026-06-01T10:39:00", order_ref="o3", result="pass")]
        r = self.compute(events)
        self.assertEqual(r["safety_reject_by_rule"], {"account_alloc_cap": 1, "ops_retry_limit": 1, "zone_distance": 1})
        self.assertEqual(r["integrity"]["strategy_predicate_reject"], {"num": 1, "den": 3, "rule_ids": ["zone_distance"]})

    # --- wake·토큰 -------------------------------------------------------------------
    def test_wake_per_day_stats_nearest_rank(self):
        delivery = [digest("2026-06-01T10:35:00", 2), digest("2026-06-01T10:40:00", 1), digest("2026-06-01T10:45:00", 3), digest("2026-06-02T09:05:00", 1),
                    {"ts": "2026-06-02T09:06:00", "kind": "skip", "reason": "unsubscribed"}, digest("2026-07-02T09:05:00", 9)]
        w = self.compute([], delivery=delivery)["wake_count"]
        self.assertEqual(w["days"], {"2026-06-01": 3, "2026-06-02": 1})
        self.assertEqual((w["total"], w["p50"], w["p95"], w["max"], w["raw_event_total"]), (4, 1, 3, 3, 7))

    def test_token_usage_days_and_cap(self):
        events = [dec(1, "2026-06-01T10:35:00", token_usage={"input": 1000, "output": 200}), dec(2, "2026-06-01T10:40:00", token_usage={"input": 1000, "output": 200}),
                  dec(3, "2026-06-02T10:40:00", token_usage=500), dec(4, "2026-06-03T10:40:00")]
        t = self.compute(events)["token_usage"]
        self.assertEqual(t["days"], {"2026-06-01": 2400, "2026-06-02": 500})
        self.assertEqual((t["total"], t["p50"], t["p95"], t["max"]), (2900, 500, 2400, 2400))
        self.assertEqual(t["decisions_without_usage"], 1)
        p = self.compute(events)["integrity"]["token_cap"]
        self.assertEqual(p, {"cap": 1000, "max_day": 2400, "exceeded_days": ["2026-06-01"], "ok": False})

    def test_token_usage_empty(self):
        t = self.compute([])["token_usage"]
        self.assertEqual((t["days"], t["total"], t["p50"], t["p95"], t["max"]), ({}, 0, "NA", "NA", "NA"))
        self.assertEqual(self.compute([])["integrity"]["token_cap"]["ok"], "NA")

    # --- 시간 규율 집계 ----------------------------------------------------------------
    def flag(self, ts, kind="expired", sym="336260"):
        return ev("flag", ts, kind=kind, sym=sym, plan_id="p-336260-1", position_id="pos:336260", due=ts[:10], dup_key=f"flag|{ts[:10]}|{kind}|{ACCT}|{sym}")

    def test_overdue_decision_states(self):
        f1 = self.flag("2026-06-24T20:35:00")
        r = self.compute([f1], end="2026-06-26")
        self.assertEqual(r["overdue_decision"]["count"], 1)
        item = r["overdue_decision"]["items"][0]
        self.assertEqual((item["sym"], item["kind"], item["deadline"], item["status"]), ("336260", "expired", "2026-06-25T08:00:00", "overdue"))
        r = self.compute([f1], end="2026-06-24")  # 기한 전 = pending
        self.assertEqual((r["overdue_decision"]["count"], r["overdue_decision"]["pending"]), (0, 1))
        resolved = dec(9, "2026-06-24T22:00:00", trigger="flag", label="none", action="exit")
        r = self.compute([f1, resolved], end="2026-06-26")
        self.assertEqual((r["overdue_decision"]["count"], r["overdue_decision"]["items"][0]["status"]), (0, "resolved"))
        late = dec(9, "2026-06-25T09:00:00", trigger="flag", label="none", action="exit")  # 기한 뒤 결정 = 여전히 overdue(late)
        r = self.compute([f1, late], end="2026-06-26")
        self.assertEqual((r["overdue_decision"]["count"], r["overdue_decision"]["items"][0]["status"]), (1, "overdue_late"))

    def test_overdue_consecutive_daily_flags_collapse(self):
        events = [self.flag("2026-06-24T20:35:00"), self.flag("2026-06-25T20:35:00"), self.flag("2026-06-25T20:36:00", kind="recheck_due")]
        r = self.compute(events, end="2026-06-30")
        self.assertEqual(r["overdue_decision"]["count"], 2)
        self.assertEqual([i["deadline"] for i in r["overdue_decision"]["items"]], ["2026-06-25T08:00:00", "2026-06-26T08:00:00"])

    def test_overdue_resolved_by_exit_or_new_plan(self):
        f1 = self.flag("2026-06-24T20:35:00")
        ex = ev("exit", "2026-06-24T23:00:00", position_id="pos:336260", reason="expired", decision_ref="d9", sym="336260")
        self.assertEqual(self.compute([f1, ex], end="2026-06-30")["overdue_decision"]["count"], 0)
        pc = ev("plan_created", "2026-06-24T23:30:00", plan_id="p-336260-2", sym="336260", account_id=ACCT, entry_zone={"lo": 1, "hi": 2}, allocation_pct=5,
                horizon_d=10, conviction="중간", invalidation_conditions=["x"])
        r = self.compute([f1, pc], end="2026-06-30")
        self.assertEqual(r["overdue_decision"]["count"], 0)
        self.assertEqual(r["rollover_count"], 1)
        self.assertEqual(r["rollovers"], [{"plan_id": "p-336260-2", "sym": "336260", "after_flag": "2026-06-24T20:35:00"}])

    def test_rollover_needs_prior_expired_flag(self):
        pc = ev("plan_created", "2026-06-10T23:30:00", plan_id="p-005930-1", sym="005930", account_id=ACCT, entry_zone={"lo": 1, "hi": 2}, allocation_pct=5,
                horizon_d=10, conviction="중간", invalidation_conditions=["x"])
        self.assertEqual(self.compute([pc])["rollover_count"], 0)
        pc2 = dict(pc, plan_id="p-005930-2", ts="2026-06-11T23:30:00", rollover_of="p-005930-1")  # 명시 필드도 인정
        self.assertEqual(self.compute([pc, pc2])["rollover_count"], 1)

    def test_rule_changes_and_unregistered_pivots(self):
        rc = ev("rule_change", "2026-06-02T22:00:00", rule_id="rc-1", hypothesis="h", metric="m", n_required=30, until="2026-07-31", reject_condition="x",
                registered_at="2026-06-02T22:00:00", applies_from="2026-06-03")
        events = [rc, dec(1, "2026-06-03T10:35:00", rule_ref="rc-1"), dec(2, "2026-06-04T10:35:00", rule_ref="rc-9")]
        r = self.compute(events)
        self.assertEqual(r["rule_version"], "rc-1")
        self.assertEqual(r["rule_changes"], [{"rule_id": "rc-1", "registered_at": "2026-06-02T22:00:00", "applies_from": "2026-06-03"}])
        self.assertEqual(r["unregistered_pivots"], [{"ts": "2026-06-04T10:35:00", "evt": "decision", "ref": "d2", "rule_ref": "rc-9"}])
        self.assertEqual(self.compute([])["rule_version"], "none")

    def test_overdue_escalation(self):
        es = ev("escalation", "2026-06-03T10:40:00", decision_ref="d1", reason="ops_retry_limit", ack_by="2026-06-04T08:00:00", sym="336260")
        r = self.compute([es], end="2026-06-05")
        self.assertEqual(r["overdue_escalation"], {"count": 1, "items": [{"decision_ref": "d1", "ack_by": "2026-06-04T08:00:00", "status": "overdue"}]})
        ack = dec(5, "2026-06-03T21:00:00", trigger="operator", label="none", action="no_action", escalation_ref="d1")
        self.assertEqual(self.compute([es, ack], end="2026-06-05")["overdue_escalation"]["count"], 0)

    # --- 마크 ------------------------------------------------------------------------
    def test_marks_vs_frozen_and_mark_missing(self):
        rows = [mark_row("2026-06-01", ["005930"], nav=1000.0), mark_row("2026-06-03", ["005930"], nav=1100.0)]
        r = self.compute([], marks_rows=rows, frozen={"trade_date": "2026-06-01", "nav": 1000.0})
        self.assertEqual(r["marks_vs_frozen_book"], {"frozen_trade_date": "2026-06-01", "frozen_nav": 1000.0, "latest_trade_date": "2026-06-03", "latest_nav": 1100.0, "diff": 100.0})
        self.assertEqual(r["integrity"]["mark_missing"], {"count": 1, "cells": [{"date": "2026-06-02", "sym": "005930"}]})
        self.assertEqual(self.compute([], marks_rows=rows)["marks_vs_frozen_book"], "NA")
        self.assertEqual(self.compute([])["integrity"]["mark_missing"], {"count": 0, "cells": []})


class ReviewIoTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.paths = rv.Paths(ledger=os.path.join(self.d, "ledger.jsonl"), marks=os.path.join(self.d, "positions.jsonl"),
                              frozen=os.path.join(self.d, "frozen_book.json"), delivery=os.path.join(self.d, "delivery.jsonl"))
        lg.append_event(self.paths.ledger, "decision", {k: v for k, v in dec(1, "2026-06-01T10:35:00", px="1000", qty=1).items() if k not in ("evt", "ts", "account_id", "schema")},
                        account_id=ACCT, ts="2026-06-01T10:35:00")
        with open(self.paths.marks, "w", encoding="utf-8") as f:
            f.write(json.dumps(mark_row("2026-06-01", ["336260"], nav=1000.0)) + "\n")
        with open(self.paths.delivery, "w", encoding="utf-8") as f:
            f.write(json.dumps(digest("2026-06-01T10:35:00")) + "\n")
        self.cfgp = os.path.join(self.d, "config.json")
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump({"account_id": ACCT, "daily_token_cap": 1000}, f)

    def test_review_event_validates_and_write_is_idempotent(self):
        r = rv.run(self.paths, start="2026-06-01", end="2026-06-07", config={"account_id": ACCT, "daily_token_cap": 1000})
        e = rv.review_event(r)
        self.assertEqual(lg.validate_event("review", e), [])
        self.assertEqual(e["period"], {"start": "2026-06-01", "end": "2026-06-07"})
        self.assertEqual(e["dup_key"], "review|2026-06-01|2026-06-07")
        self.assertEqual(r["decision_count"], 1)
        self.assertEqual(r["wake_count"]["total"], 1)
        r1 = rv.run(self.paths, start="2026-06-01", end="2026-06-07", config={"account_id": ACCT, "daily_token_cap": 1000}, write=True)
        self.assertTrue(r1["written"])
        r2 = rv.run(self.paths, start="2026-06-01", end="2026-06-07", config={"account_id": ACCT, "daily_token_cap": 1000}, write=True)
        self.assertFalse(r2["written"])
        with open(self.paths.ledger, encoding="utf-8") as f:
            recs = [json.loads(ln) for ln in f]
        self.assertEqual([x["evt"] for x in recs], ["decision", "review"])
        self.assertEqual(lg.validate_file(self.paths.ledger), [])

    def test_cli_selftest_and_report(self):
        script = os.path.join(ROOT, "scripts", "review.py")
        self.assertEqual(subprocess.run([sys.executable, script, "--selftest"], capture_output=True, text=True).returncode, 0)
        out = subprocess.run([sys.executable, script, "--start", "2026-06-01", "--end", "2026-06-07", "--ledger", self.paths.ledger, "--marks", self.paths.marks,
                              "--frozen", self.paths.frozen, "--delivery", self.paths.delivery, "--config", self.cfgp], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        j = json.loads(out.stdout)
        self.assertIn("integrity", j)
        self.assertIn("conversion", j)
        self.assertEqual(j["period"], {"start": "2026-06-01", "end": "2026-06-07"})
        self.assertFalse(j["written"])


if __name__ == "__main__":
    unittest.main()
