#!/usr/bin/env python3
"""order — 집행 결과 기록 및 사후 보고 (주문 제출 0).

주문은 rs-exec 가 gw MCP 주문 도구로 직접 낸다(D1). 이 스크립트는 브로커를 호출하지 않으며,
집행 결과(ord_no, 상태, 3소스 대사 값, 5요소)를 판단 원장에 order/fill 로 기록하고 사후 보고 1줄을 생성한다.

역할(3가지):
1. 집행 결과(ord_no, 상태, 3소스 대사 값, 5요소)를 판단 원장에 order/fill 로 기록
2. 사후 보고 1줄 생성(8항목 완비)
3. decision_ref 로 판단 원장 항목과 연결

종료 코드:
0: 정상 기록 및 보고 완료
30: 입력 오류, 설정 오류, 또는 권한 거부(RS_ROLE=analyst)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
sys.path.insert(0, HERE)

import ledger as lg  # noqa: E402

ORDER_REPORT_FIELDS = ("sym", "side", "qty", "px", "exchange", "ord_no", "status", "decision_ref")  # 사후 보고 필수 8항목
DEFAULT_ORDER_FORMAT = "[order] {side_kr} {sym} {qty}주 @{px} {exchange} {status} (ord_no={ord_no}, decision_ref={decision_ref}, {time})"


@dataclass
class Paths:
    ledger: str = "local/ledger.jsonl"
    reports: str = "local/reports"


@dataclass
class Outcome:
    rc: int
    result: str
    payload: dict


def order_report_line(fmt: str, **fields) -> str:
    """사후 보고 1줄. 템플릿이 8항목 중 빠뜨린 것은 끝에 key=value 로 붙여 항상 8항목이 들어간다."""
    f = {k: "" for k in ORDER_REPORT_FIELDS}
    f["time"] = ""
    f.update(fields)
    f["side_kr"] = "매수" if str(f.get("side", "")).lower() == "buy" else "매도"
    try:
        line = fmt.format(**f)
    except (KeyError, IndexError, ValueError):
        line = DEFAULT_ORDER_FORMAT.format(**f)
    missing = [k for k in ORDER_REPORT_FIELDS if f.get(k) and str(f.get(k)) not in line and not (k == "side" and f["side_kr"] in line)]
    if missing:
        line += " " + " ".join(f"{k}={f.get(k)}" for k in missing)
    return line




def role_gate() -> str | None:
    """RS_ROLE=analyst(분석 보조)는 주문 기록 권한이 없다(exit 30)."""
    if os.environ.get("RS_ROLE", "").strip().lower() == "analyst":
        return "RS_ROLE=analyst: 분석 역할은 주문 권한이 없다(exit 30). 주문은 세션 역할(rs-lead)의 bin/order.py 만"
    return None


def _plain(v) -> str:
    d = Decimal(str(v))
    return str(d.quantize(Decimal(1))) if d == d.to_integral_value() else str(d.normalize())


def find_order(events: list[dict], order_ref: str) -> dict | None:
    hit = None
    for e in events:
        if e.get("evt") == "order" and (str(e.get("dup_key")) == str(order_ref) or (hit is None and str(e.get("decision_ref")) == str(order_ref))):
            hit = e
    return hit


def record_order(
    *,
    decision_ref: str | None = None,
    sym: str | None = None,
    side: str | None = None,
    qty: int | None = None,
    px: str | float | Decimal | None = None,
    exchange: str = "KRX",
    ord_no: str | None = None,
    status: str | None = None,
    filled_qty: int | None = None,
    fill_px: str | float | Decimal | None = None,
    fee: float = 0.0,
    tax: float = 0.0,
    reconcile: dict | str | None = None,
    order_ref: str | None = None,
    cfg: dict | None = None,
    paths: Paths | None = None,
    now: str | None = None,
    reporter=None,
) -> Outcome:
    """집행 결과를 원장에 기록하고 사후 보고를 생성한다 (브로커 호출 0)."""
    cfg = cfg or {}
    paths = paths or Paths()
    now = now or datetime.now().isoformat(timespec="seconds")
    account = str(cfg.get("account_id") or "")
    rcfg = cfg.get("report") or {}

    events = lg._read_all(paths.ledger) if os.path.exists(paths.ledger) else []

    # 1. 5요소 결정 (order_ref 로 기존 order 조회 지원)
    if not sym and order_ref:
        existing = find_order(events, order_ref)
        if existing is None:
            return Outcome(30, "ERROR", {"result": "ERROR", "rc": 30, "error": f"order not found in ledger: {order_ref}"})
        decision_ref = decision_ref or existing.get("decision_ref")
        sym = existing.get("sym")
        side = side or existing.get("side")
        qty = qty if qty is not None else existing.get("qty")
        px = px if px is not None else existing.get("px")
        exchange = exchange or existing.get("exchange", "KRX")
        ord_no = ord_no or existing.get("ord_no", "-")
        status = status or existing.get("status", "FILLED")

    if not sym or not side or qty is None or px is None or not decision_ref:
        return Outcome(30, "ERROR", {"result": "ERROR", "rc": 30, "error": f"missing required order fields (decision_ref={decision_ref}, sym={sym}, side={side}, qty={qty}, px={px})"})

    side = str(side).lower()
    sym = str(sym).strip()
    qty = int(qty)
    px_str = _plain(px)
    exchange = str(exchange or "KRX").strip()
    ord_no = str(ord_no or "-").strip()

    # status 및 filled_qty 정규화
    if status is None:
        if filled_qty is not None and filled_qty >= qty:
            status = "FILLED"
        elif filled_qty is not None and filled_qty > 0:
            status = "PARTIAL"
        else:
            status = "ACCEPTED"
    status = str(status).upper()

    if filled_qty is None:
        filled_qty = qty if status == "FILLED" else 0
    else:
        filled_qty = int(filled_qty)

    fill_px_str = _plain(fill_px or px_str)
    order_ref = order_ref or f"order|{decision_ref}|{sym}|{ord_no}"

    # 2. 원장 order 기록 (agent 역할)
    existing_order = next((e for e in events if e.get("evt") == "order" and str(e.get("dup_key")) == str(order_ref)), None)
    order_view = {
        "decision_ref": decision_ref,
        "sym": sym,
        "side": side,
        "px": px_str,
        "qty": qty,
        "exchange": exchange,
        "ord_no": ord_no,
        "status": status,
        "dup_key": order_ref,
    }
    if reconcile is not None:
        order_view["reconcile"] = reconcile

    if not existing_order:
        lg.append_event(paths.ledger, "order", order_view, role="agent", account_id=account, ts=now)

    # 3. 체결이 있으면 fill 기록 (safety 역할)
    fill_view = None
    if filled_qty > 0:
        fill_dup_key = f"fill|{ord_no}|{filled_qty}"
        existing_fill = next((e for e in events if e.get("evt") == "fill" and str(e.get("dup_key")) == fill_dup_key), None)
        fill_view = {
            "position_id": f"pos:{sym}",
            "fill_px": fill_px_str,
            "qty": filled_qty,
            "ord_no": ord_no,
            "sym": sym,
            "side": side,
            "order_ref": order_ref,
            "fee": float(fee),
            "tax": float(tax),
            "cum_filled_qty": filled_qty,
            "dup_key": fill_dup_key,
        }
        if not existing_fill:
            lg.append_event(paths.ledger, "fill", fill_view, role="safety", account_id=account, ts=now)

    # 4. 사후 보고 1줄 생성
    reports_dir = str(rcfg.get("reports_dir") or paths.reports)
    report_format = str(rcfg.get("order_format") or DEFAULT_ORDER_FORMAT)
    report_line = order_report_line(
        report_format,
        sym=sym,
        side=side,
        qty=qty,
        px=px_str,
        exchange=exchange,
        ord_no=ord_no,
        status=status,
        decision_ref=decision_ref,
        time=now[11:19],
    )

    report_out = {"sent": False, "text": report_line}
    try:
        if reporter is not None:
            reporter(report_line, reports_dir=reports_dir, now=now)
            report_out["sent"] = True
        else:
            import report as rp
            rp.write_report(report_line, reports_dir=reports_dir, now=now)
            report_out["sent"] = True
    except Exception as exc:  # noqa: BLE001 - 보고 실패는 원장 기록과 독립
        report_out["error"] = f"{type(exc).__name__}: {exc}"

    payload = {
        "result": status,
        "rc": 0,
        "order_ref": order_ref,
        "decision_ref": decision_ref,
        "sym": sym,
        "side": side,
        "qty": qty,
        "px": px_str,
        "exchange": exchange,
        "ord_no": ord_no,
        "status": status,
        "filled_qty": filled_qty,
        "order": order_view,
        "fill": fill_view,
        "report_line": report_line,
        "report": report_out,
    }
    return Outcome(0, status, payload)


# 하위 호환용 run() 함수 (main() 및 기존 테스트/스크립트용 인터페이스)
def run(
    order_ref: str | None = None,
    *,
    cfg: dict | None = None,
    paths: Paths | None = None,
    broker=None,  # noqa: ARG001 - 브로커 호출 0 (무시)
    now: str | None = None,
    reporter=None,
    decision_ref: str | None = None,
    sym: str | None = None,
    side: str | None = None,
    qty: int | None = None,
    px: str | None = None,
    exchange: str = "KRX",
    ord_no: str | None = None,
    status: str | None = None,
    filled_qty: int | None = None,
    fill_px: str | None = None,
    fee: float = 0.0,
    tax: float = 0.0,
    reconcile: dict | str | None = None,
) -> Outcome:
    return record_order(
        decision_ref=decision_ref,
        sym=sym,
        side=side,
        qty=qty,
        px=px,
        exchange=exchange,
        ord_no=ord_no,
        status=status,
        filled_qty=filled_qty,
        fill_px=fill_px,
        fee=fee,
        tax=tax,
        reconcile=reconcile,
        order_ref=order_ref,
        cfg=cfg,
        paths=paths,
        now=now,
        reporter=reporter,
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="order", description="집행 결과 기록 및 사후 보고 (주문 제출 0)")
    p.add_argument("--decision-ref", default=None, help="원장 decision 의 decision_id")
    p.add_argument("--order-ref", default=None, help="원장 order 의 dup_key (미지정 시 자동 생성)")
    p.add_argument("--ord-no", default=None, help="브로커 주문번호")
    p.add_argument("--status", default=None, help="집행 상태 (FILLED, PARTIAL, ACCEPTED, REJECTED 등)")
    p.add_argument("--sym", default=None, help="종목코드 (6자리)")
    p.add_argument("--side", choices=["buy", "sell"], default=None, help="매매 구분")
    p.add_argument("--qty", type=int, default=None, help="주문 수량")
    p.add_argument("--px", default=None, help="주문 가격")
    p.add_argument("--exchange", choices=["KRX", "NXT"], default="KRX", help="거래소")
    p.add_argument("--filled-qty", type=int, default=None, help="체결 수량")
    p.add_argument("--fill-px", default=None, help="체결 평균 단가")
    p.add_argument("--fee", type=float, default=0.0, help="수수료")
    p.add_argument("--tax", type=float, default=0.0, help="제세금")
    p.add_argument("--reconcile", default=None, help="3소스 대사 결과 (JSON 또는 텍스트)")
    p.add_argument("--config", default="config/config.json")
    p.add_argument("--ledger", default="local/ledger.jsonl")
    p.add_argument("--reports-dir", default=None, help="보고서 디렉토리")
    p.add_argument("--now", default=None)
    p.add_argument("--json", action="store_true", help="전체 payload 출력")

    # 호환성용 dummy 옵션 (무시)
    p.add_argument("--broker", default=None, help=argparse.SUPPRESS)
    p.add_argument("--state", default=None, help=argparse.SUPPRESS)

    a = p.parse_args(argv)
    denied = role_gate()
    if denied:
        print(json.dumps({"result": "ERROR", "rc": 30, "error": denied}, ensure_ascii=False))
        return 30

    cfg = {}
    if os.path.exists(a.config):
        try:
            with open(a.config, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError) as exc:
            print(json.dumps({"result": "ERROR", "rc": 30, "error": f"config: {exc}"}, ensure_ascii=False))
            return 30

    paths = Paths(
        ledger=a.ledger,
        reports=a.reports_dir or (cfg.get("report") or {}).get("reports_dir") or "local/reports",
    )

    reconcile_val = a.reconcile
    if isinstance(reconcile_val, str) and (reconcile_val.startswith("{") or reconcile_val.startswith("[")):
        try:
            reconcile_val = json.loads(reconcile_val)
        except ValueError:
            pass

    out = record_order(
        decision_ref=a.decision_ref,
        sym=a.sym,
        side=a.side,
        qty=a.qty,
        px=a.px,
        exchange=a.exchange,
        ord_no=a.ord_no,
        status=a.status,
        filled_qty=a.filled_qty,
        fill_px=a.fill_px,
        fee=a.fee,
        tax=a.tax,
        reconcile=reconcile_val,
        order_ref=a.order_ref,
        cfg=cfg,
        paths=paths,
        now=a.now,
    )

    if a.json:
        print(json.dumps(out.payload, ensure_ascii=False, default=str))
    else:
        pl = out.payload
        print(json.dumps({k: pl.get(k) for k in ("result", "rc", "order_ref", "decision_ref", "ord_no", "status", "report_line", "error")
                          if pl.get(k) is not None}, ensure_ascii=False, default=str))
    return out.rc


if __name__ == "__main__":
    raise SystemExit(main())
