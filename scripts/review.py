#!/usr/bin/env python3
"""review — 회계(독립 코드) 주간 리뷰: 같은 로그를 누가 돌려도 같은 수가 나오는 산식. 판단·표본 제외·재분류 0.

입력(읽기 전용): 원장(local/ledger.jsonl), 마크(local/positions.jsonl), 동결북(local/frozen_book.json), 배달 로그(local/delivery.jsonl), config.
출력 = `review` 이벤트 + 무결성 지표표(`integrity` 키: CK 라벨 완비율·라벨 누락·마크 누락·코드 기원 주문·토큰 상한·전략 술어 reject). 규칙:
- 전환율: trigger=event 인 decision 을 event_label(ledger.ZONE_EVENTS)×action 셀로 세고, 결정가(`px`, 없으면 당일 마크 종가) 대비 **20 거래일 뒤 마크** 종가로 up 비율.
  창이 안 찼으면 최신 마크로 평가하고 `partial` 로 센다. 빈 셀 = "NA"(안정성 주장 금지).
- CK 라벨 완비율(checklist_completeness) = 근거 요약 CK-1~7 이 값 또는 명시 "NA" 로 다 있는 decision / 전체 decision. 라벨 누락 = trigger=event 중 ZONE_EVENTS 밖.
- 마크 누락 = 마크 파일의 거래일 갭 × 직전 보유 종목(marks.missing_cells). 코드 기원 주문 = decision_ref 없는 order.
- 전략 술어 reject = reject 중 rule_id 가 account_/ops_/infra_ 분류 밖(또는 rule_class 필드가 그 셋 밖). reject 0 건이면 "NA"(통과 아님).
- registry_hash·대사 불일치 = 이 산식은 계산하지 않는다("NA"). 토큰 = decision.token_usage(선택 필드) 일합 p50·p95·최대(nearest-rank) vs daily_token_cap.
- overdue_decision: flag(expired|recheck_due) 뒤 다음 거래일 08:00 까지 같은 종목의 decision(trigger=flag)·exit·plan_created 가 없으면 overdue.
  같은 (sym, kind) 의 연속 일별 flag 는 한 건으로 접는다. rollover = expired flag 뒤 같은 종목 plan_created(또는 `rollover_of`).
- unregistered_pivots = rule_ref 가 등록된 rule_change 밖인 이벤트. rule_version = 마지막 등록 rule_id.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import flags as fl  # noqa: E402
import ledger as lg  # noqa: E402
import marks as mk  # noqa: E402

# 어휘 정본은 ledger(→ inbound_core) 하나다. 여기서 다시 적으면 갈리고, 갈린 목록 밖의
# event_label 은 집계에서 조용히 버려진다(아래 matrix·label_missing).
LABELS = list(lg.ZONE_EVENTS)
ACTIONS = list(lg.ACTIONS)
CK_KEYS = list(lg.CK_KEYS)
REGISTRY_PREFIXES = ("account_", "acct_", "ops_", "infra_")
REGISTRY_CLASSES = ("account", "ops", "infra")
MARK_WINDOW_DAYS = 20


@dataclass
class Paths:
    ledger: str = "local/ledger.jsonl"
    marks: str = "local/positions.jsonl"
    frozen: str = "local/frozen_book.json"
    delivery: str = "local/delivery.jsonl"


def _pct(values: list, p: float):
    if not values:
        return "NA"
    s = sorted(values)
    return s[max(1, math.ceil(p * len(s))) - 1]


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _token_total(tu) -> int | None:
    if isinstance(tu, bool):
        return None
    if isinstance(tu, (int, float)):
        return int(tu)
    if isinstance(tu, dict):
        if _num(tu.get("total")) is not None:
            return int(_num(tu["total"]))
        vals = [_num(tu.get(k)) for k in ("input", "output", "input_tokens", "output_tokens")]
        vals = [v for v in vals if v is not None]
        return int(sum(vals)) if vals else None
    return None


def _in_period(e: dict, start: str, end: str) -> bool:
    ts = str(e.get("ts") or "")
    return bool(ts) and start <= ts[:10] <= end


def _conversion(decisions: list[dict], marks_by_sym: dict[str, list[tuple[str, float]]], holidays) -> dict:
    cells = {f"{l}|{a}": {"n": 0, "evaluated": 0, "up": 0, "partial": 0} for l in LABELS for a in ACTIONS}
    for d in decisions:
        if d.get("trigger") != "event" or d.get("event_label") not in LABELS or d.get("action") not in ACTIONS:
            continue
        cell = cells[f"{d['event_label']}|{d['action']}"]
        cell["n"] += 1
        sym, d_date = str(d.get("sym")), str(d["ts"])[:10]
        series = marks_by_sym.get(sym, [])
        ref = _num(d.get("px"))
        if ref is None:
            same = [c for dt, c in series if dt == d_date]
            ref = same[-1] if same else None
        if ref is None:
            continue
        target = fl.trading_days_after(d_date, MARK_WINDOW_DAYS, holidays)
        after = [(dt, c) for dt, c in series if d_date < dt <= target]
        if not after:
            continue
        dt, close = after[-1]
        cell["evaluated"] += 1
        cell["up"] += int(close > ref)
        cell["partial"] += int(dt < target)
    out = {}
    for k, c in cells.items():
        if c["n"] == 0:
            out[k] = "NA"
        else:
            out[k] = {"n": c["n"], "evaluated": c["evaluated"], "up": c["up"], "ratio": (round(c["up"] / c["evaluated"], 4) if c["evaluated"] else "NA"), "partial": c["partial"]}
    return out


def _overdue_decisions(events: list[dict], flags: list[dict], as_of: str, holidays) -> dict:
    def resolvers(sym: str):
        for e in events:
            evt = e.get("evt")
            if evt == "decision" and e.get("trigger") == "flag" and str(e.get("sym")) == sym:
                yield e
            elif evt == "exit" and (str(e.get("sym")) == sym or str(e.get("position_id")) == f"pos:{sym}"):
                yield e
            elif evt == "plan_created" and str(e.get("sym")) == sym:
                yield e

    items: list[dict] = []
    open_by_key: dict[tuple, dict] = {}
    for f in sorted(flags, key=lambda x: x["ts"]):
        key = (str(f.get("sym")), f.get("kind"))
        prev = open_by_key.get(key)
        if prev is not None and not (prev.get("resolved_ts") and prev["resolved_ts"] < f["ts"]):
            continue  # 같은 (sym, kind) 의 연속 일별 flag 는 접는다
        deadline = fl.next_trading_day(f["ts"][:10], holidays) + "T08:00:00"
        res = sorted((e for e in resolvers(key[0]) if e["ts"] > f["ts"]), key=lambda e: e["ts"])
        item = {"sym": key[0], "kind": key[1], "flag_ts": f["ts"], "deadline": deadline}
        if res:
            item["resolved_ts"], item["resolved_by"] = res[0]["ts"], res[0]["evt"]
            item["status"] = "resolved" if res[0]["ts"] <= deadline else "overdue_late"
        else:
            item["status"] = "pending" if as_of < deadline else "overdue"
        items.append(item)
        open_by_key[key] = item
    return {"count": sum(i["status"] in ("overdue", "overdue_late") for i in items), "pending": sum(i["status"] == "pending" for i in items), "items": items}


def _overdue_escalations(events: list[dict], escalations: list[dict], as_of: str) -> dict:
    items = []
    for es in sorted(escalations, key=lambda e: e["ts"]):
        ref, ack_by = str(es.get("decision_ref")), str(es.get("ack_by") or "")
        acks = [e for e in events if e.get("evt") == "decision" and e.get("trigger") == "operator" and str(e.get("escalation_ref")) == ref and e["ts"] > es["ts"]]
        if acks and (not ack_by or min(a["ts"] for a in acks) <= ack_by):
            status = "resolved"
        elif ack_by and as_of >= ack_by:
            status = "overdue"
        else:
            status = "pending"
        items.append({"decision_ref": ref, "ack_by": ack_by, "status": status})
    return {"count": sum(i["status"] == "overdue" for i in items), "items": items}


def compute(events: list[dict], *, marks_rows: list[dict], frozen: dict | None, delivery_rows: list[dict], config: dict, start: str, end: str, holidays=()) -> dict:
    events = sorted((e for e in events if isinstance(e, dict) and e.get("ts") and e.get("evt")), key=lambda e: e["ts"])
    as_of = f"{end}T23:59:59"
    period = [e for e in events if _in_period(e, start, end)]
    by = defaultdict(list)
    for e in period:
        by[e["evt"]].append(e)
    decisions = by["decision"]

    marks_by_sym: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for m in events:
        if m.get("evt") == "mark" and m.get("date") and _num(m.get("close")) is not None:
            marks_by_sym[str(m.get("sym"))].append((str(m["date"]), _num(m["close"])))
    for s in marks_by_sym.values():
        s.sort()

    # CK 라벨·이벤트 라벨
    def ck_complete(d: dict) -> bool:
        ck = d.get("checklist")
        return isinstance(ck, dict) and all(k in ck and ck[k] is not None and ck[k] != "" for k in CK_KEYS)
    ck_num = sum(ck_complete(d) for d in decisions)
    event_decisions = [d for d in decisions if d.get("trigger") == "event"]
    label_missing = sum(d.get("event_label") not in LABELS for d in event_decisions)
    seen_labels = [l for l in LABELS if any(d.get("event_label") == l for d in event_decisions)]

    # 주문·안전 외피
    orders = by["order"]
    code_origin = [str(o.get("dup_key") or f"line:{i}") for i, o in enumerate(orders) if not o.get("decision_ref")]
    rejects = [s for s in by["safety_check"] if s.get("result") == "reject"]
    reject_by_rule = dict(sorted(Counter(str(r.get("rule_id") or "unknown") for r in rejects).items())) if rejects else "NA"

    def is_strategy(r: dict) -> bool:
        cls = r.get("rule_class")
        if cls is not None:
            return str(cls) not in REGISTRY_CLASSES
        return not str(r.get("rule_id") or "").startswith(REGISTRY_PREFIXES)
    strat_ids = sorted({str(r.get("rule_id")) for r in rejects if is_strategy(r)})
    strategy_reject = {"num": sum(is_strategy(r) for r in rejects), "den": len(rejects), "rule_ids": strat_ids} if rejects else "NA"

    # wake·토큰
    wake_days: Counter = Counter()
    raw_total = 0
    for r in delivery_rows:
        if isinstance(r, dict) and r.get("kind") == "digest" and _in_period(r, start, end):
            wake_days[str(r["ts"])[:10]] += 1
            raw_total += int(r.get("raw_event_count") or 0)
    wv = list(wake_days.values())
    wake = {"days": dict(sorted(wake_days.items())), "total": sum(wv), "p50": _pct(wv, 0.5), "p95": _pct(wv, 0.95), "max": (max(wv) if wv else "NA"), "raw_event_total": raw_total}
    tok_days: Counter = Counter()
    without = 0
    for d in decisions:
        t = _token_total(d.get("token_usage"))
        if t is None:
            without += 1
        else:
            tok_days[str(d["ts"])[:10]] += t
    tv = list(tok_days.values())
    tokens = {"days": dict(sorted(tok_days.items())), "total": sum(tv), "p50": _pct(tv, 0.5), "p95": _pct(tv, 0.95), "max": (max(tv) if tv else "NA"), "decisions_without_usage": without}
    cap = config.get("daily_token_cap")
    cap_n = _num(cap)
    exceeded = sorted(d for d, v in tok_days.items() if cap_n is not None and v > cap_n)
    token_cap = {"cap": cap, "max_day": (max(tv) if tv else "NA"), "exceeded_days": exceeded, "ok": ((not exceeded) if (tv and cap_n is not None) else "NA")}

    # 룰 등록·피벗
    rule_changes = [e for e in events if e.get("evt") == "rule_change" and str(e["ts"])[:10] <= end]
    registered = {str(r.get("rule_id")) for r in rule_changes}
    pivots = []
    for e in period:
        if e.get("evt") in ("decision", "coverage", "plan_created", "order") and e.get("rule_ref") is not None and str(e["rule_ref"]) not in registered:
            ref = e.get("decision_id") or e.get("plan_id") or e.get("dup_key") or e.get("sym")
            pivots.append({"ts": e["ts"], "evt": e["evt"], "ref": str(ref), "rule_ref": str(e["rule_ref"])})

    # 시간 규율
    flags = [f for f in by["flag"] if f.get("kind") in ("expired", "recheck_due")]
    overdue = _overdue_decisions(events, flags, as_of, holidays)
    escal = _overdue_escalations(events, by["escalation"], as_of)
    expired_flags = [f for f in events if f.get("evt") == "flag" and f.get("kind") == "expired"]
    rollovers = []
    for pc in by["plan_created"]:
        prior = [f for f in expired_flags if str(f.get("sym")) == str(pc.get("sym")) and f["ts"] < pc["ts"]]
        if prior or pc.get("rollover_of"):
            item = {"plan_id": str(pc.get("plan_id")), "sym": str(pc.get("sym")), "after_flag": (prior[-1]["ts"] if prior else None)}
            if pc.get("rollover_of"):
                item["rollover_of"] = str(pc["rollover_of"])
            rollovers.append(item)

    # 마크
    rows = sorted((r for r in marks_rows if r.get("trade_date")), key=lambda r: r["trade_date"])
    if frozen and rows and _num(frozen.get("nav")) is not None:
        latest = rows[-1]
        mvf = {"frozen_trade_date": frozen.get("trade_date"), "frozen_nav": _num(frozen["nav"]), "latest_trade_date": latest["trade_date"],
               "latest_nav": _num(latest["totals"]["nav"]), "diff": round(_num(latest["totals"]["nav"]) - _num(frozen["nav"]), 2)}
    else:
        mvf = "NA"
    missing = mk.missing_cells(rows, holidays)

    integrity = {
        "registry_hash": "NA",
        "strategy_predicate_reject": strategy_reject,
        "checklist_completeness": {"num": ck_num, "den": len(decisions), "pct": (round(100 * ck_num / len(decisions), 2) if decisions else "NA")},
        "event_label_missing": {"num": label_missing, "den": len(event_decisions)},
        "label_coverage": {"seen": seen_labels, "missing": [l for l in LABELS if l not in seen_labels], "n_seen": len(seen_labels), "of": len(LABELS)},
        "mark_missing": {"count": len(missing), "cells": missing},
        "code_origin_orders": {"num": len(code_origin), "den": len(orders), "orders": code_origin},
        "token_cap": token_cap,
        "reconciliation_mismatch": "NA",
    }
    return {
        "period": {"start": start, "end": end},
        "rule_version": (str(rule_changes[-1].get("rule_id")) if rule_changes else "none"),
        "decision_count": len(decisions),
        "marks_vs_frozen_book": mvf,
        "conversion": _conversion(decisions, marks_by_sym, holidays),
        "safety_reject_by_rule": reject_by_rule,
        "wake_count": wake,
        "token_usage": tokens,
        "rule_changes": [{"rule_id": str(r.get("rule_id")), "registered_at": r.get("registered_at"), "applies_from": r.get("applies_from")} for r in rule_changes],
        "unregistered_pivots": pivots,
        "rollover_count": len(rollovers),
        "rollovers": rollovers,
        "overdue_decision": overdue,
        "overdue_escalation": escal,
        "integrity": integrity,
        "boundary": "accounting only — no judgement; NA is reported, never counted as pass",
    }


def review_event(result: dict) -> dict:
    p = result["period"]
    return {"period": p, "rule_version": result["rule_version"], "marks_vs_frozen_book": result["marks_vs_frozen_book"], "conversion": result["conversion"],
            "safety_reject_by_rule": result["safety_reject_by_rule"],
            "wake_count": {k: result["wake_count"][k] for k in ("total", "p50", "p95", "max", "raw_event_total")},
            "token_usage": {k: result["token_usage"][k] for k in ("total", "p50", "p95", "max", "decisions_without_usage")},
            "unregistered_pivots": result["unregistered_pivots"], "rollover_count": result["rollover_count"],
            "overdue_decision": {"count": result["overdue_decision"]["count"], "pending": result["overdue_decision"]["pending"]},
            "overdue_escalation": result["overdue_escalation"]["count"], "decision_count": result["decision_count"], "integrity": result["integrity"],
            "dup_key": f"review|{p['start']}|{p['end']}"}


def run(paths: Paths, *, start: str, end: str, config: dict, write: bool = False, holidays=()) -> dict:
    frozen = None
    if os.path.exists(paths.frozen):
        with open(paths.frozen, encoding="utf-8") as f:
            frozen = json.load(f)
    result = compute(fl._read_jsonl(paths.ledger), marks_rows=fl._read_jsonl(paths.marks), frozen=frozen, delivery_rows=fl._read_jsonl(paths.delivery),
                     config=config, start=start, end=end, holidays=holidays)
    result["written"] = False
    if write:
        rec = lg.append_event(paths.ledger, "review", review_event(result), role="accounting", account_id=str(config.get("account_id") or ""), ts=f"{end}T23:59:59")
        result["written"] = rec is not None
    return result


def selftest() -> bool:
    ck = {k: "ok" for k in CK_KEYS}
    d = {"evt": "decision", "ts": "2026-06-01T10:35:00", "decision_id": "d1", "trigger": "event", "event_label": "support_return", "sym": "336260", "checklist": ck,
         "action": "enter", "rationale": "r", "px": "1000", "token_usage": {"input": 100, "output": 20}}
    m = {"evt": "mark", "ts": "2026-06-29T20:30:00", "date": "2026-06-29", "sym": "336260", "close": "1100"}
    f = {"evt": "flag", "ts": "2026-06-24T20:35:00", "kind": "expired", "sym": "336260", "plan_id": "p1"}
    r = compute([d, m, f], marks_rows=[], frozen=None, delivery_rows=[{"ts": "2026-06-01T10:35:00", "kind": "digest", "raw_event_count": 2}],
                config={"daily_token_cap": 1000}, start="2026-06-01", end="2026-06-30")
    assert r["conversion"]["support_return|enter"] == {"n": 1, "evaluated": 1, "up": 1, "ratio": 1.0, "partial": 0}
    assert r["conversion"]["support_enter|hold"] == "NA" and len(r["conversion"]) == len(LABELS) * len(ACTIONS)
    assert r["integrity"]["checklist_completeness"]["pct"] == 100.0 and r["integrity"]["strategy_predicate_reject"] == "NA"
    assert r["wake_count"]["total"] == 1 and r["token_usage"]["total"] == 120 and r["overdue_decision"]["count"] == 1
    assert lg.validate_event("review", review_event(r)) == []
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="review", description="회계: 주간 리뷰 (판단 0)")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None, help="기본 오늘. --start 기본 = end-6일")
    p.add_argument("--ledger", default="local/ledger.jsonl")
    p.add_argument("--marks", default="local/positions.jsonl")
    p.add_argument("--frozen", default="local/frozen_book.json")
    p.add_argument("--delivery", default="local/delivery.jsonl")
    p.add_argument("--config", default="config/config.json")
    p.add_argument("--account", default=None)
    p.add_argument("--holidays", default=None, help="휴장일 JSON 배열 파일")
    p.add_argument("--write", action="store_true", help="원장에 review 이벤트 기록(accounting 역할, 기간당 1회)")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)
    if a.selftest:
        selftest()
        print(json.dumps({"selftest": "ok"}))
        return 0
    cfg = {}
    if os.path.exists(a.config):
        try:
            with open(a.config, encoding="utf-8") as f:
                cfg = json.load(f)
        except ValueError:
            print(f"ERROR: config 파싱 실패: {a.config}", file=sys.stderr)
            return 2
    if a.account:
        cfg["account_id"] = a.account
    end = a.end or date.today().isoformat()
    start = a.start or (date.fromisoformat(end) - timedelta(days=6)).isoformat()
    holidays = ()
    if a.holidays:
        with open(a.holidays, encoding="utf-8") as f:
            holidays = tuple(json.load(f))
    paths = Paths(ledger=a.ledger, marks=a.marks, frozen=a.frozen, delivery=a.delivery)
    res = run(paths, start=start, end=end, config=cfg, write=a.write, holidays=holidays)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
