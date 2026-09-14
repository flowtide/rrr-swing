#!/usr/bin/env python3
"""rrr-swing 구독 선언 CLI — 세션(LLM)이 자기 유니버스(보유 + active plan 종목)와 이벤트 유형·세션·만료를 선언한다.

결정 3: 이 코드는 판단하지 않는다. 선언을 저장하고(local/subscriptions.json), 원장 형식의 subscription 이벤트를
1줄 append 한다(--append-ledger, scripts/ledger.py).

  bin/subscribe.py set --symbols 336260,003230 --events support_return,resistance_break --sessions REG_KRX_NXT \
                       --expires 2026-06-18T20:00:00 [--append-ledger]
  bin/subscribe.py show
  bin/subscribe.py clear
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime

# 정본은 inbound_core 하나다. 여기서 다시 적으면 갈리고, 갈려도 조용히 버려진다.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inbound_core as _core  # noqa: E402

EVENT_TYPES = _core.SUBSCRIBABLE_EVENT_TYPES
SESSIONS = _core.SESSION_TOKENS
SYMBOL_RE = re.compile(r"^[0-9A-Z]{6}$")
EMPTY = {"symbols": [], "event_types": [], "sessions": [], "expires_at": ""}


def _validate(symbols, events, sessions, expires):
    bad = [s for s in symbols if not SYMBOL_RE.match(s)]
    if bad:
        raise ValueError(f"symbol 형식 오류(6자리): {bad}")
    bad = [e for e in events if e not in EVENT_TYPES]
    if bad:
        raise ValueError(f"event_type 은 {EVENT_TYPES} 중 하나: {bad}")
    bad = [s for s in sessions if s not in SESSIONS]
    if bad:
        raise ValueError(f"session 은 {SESSIONS} 중 하나: {bad}")
    try:
        datetime.fromisoformat(expires)
    except (TypeError, ValueError):
        raise ValueError(f"expires 는 naive KST ISO(YYYY-MM-DDTHH:MM:SS): {expires!r}")


def set_subscription(path: str, *, account_id: str, symbols, events, sessions, expires: str,
                     now: str | None = None, ledger_path: str | None = None) -> dict:
    symbols, events, sessions = list(symbols), list(events), list(sessions)
    _validate(symbols, events, sessions, expires)
    declared = now or datetime.now().isoformat(timespec="seconds")
    rec = {"account_id": account_id, "symbols": symbols, "event_types": events, "sessions": sessions,
           "expires_at": expires, "declared_at": declared}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    if ledger_path:
        os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"evt": "subscription", "ts": declared, "account_id": account_id, "symbols": symbols,
                                "event_types": events, "sessions": sessions, "expires_at": expires,
                                "schema": "rrr_swing_ledger_v1"}, ensure_ascii=False) + "\n")
    return rec


def show(path: str) -> dict:
    if not os.path.exists(path):
        return dict(EMPTY)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def clear(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(EMPTY), f, ensure_ascii=False, indent=1)



def main(argv=None) -> int:
    import argparse, sys
    ap = argparse.ArgumentParser(description="rrr-swing 구독 선언 (배달 대상 선언만, 판단 0)")
    ap.add_argument("cmd", choices=["set", "show", "clear"])
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--path", default="local/subscriptions.json")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--events", default="")
    ap.add_argument("--sessions", default="REG_KRX_NXT")
    ap.add_argument("--expires", default="")
    ap.add_argument("--append-ledger", action="store_true", help="local/ledger.jsonl 에 subscription 이벤트 1줄")
    ap.add_argument("--ledger", default="local/ledger.jsonl")
    a = ap.parse_args(argv)
    if a.cmd == "show":
        print(json.dumps(show(a.path), ensure_ascii=False)); return 0
    if a.cmd == "clear":
        clear(a.path); print("cleared"); return 0
    cfg = {}
    if os.path.exists(a.config):
        with open(a.config, encoding="utf-8") as f:
            cfg = json.load(f)
    account = str(cfg.get("account_id") or "account")
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
    try:
        rec = set_subscription(a.path, account_id=account, symbols=split(a.symbols), events=split(a.events),
                               sessions=split(a.sessions), expires=a.expires,
                               ledger_path=a.ledger if a.append_ledger else None)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2
    print(json.dumps(rec, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
