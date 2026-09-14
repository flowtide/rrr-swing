#!/usr/bin/env python3
"""rt_calc — 결정론 산술 계층 (호가단위·floor·수량·평단·손익·R). 판단 0.

세션 규율 1("수치는 스크립트 출력 그대로")의 근거. 검증된 산술만 이식했다(rrr-trading2 scripts/rt_calc.py 의
tick_size/tick_normalize/_px_abs). 신호 파싱·스톱 판정은 결정 3(코드는 매매를 결정하지 않는다) 이후 필요 없어 이식하지 않는다.

  python3 scripts/rt_calc.py selftest
  python3 scripts/rt_calc.py qty --nav 100000000 --alloc-pct 10 --px 51000 --cash 50000000
  python3 scripts/rt_calc.py floor --avg 22150 --margin 1.003
출력: stdout JSON 한 개. exit 0 정상 / 2 인자 오류.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

# KRX 호가단위 (2023 개편) — rrr-trading2 이식
TICK_TABLE = [(2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)]


def tick_size(px: Decimal) -> Decimal:
    for limit, tick in TICK_TABLE:
        if px < limit:
            return Decimal(tick)
    return Decimal(1000)


def tick_normalize(px: Decimal, side: str) -> Decimal:
    """지정가 호가단위 정규화 — buy 는 내림(의도 초과 금지), sell 은 올림."""
    t = tick_size(px)
    q = (px / t).to_integral_value(rounding=ROUND_FLOOR if side == "buy" else ROUND_CEILING)
    return q * t


def px_abs(value) -> Decimal | None:
    """브로커 부호 접두 문자열(+57700 / -52200)을 절대 가격으로 — 주가는 음수 불가. 빈 값은 None."""
    s = str(value if value is not None else "").strip().lstrip("+-")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def floor_px(avg_px: Decimal, margin: Decimal) -> Decimal:
    """lot floor = 평단 × margin 을 호가단위로 올림(이 가격 미만 매도는 손실 확정 → 규율 대상)."""
    raw = Decimal(avg_px) * Decimal(margin)
    t = tick_size(raw)
    return (raw / t).to_integral_value(rounding=ROUND_CEILING) * t


def qty_for_allocation(*, nav: Decimal, alloc_pct: Decimal, px: Decimal, cash: Decimal) -> int:
    """allocation 상한(NAV 비율)과 가용 현금 중 작은 예산으로 살 수 있는 정수 수량. 판단이 아니라 산술."""
    budget = min(Decimal(nav) * Decimal(alloc_pct) / Decimal(100), Decimal(cash))
    if budget <= 0 or Decimal(px) <= 0:
        return 0
    return int((budget / Decimal(px)).to_integral_value(rounding=ROUND_FLOOR))


def avg_after_fill(qty0: int, avg0: Decimal, qty1: int, px1: Decimal) -> Decimal:
    total = int(qty0) + int(qty1)
    if total <= 0:
        return Decimal(0)
    value = Decimal(qty0) * Decimal(avg0) + Decimal(qty1) * Decimal(px1)
    return (value / Decimal(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize()


def pnl_amount(*, qty: int, entry_px: Decimal, exit_px: Decimal, fees: Decimal = Decimal(0)) -> Decimal:
    return Decimal(qty) * (Decimal(exit_px) - Decimal(entry_px)) - Decimal(fees)


def r_multiple(*, pnl: Decimal, risk_amount: Decimal) -> Decimal | None:
    """R = 손익 / 위험 금액(계획 시점에 선언한 위험). 위험 금액이 없으면 None(NA — 0 으로 만들지 않는다)."""
    if Decimal(risk_amount) <= 0:
        return None
    return (Decimal(pnl) / Decimal(risk_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, default=str))


def selftest() -> bool:
    assert tick_size(Decimal("1500")) == 1 and tick_size(Decimal("600000")) == 1000
    assert tick_normalize(Decimal("22333"), "buy") == Decimal("22300") and tick_normalize(Decimal("22333"), "sell") == Decimal("22350")
    assert floor_px(Decimal("100000"), Decimal("1.003")) == Decimal("100300")
    assert floor_px(Decimal("22150"), Decimal("1.003")) == Decimal("22250")
    assert qty_for_allocation(nav=Decimal("100000000"), alloc_pct=Decimal("10"), px=Decimal("51000"), cash=Decimal("50000000")) == 196
    assert qty_for_allocation(nav=Decimal("100000000"), alloc_pct=Decimal("10"), px=Decimal("51000"), cash=Decimal("0")) == 0
    assert avg_after_fill(10, Decimal("100"), 10, Decimal("120")) == Decimal("110")
    assert pnl_amount(qty=10, entry_px=Decimal("100"), exit_px=Decimal("130"), fees=Decimal("30")) == Decimal("270")
    assert r_multiple(pnl=Decimal("270"), risk_amount=Decimal("100")) == Decimal("2.70")
    assert r_multiple(pnl=Decimal("1"), risk_amount=Decimal("0")) is None
    assert px_abs("-00052200") == Decimal("52200") and px_abs("") is None
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rt_calc", description="결정론 산술 (판단 0)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    t = sub.add_parser("tick"); t.add_argument("--px", required=True); t.add_argument("--side", choices=["buy", "sell"], default="buy")
    f = sub.add_parser("floor"); f.add_argument("--avg", required=True); f.add_argument("--margin", default="1.003")
    q = sub.add_parser("qty"); q.add_argument("--nav", required=True); q.add_argument("--alloc-pct", required=True); q.add_argument("--px", required=True); q.add_argument("--cash", required=True)
    a_ = sub.add_parser("avg"); a_.add_argument("--qty0", type=int, required=True); a_.add_argument("--avg0", required=True); a_.add_argument("--qty1", type=int, required=True); a_.add_argument("--px1", required=True)
    p = sub.add_parser("pnl"); p.add_argument("--qty", type=int, required=True); p.add_argument("--entry", required=True); p.add_argument("--exit", required=True); p.add_argument("--fees", default="0")
    r = sub.add_parser("r"); r.add_argument("--pnl", required=True); r.add_argument("--risk", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "selftest":
            selftest(); _out({"selftest": "ok"}); return 0
        if a.cmd == "tick":
            px = Decimal(a.px); _out({"px": str(px), "tick": str(tick_size(px)), "normalized": str(tick_normalize(px, a.side)), "side": a.side}); return 0
        if a.cmd == "floor":
            _out({"avg": a.avg, "margin": a.margin, "floor": str(floor_px(Decimal(a.avg), Decimal(a.margin)))}); return 0
        if a.cmd == "qty":
            qty = qty_for_allocation(nav=Decimal(a.nav), alloc_pct=Decimal(a.alloc_pct), px=Decimal(a.px), cash=Decimal(a.cash))
            _out({"qty": qty, "limit_px": str(tick_normalize(Decimal(a.px), "buy")), "budget_cap": str(min(Decimal(a.nav) * Decimal(a.alloc_pct) / 100, Decimal(a.cash)))}); return 0
        if a.cmd == "avg":
            _out({"avg": str(avg_after_fill(a.qty0, Decimal(a.avg0), a.qty1, Decimal(a.px1)))}); return 0
        if a.cmd == "pnl":
            _out({"pnl": str(pnl_amount(qty=a.qty, entry_px=Decimal(a.entry), exit_px=Decimal(a.exit), fees=Decimal(a.fees)))}); return 0
        if a.cmd == "r":
            rm = r_multiple(pnl=Decimal(a.pnl), risk_amount=Decimal(a.risk)); _out({"r": None if rm is None else str(rm)}); return 0
    except (InvalidOperation, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
