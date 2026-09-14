"""consult — 손절·청산 협의 기록 도구: propose/pre/agree/decline/status/list. 파일(local/consults/<id>.json) + 원장 consult 이벤트.
주문을 만들지도 bin/order.py 를 호출하지도 않는다(timeout 뒤 집행 판단은 세션). 시간은 now 주입."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import consult as cs  # noqa: E402
import ledger as lg  # noqa: E402

NOW = "2026-06-25T11:05:00"


class ConsultBase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.paths = cs.Paths(consults=os.path.join(self.d, "consults"), ledger=os.path.join(self.d, "ledger.jsonl"), reports=os.path.join(self.d, "reports"))

    def rows(self):
        if not os.path.exists(self.paths.ledger):
            return []
        with open(self.paths.ledger, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def consults(self):
        return [r for r in self.rows() if r["evt"] == "consult"]

    def propose(self, **over):
        kw = dict(sym="336260", action="exit", px="45900", qty=100, reason="지지 이탈", now=NOW, account="acct")
        kw.update(over)
        return cs.propose(self.paths, **kw)

    def file(self, cid):
        with open(cs.consult_path(self.paths.consults, cid), encoding="utf-8") as f:
            return json.load(f)


class ProposeTest(ConsultBase):
    def test_propose_is_pending_with_deadline(self):
        rec = self.propose()
        self.assertEqual((rec["status"], rec["proposed_at"], rec["respond_by"], rec["pre"]), ("pending", NOW, "2026-06-25T11:35:00", False))
        self.assertTrue(rec["id"].startswith("c-336260-"))
        self.assertEqual(self.file(rec["id"])["status"], "pending")
        c = self.consults()
        self.assertEqual(len(c), 1)
        self.assertEqual((c[0]["consult_id"], c[0]["sym"], c[0]["action"], c[0]["status"], c[0]["px"], c[0]["qty"]), (rec["id"], "336260", "exit", "pending", "45900", 100))
        self.assertEqual(lg.validate_file(self.paths.ledger), [])

    def test_respond_min_override_and_unique_ids(self):
        a = self.propose(respond_min=10)
        self.assertEqual(a["respond_by"], "2026-06-25T11:15:00")
        b = self.propose(respond_min=10)  # 같은 시각·종목의 두 번째 제안도 id 충돌 없음
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(self.consults()), 2)

    def test_bad_action_or_numbers_refused(self):
        for bad in (dict(action="buy_more"), dict(action="EXIT"), dict(qty=0), dict(qty=1.5), dict(px="0"), dict(px="abc"), dict(sym=""), dict(reason="")):
            with self.assertRaises(cs.ConsultError, msg=bad):
                self.propose(**bad)
        self.assertFalse(os.path.exists(self.paths.consults) and os.listdir(self.paths.consults))
        self.assertEqual(self.consults(), [])

    def test_report_line_written_only_on_request(self):
        rec = self.propose()
        self.assertFalse(os.path.exists(self.paths.reports))
        rec = self.propose(report=True, now="2026-06-25T11:06:00")
        day = os.path.join(self.paths.reports, "2026-06-25.md")
        with open(day, encoding="utf-8") as f:
            text = f.read()
        for must in ("336260", "100주", "45900", "청산", rec["id"], rec["respond_by"]):
            self.assertIn(must, text)


class StatusTest(ConsultBase):
    def test_pending_before_deadline_records_nothing(self):
        rec = self.propose()
        s = cs.status(self.paths, rec["id"], now="2026-06-25T11:20:00", account="acct")
        self.assertEqual(s["status"], "pending")
        self.assertEqual(len(self.consults()), 1)

    def test_exact_deadline_is_not_timeout(self):
        rec = self.propose()
        self.assertEqual(cs.status(self.paths, rec["id"], now="2026-06-25T11:35:00", account="acct")["status"], "pending")

    def test_timeout_recorded_once(self):
        rec = self.propose()
        s = cs.status(self.paths, rec["id"], now="2026-06-25T11:36:00", account="acct")
        self.assertEqual((s["status"], s["timed_out_at"]), ("timeout", "2026-06-25T11:36:00"))
        self.assertEqual(self.file(rec["id"])["status"], "timeout")
        self.assertEqual([c["status"] for c in self.consults()], ["pending", "timeout"])
        s2 = cs.status(self.paths, rec["id"], now="2026-06-25T11:40:00", account="acct")
        self.assertEqual((s2["status"], s2["timed_out_at"]), ("timeout", "2026-06-25T11:36:00"))
        self.assertEqual([c["status"] for c in self.consults()], ["pending", "timeout"])  # 재호출 시 중복 기록 0
        self.assertEqual(lg.validate_file(self.paths.ledger), [])

    def test_agree_then_never_timeout(self):
        rec = self.propose()
        a = cs.respond(self.paths, rec["id"], "agree", note="ok", now="2026-06-25T11:10:00", account="acct")
        self.assertEqual((a["status"], a["responded_at"], a["note"]), ("agreed", "2026-06-25T11:10:00", "ok"))
        s = cs.status(self.paths, rec["id"], now="2026-06-25T12:00:00", account="acct")
        self.assertEqual(s["status"], "agreed")
        self.assertEqual([c["status"] for c in self.consults()], ["pending", "agreed"])

    def test_decline_and_state_rules(self):
        rec = self.propose()
        d = cs.respond(self.paths, rec["id"], "decline", note="지금은 보유", now="2026-06-25T11:10:00", account="acct")
        self.assertEqual(d["status"], "declined")
        self.assertEqual(cs.status(self.paths, rec["id"], now="2026-06-25T12:00:00", account="acct")["status"], "declined")
        with self.assertRaises(cs.ConsultError):
            cs.respond(self.paths, rec["id"], "agree", now="2026-06-25T11:11:00", account="acct")  # declined 뒤 agree 불가
        rec2 = self.propose(now="2026-06-25T11:20:00")
        cs.status(self.paths, rec2["id"], now="2026-06-25T11:51:00", account="acct")  # timeout
        with self.assertRaises(cs.ConsultError):
            cs.respond(self.paths, rec2["id"], "agree", now="2026-06-25T11:52:00", account="acct")  # timeout 뒤 agree 불가(새 제안으로)
        with self.assertRaises(cs.ConsultError):
            cs.respond(self.paths, rec2["id"], "maybe", now="2026-06-25T11:52:00", account="acct")

    def test_unknown_id(self):
        with self.assertRaises(cs.ConsultError):
            cs.status(self.paths, "nope", now=NOW, account="acct")


class PreTest(ConsultBase):
    def test_pre_is_agreed_and_never_times_out(self):
        rec = cs.pre(self.paths, sym="336260", level="46000", qty=100, reason="스토리 무효화 조건", now="2026-06-25T08:30:00", account="acct")
        self.assertEqual((rec["status"], rec["pre"], rec["action"], rec["px"], rec["level"], rec["respond_by"]), ("agreed", True, "exit", "46000", "46000", None))
        c = self.consults()
        self.assertEqual((len(c), c[0]["status"], c[0]["pre"]), (1, "agreed", True))
        self.assertEqual(cs.status(self.paths, rec["id"], now="2026-06-30T15:00:00", account="acct")["status"], "agreed")
        self.assertEqual(len(self.consults()), 1)
        d = cs.respond(self.paths, rec["id"], "decline", note="철회", now="2026-06-25T09:00:00", account="acct")  # 운영자는 사전 합의를 철회할 수 있다
        self.assertEqual(d["status"], "declined")

    def test_pre_reduce(self):
        rec = cs.pre(self.paths, sym="336260", level="46000", qty=50, reason="절반", action="reduce", now="2026-06-25T08:30:00", account="acct")
        self.assertEqual(rec["action"], "reduce")
        with self.assertRaises(cs.ConsultError):
            cs.pre(self.paths, sym="336260", level="46000", qty=50, reason="x", action="hold", now="2026-06-25T08:30:00", account="acct")  # 사전 합의는 집행 행동만


class ListTest(ConsultBase):
    def test_list_open_only(self):
        a = self.propose(now="2026-06-25T11:00:00")
        b = cs.pre(self.paths, sym="005930", level="70000", qty=10, reason="r", now="2026-06-25T08:30:00", account="acct")
        c = self.propose(sym="000660", now="2026-06-25T11:01:00")
        cs.respond(self.paths, c["id"], "decline", now="2026-06-25T11:02:00", account="acct")
        d = self.propose(sym="035420", now="2026-06-25T10:00:00")
        cs.status(self.paths, d["id"], now="2026-06-25T10:31:00", account="acct")  # timeout
        e = self.propose(sym="068270", now="2026-06-25T10:20:00")  # 기한 경과했지만 status 로 확인 전 → 아직 pending, overdue 표시
        open_ = cs.list_consults(self.paths, now="2026-06-25T11:05:00")
        self.assertEqual([x["id"] for x in open_], [b["id"], e["id"], a["id"]])  # proposed_at 순
        by = {x["id"]: x for x in open_}
        self.assertEqual((by[a["id"]]["overdue"], by[e["id"]]["overdue"], by[b["id"]]["overdue"]), (False, True, False))
        self.assertEqual(len(cs.list_consults(self.paths, now="2026-06-25T11:05:00", all=True)), 5)
        self.assertEqual(self.file(e["id"])["status"], "pending")  # list 는 읽기 전용(timeout 전이는 status 만)
        self.assertEqual(cs.list_consults(cs.Paths(consults=os.path.join(self.d, "none"), ledger=self.paths.ledger, reports=self.paths.reports), now=NOW), [])


class BoundaryTest(ConsultBase):
    def test_consult_source_never_touches_orders_or_broker(self):
        src = open(os.path.join(ROOT, "bin", "consult.py"), encoding="utf-8").read().lower()
        for tok in ("import order", "order.py --", "broker", "kiwoom", "place(side", "entry_zone", "event_label", "support_", "resistance_", "program_", "thesis"):  # "place(" 단독은 os.replace 에 매칭
            self.assertNotIn(tok, src.replace("bin/order.py 를 호출하지도", ""), tok)

    def test_cli_roundtrip(self):
        script = os.path.join(ROOT, "bin", "consult.py")
        base = [sys.executable, script, "--consults-dir", self.paths.consults, "--ledger", self.paths.ledger, "--reports-dir", self.paths.reports, "--account", "acct"]
        out = subprocess.run(base + ["propose", "--sym", "336260", "--action", "reduce", "--px", "45900", "--qty", "50", "--reason", "절반 정리", "--now", NOW, "--report"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        rec = json.loads(out.stdout)
        self.assertEqual((rec["status"], rec["action"]), ("pending", "reduce"))
        out = subprocess.run(base + ["status", rec["id"], "--now", "2026-06-25T11:20:00"], capture_output=True, text=True)
        self.assertEqual((out.returncode, json.loads(out.stdout)["status"]), (0, "pending"))
        out = subprocess.run(base + ["agree", rec["id"], "--note", "ok", "--now", "2026-06-25T11:21:00"], capture_output=True, text=True)
        self.assertEqual((out.returncode, json.loads(out.stdout)["status"]), (0, "agreed"), out.stderr)
        out = subprocess.run(base + ["list", "--now", "2026-06-25T11:22:00"], capture_output=True, text=True)
        self.assertEqual([x["id"] for x in json.loads(out.stdout)], [rec["id"]])
        out = subprocess.run(base + ["propose", "--sym", "336260", "--action", "sell_all", "--px", "1", "--qty", "1", "--reason", "x", "--now", NOW], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        out = subprocess.run(base + ["status", "nope"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertEqual(lg.validate_file(self.paths.ledger), [])

    def test_analyst_role_refused(self):
        env = dict(os.environ, RS_ROLE="analyst")
        out = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "consult.py"), "--consults-dir", self.paths.consults, "--ledger", self.paths.ledger, "list"], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 30)
        self.assertFalse(os.path.exists(self.paths.ledger))


if __name__ == "__main__":
    unittest.main()
