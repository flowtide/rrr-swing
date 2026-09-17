#!/usr/bin/env python3
"""NXT 마감(20:00) 후 확정된 5분봉 캔들을 게이트웨이에서 사후 동기화하여 detail/200500 에 저장."""
import argparse
import datetime
import json
import os
import sys
import urllib.request

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--out", default=None, help="Root directory for the date (e.g. local/research/orderbook/YYYY-MM-DD)")
    args = parser.parse_args()

    rs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    cfg_path = os.path.join(rs_dir, "config", "config.json")
    if not os.path.exists(cfg_path):
        print(f"Error: config not found at {cfg_path}", file=sys.stderr)
        sys.exit(1)

    cfg = json.load(open(cfg_path, encoding="utf-8"))
    gw = cfg["gw"]["base_url"]
    key = cfg["gw"]["api_key"]

    out_base = args.out or os.path.join(rs_dir, "local", "research", "orderbook", args.date)
    nxt_snap_dir = os.path.join(out_base, "detail", "200500")
    os.makedirs(nxt_snap_dir, exist_ok=True)

    # 종목 리스트 로드 (config/stock_meta_18.json 우선, 없으면 gw watch-groups)
    symbols = []
    meta_path = os.path.join(rs_dir, "config", "stock_meta_18.json")
    if not os.path.exists(meta_path):
        meta_path = os.path.join(rs_dir, "local", "research", "orderbook", "stock_meta_18.json")
    if os.path.exists(meta_path):
        meta = json.load(open(meta_path, encoding="utf-8"))
        symbols = sorted(meta.keys())
    else:
        req = urllib.request.Request(f"{gw}/api/watch-groups", headers={"X-API-Key": key})
        with urllib.request.urlopen(req, timeout=10) as resp:
            groups = json.load(resp)
        sym_set = set()
        for g in groups:
            r_g = urllib.request.Request(f"{gw}/api/watch-groups/{g['group_id']}/stocks", headers={"X-API-Key": key})
            with urllib.request.urlopen(r_g, timeout=10) as resp_g:
                stocks = json.load(resp_g)
                for s in stocks:
                    sym_set.add(s.get("symbol") or s.get("code"))
        symbols = sorted(x for x in sym_set if x)

    print(f"[{args.date}] Fetching detail with NXT candles up to 20:00 for {len(symbols)} symbols into {nxt_snap_dir}...")
    success = 0
    for sym in symbols:
        url = f"{gw}/api/stocks/{sym}/detail"
        req = urllib.request.Request(url, headers={"X-API-Key": key})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                d = json.load(resp)
            with open(os.path.join(nxt_snap_dir, f"{sym}.json"), "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            candles = [c for c in d.get("candles", []) if c.get("ts_start", "")[:10] == args.date]
            nxt_c = [c for c in candles if c.get("ts_start", "")[11:16] > "15:30"]
            last_ts = nxt_c[-1]["ts_start"] if nxt_c else (candles[-1]["ts_start"] if candles else "None")
            print(f"  {sym}: today={len(candles)}, nxt={len(nxt_c)}, last={last_ts}")
            success += 1
        except Exception as e:
            print(f"  {sym}: FAILED ({e})", file=sys.stderr)

    print(f"Done patching {args.date} NXT candles ({success}/{len(symbols)} succeeded).")

if __name__ == "__main__":
    main()
