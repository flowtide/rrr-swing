#!/usr/bin/env python3
"""호가 증거 연구용 샘플러 — GET 전용, 판단 0. docs/07-orderbook_evidence_study.md §7 표본 수집.

주기마다 (1) 활성 종목의 `/api/stocks/{sym}/market-context?bars=0&market=none` 전체 봉을 원문 그대로 저장하고
(2) `/api/events` replay 를 커서로 이어 받아 events.jsonl 에 누적한다. 종목 집합은 --symbols 와
이벤트에 등장한 종목의 합집합이라 장중 새 종목이 나타나면 다음 tick 부터 함께 담는다.

출력: <out>/<HHMMSS>/<sym>.json, <out>/events.jsonl, <out>/sampler.log. X-API-Key = config gw.api_key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

REQUEST_TIMEOUT_SEC = 10.0


def get_json(base_url: str, path: str, token: str) -> tuple[int | None, dict | None, str | None]:
    req = urllib.request.Request(f"{base_url.rstrip('/')}{path}", method="GET",
                                 headers={"Accept": "application/json", "User-Agent": "rrr-swing-ob-sampler/1", "X-API-Key": token})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
            return int(getattr(resp, "status", 200)), json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        return exc.code, None, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return None, None, str(exc)


def log(out_dir: str, msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with open(os.path.join(out_dir, "sampler.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def pull_events(base_url: str, token: str, out_dir: str, cursor: str) -> tuple[str, set[str]]:
    """커서 이후 이벤트를 전부 받아 events.jsonl 에 붙인다. (새 커서, 등장 종목) 반환."""
    seen: set[str] = set()
    while True:
        status, data, err = get_json(base_url, f"/api/events?since={cursor}&limit=500", token)
        if data is None:
            log(out_dir, f"events fetch failed status={status} err={err}")
            return cursor, seen
        entries = data.get("entries") or []
        if entries:
            with open(os.path.join(out_dir, "events.jsonl"), "a", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
                    if e.get("sym"):
                        seen.add(e["sym"])
        cursor = data.get("next_since") or cursor
        if not data.get("truncated"):
            return cursor, seen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="호가 증거 샘플러 (GET 전용)")
    ap.add_argument("--symbols", default="", help="쉼표 구분 초기 종목")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--until", default="20:10", help="HH:MM 이후 종료")
    ap.add_argument("--since", default="0", help="events replay 시작 커서(redis id 또는 0)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--mode", choices=("context", "detail"), default="context",
                    help="context: market-context 전체봉+events / detail: /detail 원본(orderbook_bars 의 total_*_delta·ask_bid_ratio)")
    a = ap.parse_args(argv)
    with open(a.config, encoding="utf-8") as f:
        gw = json.load(f)["gw"]
    base_url, token = os.environ.get("RS_GW_BASE_URL") or gw["base_url"], gw["api_key"]
    os.makedirs(a.out, exist_ok=True)
    symbols = {s.strip() for s in a.symbols.split(",") if s.strip()}
    cursor = a.since
    until_h, until_m = (int(x) for x in a.until.split(":"))
    log(a.out, f"start mode={a.mode} symbols={sorted(symbols)} interval={a.interval} until={a.until}")
    while True:
        now = datetime.now()
        if (now.hour, now.minute) >= (until_h, until_m):
            log(a.out, "until reached, stop")
            return 0
        if a.mode == "context":
            cursor, seen = pull_events(base_url, token, a.out, cursor)
            symbols |= seen
        stamp_dir = os.path.join(a.out, now.strftime("%H%M%S"))
        os.makedirs(stamp_dir, exist_ok=True)
        ok = fail = 0
        for sym in sorted(symbols):
            path = f"/api/stocks/{sym}/market-context?bars=0&market=none" if a.mode == "context" else f"/api/stocks/{sym}/detail"
            status, data, err = get_json(base_url, path, token)
            if data is None:
                fail += 1
                log(a.out, f"{sym} market-context failed status={status} err={err}")
                continue
            with open(os.path.join(stamp_dir, f"{sym}.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            ok += 1
        log(a.out, f"tick {now.strftime('%H:%M:%S')} symbols={len(symbols)} ok={ok} fail={fail} cursor={cursor}")
        time.sleep(a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
