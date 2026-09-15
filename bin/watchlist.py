#!/usr/bin/env python3
"""rrr-swing 감시 목록 CLI — 세션(LLM)이 지금 보는 종목을 적어 둔다. 판단 0.

  bin/watchlist.py set 006800 042700 [--append-ledger]   # 전면 교체
  bin/watchlist.py add 006800 --note "보유 120주"          # 부분 추가(나머지 보존)
  bin/watchlist.py drop 042700                            # 부분 제거(멱등)
  bin/watchlist.py show
  bin/watchlist.py clear

파일은 `local/watchlist.json`, 형태는 종목 → 메모:

    {"006800": {"note": "보유 120주"}, "042700": {}}

종목만 적은 배열 `["006800", "042700"]` 도 읽는다(사람이 손으로 고치는 파일이다).

목록에 담는 것은 종목뿐이다. 유형(evt)·세션(sess)·시각으로는 거르지 않는다 — `sess` 는
주문정책을 고르는 재료이며(장전=지정가만·본장=SOR·동시호가=취소만) 배달 여부의 기준이
아니다. 서버에도 필터를 보내지 않는다.

`set` 은 전면 교체이고 `add`·`drop` 은 나머지 종목과 메모를 보존한다. 종목 하나만 바꿀 때
전면 교체를 쓰면 적지 않은 값이 함께 사라지며, 그 손실은 조용하다.

어댑터는 이 파일을 **이벤트가 올 때마다** 확인한다(inbound_core.WatchList). 여기서 고치면
재기동 없이 다음 이벤트부터 반영된다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

# 종목 코드의 정본은 inbound_core 하나다. 여기서 다시 적으면 갈리고, 갈리면 쓰는 쪽과
# 읽는 쪽이 서로 다른 것을 받아들여 조용히 매칭되지 않는 항목이 생긴다.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inbound_core as _core  # noqa: E402

SYMBOL_RE = _core.SYMBOL_RE


def _validate(sym: str) -> str:
    s = str(sym).strip()
    # `_AL` 접미사는 조회 전용이라 주문 코드에 넣지 않는다 — 감시 목록도 순수 6자리로 맞춘다.
    if not SYMBOL_RE.fullmatch(s):
        raise ValueError(f"종목 코드는 6자리(영숫자 대문자): {sym!r}")
    return s


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        return {str(s): {} for s in raw}
    if isinstance(raw, dict):
        return {str(k): (v if isinstance(v, dict) else {}) for k, v in raw.items()}
    raise ValueError(f"최상위가 객체도 배열도 아니다: {type(raw).__name__}")


def _save(path: str, syms: dict, *, now: str, account_id: str, ledger_path: str | None) -> dict:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # 원자적 교체 — 어댑터가 이벤트마다 이 파일을 읽으므로 반쯤 쓰인 상태를 보면 안 된다.
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(syms, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)
    if ledger_path:
        os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"evt": "subscription", "ts": now, "account_id": account_id,
                                "symbols": sorted(syms), "schema": "rrr_swing_ledger_v1"},
                               ensure_ascii=False) + "\n")
    return syms


def _now(now: str | None) -> str:
    return now or datetime.now().isoformat(timespec="seconds")


def set_symbols(path: str, symbols, *, note: str | None = None, now: str | None = None,
                account_id: str = "account", ledger_path: str | None = None) -> dict:
    """전면 교체. 목록에 없던 종목은 사라진다 — 명령 이름이 그 뜻이다."""
    syms = {}
    for s in symbols:
        syms[_validate(s)] = {"note": note} if note else {}
    return _save(path, syms, now=_now(now), account_id=account_id, ledger_path=ledger_path)


def add(path: str, symbol: str, *, note: str | None = None, now: str | None = None,
        account_id: str = "account", ledger_path: str | None = None) -> dict:
    """부분 추가. 나머지 종목과 **남이 적어 둔 메모**를 지우지 않는다."""
    sym = _validate(symbol)
    syms = _load(path)
    entry = dict(syms.get(sym) or {})
    if note:
        entry["note"] = note
    syms[sym] = entry
    return _save(path, syms, now=_now(now), account_id=account_id, ledger_path=ledger_path)


def drop(path: str, symbol: str, *, now: str | None = None,
         account_id: str = "account", ledger_path: str | None = None) -> dict:
    """부분 제거. 이미 없으면 그대로 둔다(멱등) — 원하는 상태에 이미 있다는 뜻이다."""
    syms = _load(path)
    syms.pop(_validate(symbol), None)
    return _save(path, syms, now=_now(now), account_id=account_id, ledger_path=ledger_path)


def show(path: str) -> dict:
    return _load(path)


def clear(path: str, *, now: str | None = None, account_id: str = "account",
          ledger_path: str | None = None) -> dict:
    return _save(path, {}, now=_now(now), account_id=account_id, ledger_path=ledger_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="watchlist.py", description="rrr-swing 감시 목록 (판단 0)")
    ap.add_argument("--path", default="local/watchlist.json")
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--ledger", default="local/ledger.jsonl")
    ap.add_argument("--append-ledger", action="store_true")
    ap.add_argument("--now", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_set = sub.add_parser("set", help="전면 교체"); p_set.add_argument("symbols", nargs="*"); p_set.add_argument("--note", default=None)
    p_add = sub.add_parser("add", help="부분 추가"); p_add.add_argument("symbol"); p_add.add_argument("--note", default=None)
    p_drop = sub.add_parser("drop", help="부분 제거"); p_drop.add_argument("symbol")
    sub.add_parser("show", help="현재 목록")
    sub.add_parser("clear", help="비우기")
    a = ap.parse_args(argv)

    account = "account"
    try:
        with open(a.config, encoding="utf-8") as f:
            account = json.load(f).get("account_id") or account
    except Exception:  # noqa: BLE001 - 목록 편집이 config 부재로 막히지 않는다
        pass
    ledger = a.ledger if a.append_ledger else None
    kw = {"now": a.now, "account_id": account, "ledger_path": ledger}
    try:
        if a.cmd == "set":
            out = set_symbols(a.path, a.symbols, note=a.note, **kw)
        elif a.cmd == "add":
            out = add(a.path, a.symbol, note=a.note, **kw)
        elif a.cmd == "drop":
            out = drop(a.path, a.symbol, **kw)
        elif a.cmd == "clear":
            out = clear(a.path, **kw)
        else:
            out = show(a.path)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
