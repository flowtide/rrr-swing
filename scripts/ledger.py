#!/usr/bin/env python3
"""ledger — 계좌별 append-only JSONL 원장 (local/ledger.jsonl). 이벤트 스키마 검증·역할 게이트·dup_key 멱등·직렬 쓰기.

이 스크립트는 기록·검증만 한다. 판단·주문 없음.
- 이벤트 타입과 필수 필드·enum 은 EVENT_SCHEMA 표가 **단일 기준**이며 tests/test_ledger.py 의 EXPECTED_REQUIRED 픽스처와 짝이다 — 타입을 바꾸면 둘을 함께 바꾼다.
- 역할 게이트: 세션(agent)이 쓰는 타입은 ROLE_EVENTS["agent"]. safety_check·fill·cancel 은 --role safety, mark·flag·review 는 --role accounting.
- dup_key 가 있으면 같은 (evt, dup_key) 가 이미 있을 때 기록하지 않는다(멱등, exit 3). 쓰기는 fcntl 락으로 직렬화.

  python3 scripts/ledger.py append decision --json '{...}' [--account A] [--ts ISO]
  python3 scripts/ledger.py append mark --role accounting --file rec.json
  python3 scripts/ledger.py validate | tail -n 20 | selftest
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from datetime import datetime

# 이벤트 어휘의 정본은 inbound_core 하나다. 여기서 다시 적으면 갈린다 — 실제로 갈려서
# heartbeat(D13)가 빠지고 폐기된 tick(D12)이 남아, 배달되는 이벤트를 원장이 도메인 밖으로
# 판정했다. 원장 고유 어휘(ACTIONS·TRIGGERS 등)만 여기서 정의한다.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import inbound_core as _core  # noqa: E402

SCHEMA = "rrr_swing_ledger_v1"
DEFAULT_LEDGER = "local/ledger.jsonl"

ZONE_EVENTS = _core.ZONE_EVENTS                      # 6종 + heartbeat
SUBSCRIBABLE_EVENT_TYPES = _core.SUBSCRIBABLE_EVENT_TYPES  # ZONE_EVENTS + macro
ACTIONS = ("enter", "add", "hold", "exit", "no_action")
TRIGGERS = ("event", "schedule", "operator", "flag")
CK_KEYS = tuple(f"CK-{i}" for i in range(1, 8))
CONVICTIONS = ("높음", "중간", "중하", "낮음")

# ---- 원장 이벤트 스키마 (단일 기준) ------------------------------------------------------------------
EVENT_SCHEMA: dict[str, dict] = {
    "plan_created": {"required": ["plan_id", "sym", "account_id", "entry_zone", "allocation_pct", "horizon_d", "conviction", "invalidation_conditions"],
                     "enums": {"conviction": CONVICTIONS}},
    "plan_activated": {"required": ["plan_id"], "enums": {}},
    "subscription": {"required": ["symbols", "event_types", "sessions", "expires_at"], "enums": {}},
    "decision": {"required": ["decision_id", "trigger", "event_label", "sym", "checklist", "action", "rationale"],
                 "enums": {"trigger": TRIGGERS, "event_label": ZONE_EVENTS + ("none",), "action": ACTIONS}},
    "order": {"required": ["decision_ref", "sym", "side", "px", "qty", "dup_key"], "enums": {"side": ("buy", "sell")}},
    "safety_check": {"required": ["order_ref", "result"], "enums": {"result": ("pass", "reject")}},
    "fill": {"required": ["position_id", "fill_px", "qty"], "enums": {}},
    "cancel": {"required": ["ord", "qty_remaining"], "enums": {}},
    "mark": {"required": ["date", "sym", "position_id", "qty", "avg_px", "close", "unrealized"], "enums": {}},
    "coverage": {"required": ["sym", "plan_id", "status", "basis", "recheck_by", "as_of", "evidence"],
                 "enums": {"status": ("valid", "weakening", "invalidated"), "evidence": ("full", "partial", "stale")}},
    "flag": {"required": ["kind", "ts"], "enums": {"kind": ("expired", "recheck_due")}},
    "exit": {"required": ["position_id", "reason", "decision_ref"], "enums": {"reason": ("target", "expired", "invalidated", "operator")}},
    "rule_change": {"required": ["rule_id", "hypothesis", "metric", "n_required", "until", "reject_condition", "registered_at", "applies_from"], "enums": {}},
    "review": {"required": ["period", "rule_version", "marks_vs_frozen_book", "conversion", "wake_count", "token_usage", "unregistered_pivots", "rollover_count", "overdue_decision"], "enums": {}},
    "escalation": {"required": ["decision_ref", "reason", "ack_by"], "enums": {}},
    "consult": {"required": ["consult_id", "sym", "action", "status"],
                "enums": {"status": ("pending", "agreed", "declined", "timeout"), "action": ("exit", "reduce", "hold")}},
}
ROLE_EVENTS: dict[str, set[str]] = {
    "agent": {"plan_created", "plan_activated", "subscription", "decision", "order", "coverage", "exit", "rule_change", "escalation", "consult"},
    "safety": {"safety_check", "fill", "cancel"},
    "accounting": {"mark", "flag", "review"},
}


class LedgerError(Exception):
    pass


def validate_event(evt: str, data: dict) -> list[str]:
    """스키마 위반 목록(빈 목록 = 유효)."""
    spec = EVENT_SCHEMA.get(evt)
    if spec is None:
        return [f"unknown evt: {evt}"]
    if not isinstance(data, dict):
        return ["data must be an object"]
    problems = [f"missing: {k}" for k in spec["required"] if k not in data]
    for field, allowed in spec["enums"].items():
        if field in data and data[field] not in allowed:
            problems.append(f"{field} must be one of {list(allowed)}: {data[field]!r}")
    if evt == "decision":
        ck = data.get("checklist")
        if not isinstance(ck, dict) or any(k not in ck for k in CK_KEYS):
            problems.append("checklist must contain CK-1..CK-7 (values may be NA)")
        if data.get("action") in ("enter", "add", "exit") and (data.get("px") is None or data.get("qty") is None):
            problems.append("px and qty are required for enter|add|exit")
    if evt == "flag" and not (data.get("plan_id") or data.get("position_id")):
        problems.append("flag needs plan_id or position_id")
    if evt == "plan_created":
        h = data.get("horizon_d")
        if not isinstance(h, int) or isinstance(h, bool) or h < 1:
            problems.append("horizon_d must be an integer >= 1 (null 금지)")
        if not isinstance(data.get("invalidation_conditions"), list):
            problems.append("invalidation_conditions must be a list")
    if evt == "safety_check" and data.get("result") == "reject" and not data.get("rule_id"):
        problems.append("reject requires rule_id")
    if evt == "subscription":
        bad = [e for e in (data.get("event_types") or []) if e not in SUBSCRIBABLE_EVENT_TYPES]
        if bad:
            problems.append(f"event_types outside domain: {bad}")
    return problems


def _read_all(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    out.append({"_corrupt": ln})
    return out


def append_event(path: str, evt: str, data: dict, *, role: str = "agent", account_id: str, ts: str | None = None) -> dict | None:
    """검증 → 역할 게이트 → dup_key 멱등 → 락 잡고 1줄 append. 반환: 기록 dict, 중복이면 None. 위반은 LedgerError."""
    if role not in ROLE_EVENTS:
        raise LedgerError(f"unknown role: {role}")
    if evt not in ROLE_EVENTS[role]:
        raise LedgerError(f"role {role} may not append {evt} (allowed: {sorted(ROLE_EVENTS[role])})")
    problems = validate_event(evt, data)
    if problems:
        raise LedgerError("; ".join(problems))
    rec = {"evt": evt, "ts": ts or datetime.now().isoformat(timespec="seconds"), "account_id": account_id, "schema": SCHEMA}
    rec.update({k: v for k, v in data.items() if k not in ("evt", "schema")})
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            dup = data.get("dup_key")
            if dup:
                f.seek(0)
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        old = json.loads(ln)
                    except ValueError:
                        continue
                    if old.get("evt") == evt and old.get("dup_key") == dup:
                        return None
            f.seek(0, os.SEEK_END)
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return rec


def validate_file(path: str) -> list[dict]:
    problems = []
    for index, rec in enumerate(_read_all(path), start=1):
        if "_corrupt" in rec:
            problems.append({"line": index, "evt": None, "problems": ["corrupt json"]}); continue
        evt = rec.get("evt")
        body = {k: v for k, v in rec.items() if k not in ("evt", "account_id", "schema")}  # ts 는 봉투와 본문(flag.ts) 공용
        missing_env = [k for k in ("ts", "account_id", "schema") if k not in rec]
        p = ([f"envelope missing: {missing_env}"] if missing_env else []) + validate_event(str(evt), body)
        if p:
            problems.append({"line": index, "evt": evt, "problems": p})
    return problems


def selftest() -> bool:
    import tempfile
    ck = {k: "NA" for k in CK_KEYS}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "l.jsonl")
        rec = append_event(p, "decision", {"decision_id": "d", "trigger": "event", "event_label": "support_return", "bar_ts": "t", "sym": "336260",
                                           "checklist": ck, "action": "hold", "rationale": "r"}, role="agent", account_id="a", ts="t")
        assert rec and rec["schema"] == SCHEMA
        try:
            append_event(p, "mark", {"date": "d", "sym": "s", "position_id": "p", "qty": 1, "avg_px": "1", "close": "1", "unrealized": "0"}, role="agent", account_id="a")
            raise AssertionError("role gate failed")
        except LedgerError:
            pass
        o = {"decision_ref": "d", "sym": "336260", "side": "buy", "px": "1", "qty": 1, "dup_key": "k"}
        assert append_event(p, "order", o, role="agent", account_id="a") and append_event(p, "order", o, role="agent", account_id="a") is None
        assert validate_file(p) == []
    return True


def _account_default(config_path: str) -> str:
    try:
        with open(config_path, encoding="utf-8") as f:
            v = str(json.load(f).get("account_id") or "")
        return v if v and not v.startswith("<") else "account"
    except (OSError, ValueError):
        return "account"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ledger", description="계좌별 append-only 원장 (기록·검증만)")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--config", default="config/config.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ad = sub.add_parser("append"); ad.add_argument("evt"); ad.add_argument("--json"); ad.add_argument("--file")
    ad.add_argument("--role", choices=sorted(ROLE_EVENTS), default="agent"); ad.add_argument("--account"); ad.add_argument("--ts")
    sub.add_parser("validate")
    tl = sub.add_parser("tail"); tl.add_argument("-n", type=int, default=20)
    sub.add_parser("selftest")
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        selftest(); print(json.dumps({"selftest": "ok"})); return 0
    if a.cmd == "validate":
        problems = validate_file(a.ledger)
        print(json.dumps({"ledger": a.ledger, "lines": len(_read_all(a.ledger)), "problems": problems}, ensure_ascii=False))
        return 0 if not problems else 1
    if a.cmd == "tail":
        for rec in _read_all(a.ledger)[-a.n:]:
            print(json.dumps(rec, ensure_ascii=False))
        return 0
    if a.cmd == "append":
        if bool(a.json) == bool(a.file):
            print("ERROR: --json 또는 --file 중 하나", file=sys.stderr); return 2
        try:
            if a.json:
                data = json.loads(a.json)
            else:
                with open(a.file, encoding="utf-8") as f:
                    data = json.load(f)
        except (OSError, ValueError) as exc:
            print(f"ERROR: invalid json: {exc}", file=sys.stderr); return 2
        try:
            rec = append_event(a.ledger, a.evt, data, role=a.role, account_id=a.account or _account_default(a.config), ts=a.ts)
        except LedgerError as exc:
            print(f"ERROR: {exc}", file=sys.stderr); return 2
        if rec is None:
            print(json.dumps({"skipped": "duplicate dup_key", "evt": a.evt}, ensure_ascii=False)); return 3
        print(json.dumps(rec, ensure_ascii=False, default=str)); return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
