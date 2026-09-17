#!/usr/bin/env python3
"""시장 축 샘플러 — Kiwoom REST 읽기 전용, 판단 0. rrr venv 에서 rrr 의 토큰 공급자(미러 파일 읽기, 발급 없음)로 실행한다.

주기마다 두 TR 을 원문 그대로 저장한다.
- ka10051 업종별투자자순매수(코스피 mrkt_tp='0', 금액 억원 amt_qty_tp='0', 통합 stex_tp='3'): 종합(001)·규모별·업종별 투자자 당일 누적 순매수 스냅샷.
  이력이 없는 스냅샷이라 주기 저장이 곧 시계열이다.
- ka90005 프로그램매매추이 시간대별(코스피 P00101, 금액, 시간대별): 최근 100분의 1분 버킷 당일 누적. cntr_tm 키로 병합해 하루치를 만든다.

실행: cd "$PROVIDER_DIR" && (set -a; . ./.env; set +a; python3 "$SWING_DIR/bin/detach.py" ./.venv/bin/python "$SWING_DIR/scripts/research/market_sampler.py" --out <dir>)
출력: <out>/<HHMMSS>/ka10051.json, <out>/ka90005_merged.json(cntr_tm→row), <out>/sampler.log
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime

from rrr.ingestor.quote_seeder import create_kiwoom_rest_client


def log(out_dir: str, msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with open(os.path.join(out_dir, "sampler.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def dump(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True)
    if isinstance(obj, list):
        return [dump(x) for x in obj]
    return obj


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--until", default="15:40")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    client = create_kiwoom_rest_client()
    until_h, until_m = (int(x) for x in a.until.split(":"))
    merged_path = os.path.join(a.out, "ka90005_merged.json")
    merged = json.load(open(merged_path, encoding="utf-8")) if os.path.exists(merged_path) else {}
    log(a.out, f"start interval={a.interval} until={a.until} merged={len(merged)}")
    while True:
        now = datetime.now()
        if (now.hour, now.minute) >= (until_h, until_m):
            log(a.out, "until reached, stop")
            return 0
        stamp_dir = os.path.join(a.out, now.strftime("%H%M%S"))
        os.makedirs(stamp_dir, exist_ok=True)
        try:
            r = client.sector.net_purchase(mrkt_tp="0", amt_qty_tp="0", stex_tp="3")
            rows = dump(r.inds_netprps)
            with open(os.path.join(stamp_dir, "ka10051.json"), "w", encoding="utf-8") as f:
                json.dump({"sampled_at": now.isoformat(timespec="seconds"), "mrkt_tp": "0", "amt_qty_tp": "0", "stex_tp": "3", "rows": rows}, f, ensure_ascii=False)
            n51 = len(rows)
        except Exception as exc:  # noqa: BLE001 — 수집기는 죽지 않고 기록만 남긴다
            n51 = -1
            log(a.out, f"ka10051 failed: {exc!r}")
        try:
            p = client.program.trend(date=now.strftime("%Y%m%d"), amt_qty_tp="1", mrkt_tp="P00101", min_tic_tp="0", stex_tp="3")
            prow = dump(p if isinstance(p, list) else getattr(p, "items", None) or getattr(p, "list", None) or [])
            added = 0
            for row in prow:
                key = str(row.get("cntr_tm"))
                if key not in merged:
                    added += 1
                merged[key] = dict(row, sampled_at=now.isoformat(timespec="seconds"))
            tmp = merged_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False)
            os.replace(tmp, merged_path)
            n05 = f"{len(prow)} (+{added}, merged={len(merged)})"
        except Exception as exc:  # noqa: BLE001
            n05 = f"failed: {exc!r}"
            log(a.out, f"ka90005 failed: {exc!r}")
        log(a.out, f"tick {now.strftime('%H:%M:%S')} ka10051={n51} ka90005={n05}")
        time.sleep(a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
