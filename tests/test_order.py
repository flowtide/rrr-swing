"""test_order — W2 PR-1: order.py 축소 검증.

order.py 는 주문을 내지 않는다(브로커·MCP 호출 0).
역할은 셋뿐이다:
1. 집행 결과(ord_no, 상태, 3소스 대사 값, 5요소)를 판단 원장에 order/fill 로 기록
2. 사후 보고 1줄 생성(8항목 완비)
3. decision_ref 로 판단 원장 항목과 연결
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ledger as lg  # noqa: E402
import order as od  # noqa: E402


class OrderRecorderTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.d, "ledger.jsonl")
        self.reports_dir = os.path.join(self.d, "reports")
        os.makedirs(self.reports_dir, exist_ok=True)
        self.paths = od.Paths(
            ledger=self.ledger_path,
            reports=self.reports_dir,
        )
        self.cfg = {
            "account_id": "acct-test",
            "report": {
                "reports_dir": self.reports_dir,
                "order_format": "[order] {side_kr} {sym} {qty}주 @{px} {exchange} {status} (ord_no={ord_no}, decision_ref={decision_ref}, {time})",
            },
        }
        # 사전 판단 기록
        ck = {f"CK-{i}": "ok" for i in range(1, 8)}
        lg.append_event(
            self.ledger_path,
            "decision",
            {
                "decision_id": "d-101",
                "trigger": "event",
                "event_label": "support_return",
                "sym": "005930",
                "checklist": ck,
                "action": "enter",
                "px": "70000",
                "qty": 10,
                "rationale": "지지 확인",
            },
            account_id="acct-test",
            ts="2026-09-13T10:00:00",
        )

    def ledger_rows(self) -> list[dict]:
        if not os.path.exists(self.ledger_path):
            return []
        with open(self.ledger_path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_does_not_call_broker_or_place_orders(self):
        """핵심 규율: order.py 는 브로커를 호출하지 않으며 주문을 내지 않는다."""
        # 1. order.py 소스 내 브로커 주문 제출/대사 호출 부재 검증
        with open(os.path.join(ROOT, "bin", "order.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("broker_kiwoom", src)
        self.assertNotIn("kiwoom_sdk", src)
        self.assertNotIn("broker.place", src)
        self.assertNotIn("kiwoom_order_", src)

        # 2. 실행 시 브로커 인자가 주어지지 않아도 정상 기록
        out = od.record_order(
            decision_ref="d-101",
            sym="005930",
            side="buy",
            qty=10,
            px="70000",
            ord_no="12345",
            status="FILLED",
            cfg=self.cfg,
            paths=self.paths,
            now="2026-09-13T10:05:00",
        )
        self.assertEqual(out.rc, 0)
        self.assertEqual(out.result, "FILLED")

    def test_records_order_and_fill_in_ledger(self):
        """집행 결과(5요소 + ord_no + status)를 원장에 order 와 fill 로 기록."""
        reconcile_info = {
            "unfilled": {"ord_no": "12345", "oso_qty": 0},
            "filled": {"ord_no": "12345", "cntr_qty": 10, "cntr_pric": 70000},
            "status": "정상체결",
        }
        out = od.record_order(
            decision_ref="d-101",
            sym="005930",
            side="buy",
            qty=10,
            px="70000",
            exchange="KRX",
            ord_no="12345",
            status="FILLED",
            filled_qty=10,
            fill_px="70000",
            fee=70.0,
            tax=0.0,
            reconcile=reconcile_info,
            cfg=self.cfg,
            paths=self.paths,
            now="2026-09-13T10:05:00",
        )
        self.assertEqual(out.rc, 0)
        rows = self.ledger_rows()
        evts = [r["evt"] for r in rows]
        self.assertIn("order", evts)
        self.assertIn("fill", evts)

        # order 이벤트 검증
        ord_evt = next(r for r in rows if r["evt"] == "order")
        self.assertEqual(ord_evt["decision_ref"], "d-101")
        self.assertEqual(ord_evt["sym"], "005930")
        self.assertEqual(ord_evt["side"], "buy")
        self.assertEqual(ord_evt["px"], "70000")
        self.assertEqual(ord_evt["qty"], 10)
        self.assertEqual(ord_evt["exchange"], "KRX")
        self.assertEqual(ord_evt["ord_no"], "12345")
        self.assertEqual(ord_evt["status"], "FILLED")
        self.assertEqual(ord_evt["reconcile"], reconcile_info)

        # fill 이벤트 검증
        fill_evt = next(r for r in rows if r["evt"] == "fill")
        self.assertEqual(fill_evt["position_id"], "pos:005930")
        self.assertEqual(fill_evt["fill_px"], "70000")
        self.assertEqual(fill_evt["qty"], 10)
        self.assertEqual(fill_evt["ord_no"], "12345")
        self.assertEqual(fill_evt["fee"], 70.0)

        # 원장 스키마 전체 유효성 검증
        self.assertEqual(lg.validate_file(self.ledger_path), [])

    def test_links_with_decision_ref(self):
        """decision_ref 로 판단 원장 항목과 명확히 연결된다."""
        out = od.record_order(
            decision_ref="d-101",
            sym="005930",
            side="sell",
            qty=5,
            px="71000",
            ord_no="54321",
            status="FILLED",
            cfg=self.cfg,
            paths=self.paths,
            now="2026-09-13T10:10:00",
        )
        self.assertEqual(out.rc, 0)
        rows = self.ledger_rows()
        ord_evt = next(r for r in rows if r["evt"] == "order" and r.get("ord_no") == "54321")
        self.assertEqual(ord_evt["decision_ref"], "d-101")

    def test_generates_and_delivers_post_report(self):
        """사후 보고 1줄 생성 (8개 필수 필드 완비: sym, side, qty, px, exchange, ord_no, status, decision_ref)."""
        sent_reports = []

        def mock_reporter(text, reports_dir=None, now=None):
            sent_reports.append(text)

        out = od.record_order(
            decision_ref="d-101",
            sym="005930",
            side="buy",
            qty=10,
            px="70000",
            exchange="KRX",
            ord_no="12345",
            status="FILLED",
            cfg=self.cfg,
            paths=self.paths,
            now="2026-09-13T10:05:00",
            reporter=mock_reporter,
        )
        self.assertEqual(out.rc, 0)
        self.assertEqual(len(sent_reports), 1)
        line = sent_reports[0]
        for field in ("005930", "10", "70000", "KRX", "12345", "FILLED", "d-101"):
            self.assertIn(field, line)
        self.assertTrue("매수" in line or "buy" in line)

    def test_partial_fill_recording(self):
        """부분 체결 상태(status=PARTIAL, filled_qty < qty) 처리."""
        out = od.record_order(
            decision_ref="d-101",
            sym="005930",
            side="buy",
            qty=100,
            px="70000",
            ord_no="99999",
            status="PARTIAL",
            filled_qty=30,
            fill_px="70000",
            cfg=self.cfg,
            paths=self.paths,
            now="2026-09-13T10:05:00",
        )
        self.assertEqual(out.rc, 0)
        self.assertEqual(out.result, "PARTIAL")
        rows = self.ledger_rows()
        fill_evt = next(r for r in rows if r["evt"] == "fill" and r.get("ord_no") == "99999")
        self.assertEqual(fill_evt["qty"], 30)

    def test_accepted_unfilled_recording(self):
        """미체결 접수 상태(status=ACCEPTED, filled_qty=0)는 fill 이벤트를 남기지 않는다."""
        out = od.record_order(
            decision_ref="d-101",
            sym="005930",
            side="buy",
            qty=100,
            px="70000",
            ord_no="88888",
            status="ACCEPTED",
            filled_qty=0,
            cfg=self.cfg,
            paths=self.paths,
            now="2026-09-13T10:05:00",
        )
        self.assertEqual(out.rc, 0)
        self.assertEqual(out.result, "ACCEPTED")
        rows = self.ledger_rows()
        fill_evts = [r for r in rows if r["evt"] == "fill" and r.get("ord_no") == "88888"]
        self.assertEqual(fill_evts, [])
        ord_evt = next(r for r in rows if r["evt"] == "order" and r.get("ord_no") == "88888")
        self.assertEqual(ord_evt["status"], "ACCEPTED")

    def test_idempotent_recording(self):
        """동일 dup_key 재기록 시 원장에 중복 기록되지 않는다."""
        od.record_order(
            decision_ref="d-101",
            sym="005930",
            side="buy",
            qty=10,
            px="70000",
            ord_no="12345",
            status="FILLED",
            order_ref="order|d-101|005930|12345",
            cfg=self.cfg,
            paths=self.paths,
            now="2026-09-13T10:05:00",
        )
        count_before = len(self.ledger_rows())
        od.record_order(
            decision_ref="d-101",
            sym="005930",
            side="buy",
            qty=10,
            px="70000",
            ord_no="12345",
            status="FILLED",
            order_ref="order|d-101|005930|12345",
            cfg=self.cfg,
            paths=self.paths,
            now="2026-09-13T10:05:00",
        )
        count_after = len(self.ledger_rows())
        self.assertEqual(count_before, count_after)

    def test_records_from_existing_order_in_ledger(self):
        """이미 원장에 order 가 존재하는 경우 order-ref 로 찾아 fill 및 보고를 생성한다."""
        lg.append_event(
            self.ledger_path,
            "order",
            {
                "decision_ref": "d-101",
                "sym": "005930",
                "side": "buy",
                "px": "70000",
                "qty": 10,
                "exchange": "KRX",
                "dup_key": "existing-order-key",
            },
            account_id="acct-test",
            ts="2026-09-13T10:02:00",
        )
        out = od.record_order(
            order_ref="existing-order-key",
            ord_no="77777",
            status="FILLED",
            cfg=self.cfg,
            paths=self.paths,
            now="2026-09-13T10:05:00",
        )
        self.assertEqual(out.rc, 0)
        rows = self.ledger_rows()
        fill_evt = next(r for r in rows if r["evt"] == "fill" and r.get("ord_no") == "77777")
        self.assertEqual(fill_evt["qty"], 10)

    def test_cli_execution(self):
        """CLI 인자 전달을 통한 정상 기록 및 JSON 출력 검증."""
        cfg_file = os.path.join(self.d, "config.json")
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f)
        cmd = [
            sys.executable,
            os.path.join(ROOT, "bin", "order.py"),
            "--decision-ref", "d-101",
            "--sym", "005930",
            "--side", "buy",
            "--qty", "10",
            "--px", "70000",
            "--ord-no", "66666",
            "--status", "FILLED",
            "--config", cfg_file,
            "--ledger", self.ledger_path,
            "--json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data["result"], "FILLED")
        self.assertEqual(data["rc"], 0)

    def test_order_not_found_rejected(self):
        """원장에 없는 order-ref 를 참조하고 필수 5요소가 없으면 exit 30 오류."""
        out = od.record_order(
            order_ref="non-existent-order",
            paths=self.paths,
            cfg=self.cfg,
        )
        self.assertEqual(out.rc, 30)
        self.assertIn("order not found", out.payload.get("error", ""))

    def test_role_gate_analyst_rejected(self):
        """RS_ROLE=analyst 환경에서는 order.py 실행이 exit 30 으로 차단된다."""
        env = dict(os.environ, RS_ROLE="analyst")
        cmd = [
            sys.executable,
            os.path.join(ROOT, "bin", "order.py"),
            "--decision-ref", "d-101",
            "--sym", "005930",
            "--side", "buy",
            "--qty", "10",
            "--px", "70000",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 30)
        self.assertIn("analyst", res.stdout + res.stderr)

    def test_order_report_line_and_config_format(self):
        """사후 보고 1줄: DEFAULT_ORDER_FORMAT 기본 접두어 [order], 8항목 보장, order_format 및 direct_format 폴백 지원."""
        self.assertTrue(od.DEFAULT_ORDER_FORMAT.startswith("[order]"))
        self.assertNotIn("[direct]", od.DEFAULT_ORDER_FORMAT)
        # 1. order_report_line 호출
        line = od.order_report_line(
            od.DEFAULT_ORDER_FORMAT,
            sym="005930",
            side="buy",
            qty=10,
            px="70000",
            exchange="KRX",
            ord_no="12345",
            status="FILLED",
            decision_ref="d-101",
            time="10:00:00",
        )
        self.assertTrue(line.startswith("[order]"))
        self.assertNotIn("[direct]", line)
        for expected in ("005930", "매수", "10주", "@70000", "KRX", "FILLED", "12345", "d-101"):
            self.assertIn(expected, line)

        # 2. record_order 에서 order_format 설정 반영 확인
        sent = []
        od.record_order(
            decision_ref="d-101",
            sym="005930",
            side="buy",
            qty=10,
            px="70000",
            ord_no="55555",
            status="FILLED",
            cfg={"report": {"order_format": "[custom] {sym} {qty}"}},
            paths=self.paths,
            reporter=lambda text, **kw: sent.append(text),
        )
        self.assertTrue(sent[0].startswith("[custom] 005930 10"))


if __name__ == "__main__":
    unittest.main()
