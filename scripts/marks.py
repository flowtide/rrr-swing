#!/usr/bin/env python3
"""marks — 회계(독립 코드): 전 포지션 종가 마크 → local/positions.jsonl + 원장 `mark`(accounting 역할). 주문·판단 0.

왜: 손절 없는 체제에서 완결 거래 통계는 패자를 표본에서 빼먹는다(E3). 성과의 단일 소스는 매 거래일 전 포지션 마크다.
- 입력: 브로커 계좌 평가 응답(JSON, --from-json/stdin — 브로커 조회는 주입) + 선택 `--closes-json {sym: close}`(공급자 일봉 종가).
- 종가 소스: broker(cur_prc) | supplier_daily_candle(closes). 종가가 없거나 0(거래정지·휴장)이면 직전 마크 종가를 carry 하고 `halted=true`(NA·0 으로 만들지 않는다).
- 동결북: `--freeze` 가 그 시점 마크를 local/frozen_book.json 에 스냅샷. `--report` 는 최신 마크 vs 동결북, 마크 누락 셀(거래일×보유 종목).
- 소급: `--date YYYY-MM-DD`. 같은 거래일 마크가 있으면 skip(exit 3, --force 로 추가). 원장 mark 는 dup_key 로 멱등.
재사용 출처: rrr-trading2 scripts/marks.py (parse_evaluation·frozen_book_value·activity 산식). 크론(20:30) 설치는 사람이 한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ledger as lg  # noqa: E402


@dataclass
class Paths:
    marks: str = "local/positions.jsonl"
    ledger: str = "local/ledger.jsonl"
    frozen: str = "local/frozen_book.json"


def _num(v, default=0.0):
    """브로커 응답의 부호 접두(+/-)와 zero-padding 을 견디는 숫자 파서. 결측은 None 이 아니라 명시 기본값."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return default
    neg = s.startswith("-")
    s = s.lstrip("+-").lstrip("0") or "0"
    try:
        f = float(s)
    except ValueError:
        return default
    return -f if neg else f


