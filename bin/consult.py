#!/usr/bin/env python3
"""consult — 손절·청산 협의 기록(콘솔). 세션이 propose(실시간)·pre(사전)로 제안을 남기고, 운영자가 agree/decline 으로 답하고,
status 가 응답 기한 경과를 timeout 으로 1회 기록한다. list 는 열린 협의(pending·agreed)를 보여 준다.

- 파일: local/consults/<id>.json {id, sym, action, px, qty, reason, proposed_at, respond_by, status, pre, level, responded_at, note, timed_out_at}
  + 원장 `consult`(consult_id·sym·action·status …, agent 역할). dup_key=consult|<id>|<status> 라 같은 전이는 원장에 1회만 남는다.
- 상태: pending → agreed | declined | timeout. pre 는 처음부터 agreed(respond_by 없음, 도달 시 세션이 집행·보고). 운영자는 agreed 를 decline 으로 철회할 수 있다.
  timeout·declined 뒤에는 새 제안으로만(재합의 없음).
- 이 도구는 주문을 만들지도 bin/order.py 를 호출하지도 않는다. timeout 뒤 제안대로 집행할지는 세션이 판단한다(AGENTS.md §4). 기한(분)은 인자, 기본 30.
- 시간은 tz 없는 naive KST ISO. `--now` 주입으로 테스트. `--report` 는 console 채널(bin/report.py) 1줄.

  python3 bin/consult.py propose --sym 336260 --action exit|reduce|hold --px 45900 --qty 100 --reason "…" [--respond-min 30] [--report]
  python3 bin/consult.py pre --sym 336260 --level 46000 --qty 100 --reason "…" [--action exit|reduce] [--report]
  python3 bin/consult.py agree <id> [--note …] | decline <id> [--note …]        # 운영자
  python3 bin/consult.py status <id> | list [--all]
종료 코드: 0 성공 / 2 인자·상태 오류 / 30 역할 거부(RS_ROLE=analyst).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
sys.path.insert(0, HERE)

import ledger as lg  # noqa: E402

ACTIONS = ("exit", "reduce", "hold")
PRE_ACTIONS = ("exit", "reduce")  # 사전 합의는 집행 행동만(보류를 미리 합의할 것은 없다)
STATUSES = ("pending", "agreed", "declined", "timeout")
OPEN_STATUSES = ("pending", "agreed")
ACTION_KR = {"exit": "청산", "reduce": "축소", "hold": "보류"}
DEFAULT_RESPOND_MIN = 30


class ConsultError(ValueError):
    pass


@dataclass
class Paths:
    consults: str = "local/consults"
    ledger: str = "local/ledger.jsonl"
    reports: str = "local/reports"


def role_gate() -> str | None:
    """RS_ROLE=analyst(분석 보조)는 협의를 제안·응답하지 않는다(trade-pair 권한 비대칭: envelope 밖 채널 금지)."""
    if os.environ.get("RS_ROLE", "").strip().lower() == "analyst":
        return "RS_ROLE=analyst: 분석 역할은 협의 권한이 없다(exit 30). 협의는 세션 역할(rs-lead)의 bin/consult.py 만"
    return None


def _now(now: str | None) -> str:
    return now or datetime.now().isoformat(timespec="seconds")


def _parse(ts: str) -> datetime:
    try:
        return datetime.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise ConsultError(f"bad timestamp {ts!r}: {exc}") from None


def _plus_min(ts: str, minutes: int) -> str:
    return (_parse(ts) + timedelta(minutes=int(minutes))).isoformat(timespec="seconds")


def _plain(v) -> str:
    d = Decimal(str(v))
    return str(d.quantize(Decimal(1))) if d == d.to_integral_value() else str(d.normalize())


def consult_path(dirpath: str, cid: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(cid))
    return os.path.join(dirpath, f"{safe}.json")


def _read(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except ValueError:
            return None


def _write(path: str, rec: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _load(paths: Paths, cid: str) -> dict:
    rec = _read(consult_path(paths.consults, cid))
    if rec is None:
        raise ConsultError(f"consult not found: {cid}")
    return rec


def _new_id(paths: Paths, sym: str, now: str) -> str:
    base = f"c-{sym}-{now[:19].replace('-', '').replace(':', '')}"
    cid, n = base, 1
    while os.path.exists(consult_path(paths.consults, cid)):
        n += 1
        cid = f"{base}-{n}"
    return cid


def _validate(sym, action, px, qty, reason, *, allowed=ACTIONS) -> str:
    if not str(sym or "").strip():
        raise ConsultError("sym required")
    if action not in allowed:
        raise ConsultError(f"action must be one of {list(allowed)}: {action!r}")
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
        raise ConsultError(f"qty must be an integer >= 1: {qty!r}")
    try:
        d = Decimal(str(px))
    except (InvalidOperation, ValueError):
        raise ConsultError(f"px must be a positive number: {px!r}") from None
    if not d.is_finite() or d <= 0:
        raise ConsultError(f"px must be a positive number: {px!r}")
    if not str(reason or "").strip():
        raise ConsultError("reason required")
    return _plain(d)


def _ledger(paths: Paths, rec: dict, *, account: str, now: str) -> dict | None:
    body = {"consult_id": rec["id"], "sym": rec["sym"], "action": rec["action"], "status": rec["status"], "px": rec.get("px"), "qty": rec.get("qty"),
            "reason": rec.get("reason"), "proposed_at": rec.get("proposed_at"), "respond_by": rec.get("respond_by"), "pre": bool(rec.get("pre")),
            "dup_key": f"consult|{rec['id']}|{rec['status']}"}
    for k in ("level", "note", "responded_at", "timed_out_at"):
        if rec.get(k) is not None:
            body[k] = rec[k]
    return lg.append_event(paths.ledger, "consult", body, role="agent", account_id=account, ts=now)


def _report(paths: Paths, text: str, now: str) -> None:
    import report as rp
    rp.write_report(text, reports_dir=paths.reports, now=now)


def propose(paths: Paths, *, sym: str, action: str, px, qty: int, reason: str, respond_min: int = DEFAULT_RESPOND_MIN, now: str | None = None,
            account: str = "account", report: bool = False) -> dict:
    """실시간 협의 제안 → pending. respond_by = 제안 시각 + respond_min. 집행은 이 함수가 하지 않는다."""
    now = _now(now)
    px_s = _validate(sym, action, px, qty, reason)
    if int(respond_min) < 1:
        raise ConsultError("respond_min must be >= 1")
    rec = {"id": _new_id(paths, str(sym), now), "sym": str(sym), "action": action, "px": px_s, "qty": int(qty), "reason": reason, "proposed_at": now,
           "respond_by": _plus_min(now, respond_min), "status": "pending", "pre": False}
    _write(consult_path(paths.consults, rec["id"]), rec)
    _ledger(paths, rec, account=account, now=now)
    if report:
        _report(paths, f"[손절 협의] {sym} {qty}주 @{px_s} {ACTION_KR[action]} 제안 — {reason}. 응답 기한 {rec['respond_by']}, 무응답 시 제안대로 집행 (id={rec['id']})", now)
    return rec


def pre(paths: Paths, *, sym: str, level, qty: int, reason: str, action: str = "exit", now: str | None = None, account: str = "account", report: bool = False) -> dict:
    """사전 협의 → 처음부터 agreed(pre=true). 수준(level) 도달 시 세션이 집행·보고한다."""
    now = _now(now)
    lv = _validate(sym, action, level, qty, reason, allowed=PRE_ACTIONS)
    rec = {"id": _new_id(paths, str(sym), now), "sym": str(sym), "action": action, "px": lv, "level": lv, "qty": int(qty), "reason": reason, "proposed_at": now,
           "respond_by": None, "status": "agreed", "pre": True, "responded_at": now}
    _write(consult_path(paths.consults, rec["id"]), rec)
    _ledger(paths, rec, account=account, now=now)
    if report:
        _report(paths, f"[손절 사전협의] {sym} {qty}주 — {lv} 도달 시 {ACTION_KR[action]} — {reason} (id={rec['id']})", now)
    return rec


def respond(paths: Paths, cid: str, verdict: str, *, note: str | None = None, now: str | None = None, account: str = "account") -> dict:
    """운영자 응답. agree: pending → agreed. decline: pending|agreed → declined(사전 합의 철회 포함)."""
    now = _now(now)
    if verdict not in ("agree", "decline"):
        raise ConsultError(f"verdict must be agree|decline: {verdict!r}")
    rec = _load(paths, cid)
    new = "agreed" if verdict == "agree" else "declined"
    allowed_from = ("pending",) if new == "agreed" else ("pending", "agreed")
    if rec.get("status") not in allowed_from:
        raise ConsultError(f"{cid}: cannot {verdict} from status {rec.get('status')!r}")
    rec["status"] = new
    rec["responded_at"] = now
    if note is not None:
        rec["note"] = note
    _write(consult_path(paths.consults, cid), rec)
    _ledger(paths, rec, account=account, now=now)
    return rec


def status(paths: Paths, cid: str, *, now: str | None = None, account: str = "account") -> dict:
    """현재 상태. pending 이고 now > respond_by 면 timeout 으로 전이해 1회 기록한다(재호출은 기록 0)."""
    now = _now(now)
    rec = _load(paths, cid)
    if rec.get("status") == "pending" and rec.get("respond_by") and now > str(rec["respond_by"]):
        rec["status"] = "timeout"
        rec["timed_out_at"] = now
        _write(consult_path(paths.consults, cid), rec)
        _ledger(paths, rec, account=account, now=now)
    return rec


def list_consults(paths: Paths, *, now: str | None = None, all: bool = False) -> list[dict]:
    """열린 협의(pending·agreed). 읽기 전용 — 기한 경과는 overdue 로 표시만 하고 전이는 status 가 한다."""
    now = _now(now)
    out: list[dict] = []
    if not os.path.isdir(paths.consults):
        return out
    for name in sorted(os.listdir(paths.consults)):
        if not name.endswith(".json"):
            continue
        rec = _read(os.path.join(paths.consults, name))
        if not rec or (not all and rec.get("status") not in OPEN_STATUSES):
            continue
        item = dict(rec)
        item["overdue"] = bool(rec.get("status") == "pending" and rec.get("respond_by") and now > str(rec["respond_by"]))
        out.append(item)
    out.sort(key=lambda r: (str(r.get("proposed_at") or ""), str(r.get("id") or "")))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="consult", description="손절·청산 협의 기록(콘솔). 판단·주문 0 — timeout 뒤 집행은 세션의 판단")
    p.add_argument("--consults-dir", default="local/consults")
    p.add_argument("--ledger", default="local/ledger.jsonl")
    p.add_argument("--reports-dir", default="local/reports")
    p.add_argument("--config", default="config/config.json")
    p.add_argument("--account", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("propose", help="실시간 협의 제안(pending)")
    pr.add_argument("--sym", required=True); pr.add_argument("--action", required=True, help="exit|reduce|hold"); pr.add_argument("--px", required=True)
    pr.add_argument("--qty", required=True, type=int); pr.add_argument("--reason", required=True); pr.add_argument("--respond-min", type=int, default=DEFAULT_RESPOND_MIN)
    pr.add_argument("--report", action="store_true"); pr.add_argument("--now", default=None)
    pe = sub.add_parser("pre", help="사전 협의(agreed, pre=true)")
    pe.add_argument("--sym", required=True); pe.add_argument("--level", required=True); pe.add_argument("--qty", required=True, type=int); pe.add_argument("--reason", required=True)
    pe.add_argument("--action", default="exit", help="exit|reduce"); pe.add_argument("--report", action="store_true"); pe.add_argument("--now", default=None)
    for name in ("agree", "decline"):
        sp = sub.add_parser(name, help=f"운영자 응답: {name}")
        sp.add_argument("id"); sp.add_argument("--note", default=None); sp.add_argument("--now", default=None)
    st = sub.add_parser("status", help="현재 상태(기한 경과면 timeout 1회 기록)"); st.add_argument("id"); st.add_argument("--now", default=None)
    ls = sub.add_parser("list", help="열린 협의"); ls.add_argument("--all", action="store_true"); ls.add_argument("--now", default=None)
    a = p.parse_args(argv)
    denied = role_gate()
    if denied:
        print(json.dumps({"error": denied, "rc": 30}, ensure_ascii=False))
        return 30
    paths = Paths(consults=a.consults_dir, ledger=a.ledger, reports=a.reports_dir)
    account = a.account or lg._account_default(a.config)
    try:
        if a.cmd == "propose":
            out = propose(paths, sym=a.sym, action=a.action, px=a.px, qty=a.qty, reason=a.reason, respond_min=a.respond_min, now=a.now, account=account, report=a.report)
        elif a.cmd == "pre":
            out = pre(paths, sym=a.sym, level=a.level, qty=a.qty, reason=a.reason, action=a.action, now=a.now, account=account, report=a.report)
        elif a.cmd in ("agree", "decline"):
            out = respond(paths, a.id, a.cmd, note=a.note, now=a.now, account=account)
        elif a.cmd == "status":
            out = status(paths, a.id, now=a.now, account=account)
        else:
            out = list_consults(paths, now=a.now, all=a.all)
    except (ConsultError, lg.LedgerError) as exc:
        print(json.dumps({"error": str(exc), "rc": 2}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