def _plain(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def parse_evaluation(payload: dict) -> dict:
    rows = payload.get("stk_acnt_evlt_prst") or []
    positions = []
    for r in rows:
        qty = _num(r.get("rmnd_qty"))
        if qty == 0:
            continue
        positions.append({"sym": str(r.get("stk_cd", "")).lstrip("A"), "name": r.get("stk_nm", ""), "qty": qty,
                          "avg_px": _num(r.get("avg_prc")), "close_px": _num(r.get("cur_prc")), "close_source": "broker", "halted": False,
                          "pur_amt": _num(r.get("pur_amt"))})
    cash = {"entr": _num(payload.get("entr")), "d2_entra": _num(payload.get("d2_entra"))}
    return {"positions": positions, "cash": cash}


def _apply_closes(positions: list[dict], prev: dict | None, closes: dict | None, close_source: str) -> None:
    prev_close = {p["sym"]: p["close_px"] for p in (prev or {}).get("positions", [])}
    for p in positions:
        chosen, src = None, None
        if close_source == "supplier_daily_candle" and closes and closes.get(p["sym"]) not in (None, 0, "0", ""):
            chosen, src = float(closes[p["sym"]]), "supplier_daily_candle"
        elif p["close_px"] > 0 and close_source == "broker":
            chosen, src = p["close_px"], "broker"
        elif close_source == "supplier_daily_candle" and p["close_px"] > 0:
            chosen, src = p["close_px"], "broker"  # 공급자 종가 없음 → 브로커 현재가 폴백(출처 기록)
        if chosen is None:
            carry = prev_close.get(p["sym"])
            p["halted"] = True
            p["close_px"] = carry if carry is not None else p["avg_px"]
            p["close_source"] = "carry" if carry is not None else "avg_fallback"
        else:
            p["close_px"], p["close_source"], p["halted"] = chosen, src, False
        p["eval_amt"] = p["qty"] * p["close_px"]
        p["pl_amt"] = p["eval_amt"] - p["pur_amt"]
        p["pl_pct"] = round((p["eval_amt"] / p["pur_amt"] - 1) * 100, 4) if p["pur_amt"] else 0.0


def frozen_book_value(prev_positions, close_by_sym) -> float:
    """직전 마크의 보유를 그대로 들고 있었을 때의 평가액. 오늘 종가를 모르는 종목은 직전 평가액으로 동결(0 아님)."""
    total = 0.0
    for p in prev_positions:
        px = close_by_sym.get(p["sym"])
        total += p["qty"] * px if px is not None else p["eval_amt"]
    return total


def build_mark(payload, prev=None, *, closes=None, close_source="broker", flow_in=0.0, flow_out=0.0, trade_date=None, ts=None) -> dict:
    rec = parse_evaluation(payload)
    _apply_closes(rec["positions"], prev, closes, close_source)
    eval_amt = sum(p["eval_amt"] for p in rec["positions"])
    pur_amt = sum(p["pur_amt"] for p in rec["positions"])
    rec["totals"] = {"eval_amt": eval_amt, "pur_amt": pur_amt, "pl_amt": eval_amt - pur_amt,
                     "pl_pct": round((eval_amt / pur_amt - 1) * 100, 4) if pur_amt else 0.0, "nav": eval_amt + rec["cash"]["entr"]}
    now = ts or datetime.now().isoformat(timespec="seconds")
    rec["ts"] = now
    rec["trade_date"] = trade_date or now[:10]
    rec["close_source"] = close_source
    rec["flows"] = {"deposit": flow_in, "withdraw": flow_out}
    if prev:
        close_by_sym = {p["sym"]: p["close_px"] for p in rec["positions"]}
        frozen = frozen_book_value(prev["positions"], close_by_sym) + prev["cash"]["entr"]
        rec["frozen_book"] = round(frozen, 2)
        rec["activity"] = round((rec["totals"]["nav"] - prev["totals"]["nav"]) - (frozen - prev["totals"]["nav"]) - (flow_in - flow_out), 2)
    else:
        rec["frozen_book"] = None
        rec["activity"] = None
    return rec


def mark_events(mark: dict) -> list[dict]:
    """원장 `mark` 이벤트(스키마 scripts/ledger.py): 포지션당 1건. dup_key 로 멱등."""
    return [{"date": mark["trade_date"], "sym": p["sym"], "position_id": f"pos:{p['sym']}", "qty": int(p["qty"]), "avg_px": _plain(p["avg_px"]),
             "close": _plain(p["close_px"]), "unrealized": _plain(p["pl_amt"]), "halted": p["halted"], "close_source": p["close_source"],
             "dup_key": f"mark|{mark['trade_date']}|{p['sym']}"} for p in mark["positions"]]


def _read_marks(paths: Paths) -> list[dict]:
    if not os.path.exists(paths.marks):
        return []
    with open(paths.marks, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def last_mark(paths: Paths) -> dict | None:
    rows = _read_marks(paths)
    return rows[-1] if rows else None


def write_mark(payload, paths: Paths, *, account_id: str, trade_date=None, ts=None, closes=None, close_source="broker",
               flow_in=0.0, flow_out=0.0, force=False) -> int:
    rows = _read_marks(paths)
    prev = rows[-1] if rows else None
    rec = build_mark(payload, prev=prev, closes=closes, close_source=close_source, flow_in=flow_in, flow_out=flow_out, trade_date=trade_date, ts=ts)
    if any(r.get("trade_date") == rec["trade_date"] for r in rows) and not force:
        return 3
    os.makedirs(os.path.dirname(paths.marks) or ".", exist_ok=True)
    with open(paths.marks, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    for ev in mark_events(rec):
        lg.append_event(paths.ledger, "mark", ev, role="accounting", account_id=account_id, ts=rec["ts"])
    return 0


def freeze(paths: Paths, trade_date: str | None = None) -> dict:
    rows = _read_marks(paths)
    pick = [r for r in rows if trade_date is None or r["trade_date"] == trade_date]
    if not pick:
        raise ValueError("동결할 마크가 없다")
    m = pick[-1]
    snap = {"trade_date": m["trade_date"], "ts": m["ts"], "frozen_at": datetime.now().isoformat(timespec="seconds"),
            "positions": [{"sym": p["sym"], "qty": p["qty"], "close_px": p["close_px"], "eval_amt": p["eval_amt"]} for p in m["positions"]],
            "cash": m["cash"], "nav": m["totals"]["nav"]}
    os.makedirs(os.path.dirname(paths.frozen) or ".", exist_ok=True)
    with open(paths.frozen, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    return snap


def _weekdays_between(first: str, last: str, holidays=()) -> list[str]:
    d0, d1 = date.fromisoformat(first), date.fromisoformat(last)
    out = []
    d = d0 + timedelta(days=1)
    while d < d1:
        if d.weekday() < 5 and d.isoformat() not in set(holidays):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def missing_cells(rows: list[dict], holidays=()) -> list[dict]:
    """마크 누락 셀: 마크 파일의 거래일 갭 × 직전 마크의 보유 종목. review 도 같은 규칙을 쓴다."""
    rows = sorted((r for r in rows if r.get("trade_date")), key=lambda r: r["trade_date"])
    have = {r["trade_date"] for r in rows}
    missing = []
    for i in range(len(rows) - 1):
        for d in _weekdays_between(rows[i]["trade_date"], rows[i + 1]["trade_date"], holidays):
            if d not in have:
                missing.extend({"date": d, "sym": p["sym"]} for p in rows[i].get("positions", []))
    return missing


def report(paths: Paths) -> dict:
    rows = sorted(_read_marks(paths), key=lambda r: r["trade_date"])
    if not rows:
        return {"marks": 0, "dates": [], "latest": None, "frozen_book": None, "vs_frozen_book": None, "missing_cells": []}
    dates = [r["trade_date"] for r in rows]
    missing = missing_cells(rows)
    latest = rows[-1]
    frozen = None
    if os.path.exists(paths.frozen):
        with open(paths.frozen, encoding="utf-8") as f:
            frozen = json.load(f)
    acts = [r["activity"] for r in rows if r.get("activity") is not None]
    return {"marks": len(rows), "dates": dates,
            "latest": {"trade_date": latest["trade_date"], "nav": latest["totals"]["nav"], "pl_pct": latest["totals"]["pl_pct"],
                       "halted": [p["sym"] for p in latest["positions"] if p.get("halted")]},
            "frozen_book": ({"trade_date": frozen["trade_date"], "nav": frozen["nav"]} if frozen else None),
            "vs_frozen_book": (latest["totals"]["nav"] - frozen["nav"]) if frozen else None,
            "activity_total": round(sum(acts), 2) if acts else None, "missing_cells": missing}


def selftest() -> bool:
    row = {"stk_cd": "A005930", "stk_nm": "삼성전자", "rmnd_qty": "000000000010", "avg_prc": "000000100000", "cur_prc": "000000100000",
           "evlt_amt": "000001000000", "pur_amt": "000001000000", "pl_amt": "0", "pl_rt": "0.00"}
    day1 = {"entr": "000001000000", "d2_entra": "000001000000", "stk_acnt_evlt_prst": [row]}
    m1 = build_mark(day1, prev=None, trade_date="2026-09-01")
    assert m1["totals"]["nav"] == 2_000_000 and m1["frozen_book"] is None
    day2 = json.loads(json.dumps(day1)); day2["stk_acnt_evlt_prst"][0]["cur_prc"] = "000000110000"
    m2 = build_mark(day2, prev=m1, trade_date="2026-09-02")
    assert m2["totals"]["nav"] == 2_100_000 and m2["activity"] == 0.0
    day3 = json.loads(json.dumps(day1)); day3["stk_acnt_evlt_prst"][0]["cur_prc"] = "000000000000"
    m3 = build_mark(day3, prev=m2, trade_date="2026-09-03")
    assert m3["positions"][0]["halted"] and m3["positions"][0]["close_px"] == 110_000 and m3["positions"][0]["close_source"] == "carry"
    m4 = build_mark(day3, prev=m3, trade_date="2026-09-04", closes={"005930": 120000}, close_source="supplier_daily_candle")
    assert not m4["positions"][0]["halted"] and m4["positions"][0]["close_px"] == 120_000
    assert _num("-00000523856") == -523856.0 and _num("") == 0.0
    empty = {"entr": "000002100000", "d2_entra": "0", "stk_acnt_evlt_prst": []}
    m5 = build_mark(empty, prev=m2, trade_date="2026-09-05")
    assert m5["frozen_book"] == 1_100_000 + 1_000_000
    assert all(lg.validate_event("mark", e) == [] for e in mark_events(m2))
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="marks", description="회계: 일별 포지션 마크 (주문·판단 0)")
    p.add_argument("--write", action="store_true"); p.add_argument("--report", action="store_true"); p.add_argument("--freeze", action="store_true")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--from-json", help="브로커 계좌 평가 응답 JSON 파일(없으면 stdin)")
    p.add_argument("--closes-json", help="공급자 일봉 종가 {sym: close} JSON 파일")
    p.add_argument("--close-source", choices=["broker", "supplier_daily_candle"], default=None)
    p.add_argument("--date"); p.add_argument("--ts"); p.add_argument("--force", action="store_true")
    p.add_argument("--flow-in", type=float, default=0.0); p.add_argument("--flow-out", type=float, default=0.0)
    p.add_argument("--marks", default="local/positions.jsonl"); p.add_argument("--ledger", default="local/ledger.jsonl"); p.add_argument("--frozen", default="local/frozen_book.json")
    p.add_argument("--account"); p.add_argument("--config", default="config/config.json")
    a = p.parse_args(argv)
    paths = Paths(marks=a.marks, ledger=a.ledger, frozen=a.frozen)
    if a.selftest:
        selftest(); print(json.dumps({"selftest": "ok"})); return 0
    cfg = {}
    if os.path.exists(a.config):
        try:
            with open(a.config, encoding="utf-8") as f:
                cfg = json.load(f)
        except ValueError:
            cfg = {}
    account = a.account or str(cfg.get("account_id") or "account")
    close_source = a.close_source or str((cfg.get("accounting") or {}).get("close_source") or "broker")
    if a.freeze:
        snap = freeze(paths, a.date); print(json.dumps({"frozen": snap["trade_date"], "nav": snap["nav"]}, ensure_ascii=False)); return 0
    if a.report:
        print(json.dumps(report(paths), ensure_ascii=False)); return 0
    if a.write:
        if a.from_json:
            with open(a.from_json, encoding="utf-8") as f:
                payload = json.load(f)
        else:
            raw = sys.stdin.read().strip()
            if not raw:
                print("ERROR: 계좌 응답이 없다 — --from-json 또는 stdin", file=sys.stderr); return 2
            payload = json.loads(raw)
        closes = None
        if a.closes_json:
            with open(a.closes_json, encoding="utf-8") as f:
                closes = json.load(f)
        rc = write_mark(payload, paths, account_id=account, trade_date=a.date, ts=a.ts, closes=closes, close_source=close_source,
                        flow_in=a.flow_in, flow_out=a.flow_out, force=a.force)
        if rc == 3:
            print("SKIP: 같은 거래일 마크가 이미 있다(--force 로 추가)", file=sys.stderr); return 3
        m = last_mark(paths)
        print(json.dumps({"trade_date": m["trade_date"], "positions": len(m["positions"]), "nav": m["totals"]["nav"], "pl_pct": m["totals"]["pl_pct"],
                          "frozen_book": m["frozen_book"], "activity": m["activity"], "halted": [q["sym"] for q in m["positions"] if q["halted"]]}, ensure_ascii=False))
        return 0
    p.print_help(); return 2


if __name__ == "__main__":
    raise SystemExit(main())
