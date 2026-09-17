#!/usr/bin/env python3
"""큰손(체결 1건 1억 초과) 순매수가 다음 봉 방향을 가르는지 — 조건부 집계. 판단·추천 0.

입력: ob_sampler.py `--mode detail` 스냅샷(`/detail` 의 trade_buckets·candles). 5분 확정봉, 정규장 09:00~15:20.
정의 세 가지를 같은 틀로 센다(대조군 = 개미 구간 1천만 이하):
- A 절대: 큰손 순매수 금액 |net| >= X 원
- R 비율: 큰손 순매수 금액 / 봉 전체 거래대금 >= r
- Z 이례: 종목의 직전 N봉 큰손 순매수 분포 대비 z-score >= z
결과: 신호 봉 t 이후 k=1·3·6 봉 종가 수익률(bp)의 평균과 신호 방향 일치율. 같은 봉 t 의 수익률은 쓰지 않는다(동행은 예측이 아니다).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

REG_START, REG_END = "09:00", "15:20"
WHALE_LOWER = 100_000_000   # upper_amount_won > 1억 → 큰손 (5억이하·5억초과)
ANT_UPPER = 10_000_000      # upper_amount_won <= 1천만 → 개미
ZWIN = 24                   # z-score 창(봉), 최소 12봉


def next_ts(ts: str, k: int) -> str:
    return (datetime.fromisoformat(ts) + timedelta(minutes=5 * k)).isoformat(timespec="seconds")


def in_reg(ts: str) -> bool:
    return REG_START <= ts[11:16] <= REG_END


def tier_group(t: dict) -> str:
    up = t["upper_amount_won"]
    if up is None or up > WHALE_LOWER:
        return "whale"
    if up <= ANT_UPPER:
        return "ant"
    return "mid"


def load(out_dir: str, date: str):
    ddir = os.path.join(out_dir, "detail")
    snap = sorted(d for d in os.listdir(ddir) if d.isdigit())[-1]
    data = {}
    for fp in glob.glob(os.path.join(ddir, snap, "*.json")):
        d = json.load(open(fp, encoding="utf-8"))
        sym = os.path.basename(fp)[:-5]
        closes = {c["ts_start"]: float(c["close"]) for c in d.get("candles") or [] if not c["is_partial"] and c["ts_start"][:10] == date}
        rows = {}
        for tb in d.get("trade_buckets") or []:
            ts = tb["ts_start"]
            if tb["is_partial"] or ts[:10] != date:
                continue
            g = defaultdict(lambda: {"buy": 0.0, "sell": 0.0, "bcnt": 0, "scnt": 0})
            for t in tb["tiers"]:
                k = tier_group(t)
                g[k]["buy"] += float(t["buy_amount"]); g[k]["sell"] += float(t["sell_amount"])
                g[k]["bcnt"] += t["buy_count"]; g[k]["scnt"] += t["sell_count"]
            total = sum(v["buy"] + v["sell"] for v in g.values())
            rows[ts] = {"g": dict(g), "total": total}
        data[sym] = {"closes": closes, "rows": rows}
    return snap, data


KOSDAQ = {"196170", "277810"}  # 알테오젠·레인보우로보틱스. 나머지 표본은 KOSPI


def load_market(out_dir: str, date: str) -> dict[str, dict[str, dict]]:
    """global market-context 슬롯 → {market: {ts: {"close": float, "bp": int, "rising": int, "falling": int}}}"""
    fp = os.path.join(out_dir, "global_market_context.json")
    if not os.path.exists(fp):
        return {}
    d = json.load(open(fp, encoding="utf-8"))
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for slot in d.get("slots") or []:
        ts = slot["slot_start"]
        if ts[:10] != date or slot.get("is_partial"):
            continue
        for mk, node in (slot.get("markets") or {}).items():
            idx, br = (node or {}).get("index") or {}, (node or {}).get("breadth") or {}
            if idx.get("close") is None:
                continue
            out[mk][ts] = {"close": float(idx["close"]), "bp": idx.get("return_bp"),
                           "rising": br.get("rising_count"), "falling": br.get("falling_count")}
    return out


def fwd_bp(closes: dict, ts: str, k: int):
    c0, ck = closes.get(ts), closes.get(next_ts(ts, k))
    return None if c0 is None or ck is None or c0 == 0 else (ck / c0 - 1) * 1e4


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--date", required=True)
    a = ap.parse_args(argv)
    snap, data = load(a.out, a.date)
    market = load_market(a.out, a.date)

    # 봉별 신호값 계산
    bars = []  # (sym, ts, group→net, group→ratio, group→z, same_bar_bp)
    for sym, d in data.items():
        ts_list = sorted(t for t in d["rows"] if in_reg(t))
        hist = defaultdict(list)
        for ts in ts_list:
            r = d["rows"][ts]
            rec = {"sym": sym, "ts": ts, "net": {}, "ratio": {}, "z": {}, "cnt": {}}
            for grp in ("whale", "mid", "ant"):
                gv = r["g"].get(grp, {"buy": 0.0, "sell": 0.0, "bcnt": 0, "scnt": 0})
                net = gv["buy"] - gv["sell"]
                rec["net"][grp] = net
                rec["ratio"][grp] = net / r["total"] if r["total"] else 0.0
                rec["cnt"][grp] = (gv["bcnt"], gv["scnt"])
                h = hist[grp]
                if len(h) >= 12:
                    mu, sd = statistics.mean(h), statistics.pstdev(h)
                    rec["z"][grp] = (net - mu) / sd if sd > 0 else 0.0
                else:
                    rec["z"][grp] = None
                h.append(net)
                if len(h) > ZWIN:
                    h.pop(0)
            prev = d["closes"].get(next_ts(ts, -1)); cur = d["closes"].get(ts)
            rec["same_bp"] = (cur / prev - 1) * 1e4 if prev and cur else None
            rec["fwd"] = {k: fwd_bp(d["closes"], ts, k) for k in (1, 3, 6, 12)}
            mk = market.get("kosdaq" if sym in KOSDAQ else "kospi", {})
            mclose = {t: v["close"] for t, v in mk.items()}
            slot = mk.get(ts)
            rec["idx_bp"] = slot["bp"] if slot else None
            rec["breadth"] = (slot["rising"], slot["falling"]) if slot and slot.get("rising") is not None else None
            rec["same_ex"] = (rec["same_bp"] - slot["bp"]) if (rec["same_bp"] is not None and slot and slot["bp"] is not None) else None
            rec["fwd_ex"] = {}
            for k in (1, 3, 6, 12):
                fi = fwd_bp(mclose, ts, k)
                rec["fwd_ex"][k] = (rec["fwd"][k] - fi) if (rec["fwd"][k] is not None and fi is not None) else None
            bars.append(rec)
    # D 누적·지속: 종목별 시계열에서 롤링 6봉(30분) 큰손 순매수 비율, 당일 누적 비율, 부호 지속(직전 3봉 같은 부호)
    by_sym = defaultdict(list)
    for rec in bars:
        by_sym[rec["sym"]].append(rec)
    for sym, recs in by_sym.items():
        recs.sort(key=lambda r: r["ts"])
        tot_hist, net_hist, cum_net, cum_tot = [], [], 0.0, 0.0
        for rec in recs:
            r = data[sym]["rows"][rec["ts"]]
            net_hist.append(rec["net"]["whale"]); tot_hist.append(r["total"])
            cum_net += rec["net"]["whale"]; cum_tot += r["total"]
            w6n, w6t = sum(net_hist[-6:]), sum(tot_hist[-6:])
            rec["roll6"] = w6n / w6t if w6t and len(net_hist) >= 6 else None
            rec["cumr"] = cum_net / cum_tot if cum_tot else None
            mk = market.get("kosdaq" if sym in KOSDAQ else "kospi", {})
            first_ts = recs[0]["ts"]
            c0, c1 = data[sym]["closes"].get(next_ts(first_ts, -1)) or data[sym]["closes"].get(first_ts), data[sym]["closes"].get(rec["ts"])
            i0, i1 = (mk.get(next_ts(first_ts, -1)) or mk.get(first_ts) or {}).get("close"), (mk.get(rec["ts"]) or {}).get("close")
            rec["cum_ex"] = ((c1 / c0 - 1) - (i1 / i0 - 1)) * 1e4 if c0 and c1 and i0 and i1 else None
            last3 = [(x > 0) - (x < 0) for x in net_hist[-3:]]
            rec["persist3"] = last3[0] if len(last3) == 3 and len(set(last3)) == 1 and last3[0] != 0 else 0
    print(f"snapshot={snap} symbols={len(data)} 정규장 확정봉={len(bars)}")

    def report(title, sel, excess=False):
        """sel(rec) → +1/-1/0. 방향별 k=1,3,6,12 평균·일치율. excess=True 면 지수 대비 초과수익률."""
        rows = {}
        for k in (1, 3, 6, 12):
            same = tot = 0; xs = []
            for rec in bars:
                s = sel(rec); f = (rec["fwd_ex"] if excess else rec["fwd"])[k]
                if not s or f is None:
                    continue
                xs.append(f * s)  # 신호 방향으로 부호 정렬
                if f != 0:
                    tot += 1; same += (f > 0) == (s > 0)
            rows[k] = (len(xs), statistics.mean(xs) if xs else 0.0, same, tot)
        n = rows[1][0]
        print(f"  {title:<34} n={n:3d} |{'EX' if excess else '  '} " + " | ".join(f"k={k}: {m:+6.1f}bp 일치 {s}/{t}={100*s/t if t else 0:3.0f}%" for k, (_, m, s, t) in rows.items()))

    print("\n[기준선] 모든 봉을 무작위 방향(+)으로 두면 평균 0·일치 50% 근처여야 한다")
    report("전 봉 (+1 고정)", lambda r: 1)
    report("전 봉 같은 봉 수익률 부호 추종(모멘텀)", lambda r: 0 if r["same_bp"] is None else (r["same_bp"] > 0) - (r["same_bp"] < 0))

    for grp, label in (("whale", "큰손(1억↑)"), ("ant", "개미(1천만↓, 대조군)"), ("mid", "중간(1천만~1억)")):
        print(f"\n[{label}]")
        for x in (1e8, 3e8, 1e9):
            report(f"A 절대 |net|>={x/1e8:.0f}억", lambda r, x=x, g=grp: (1 if r["net"][g] >= x else -1 if r["net"][g] <= -x else 0))
        for rr in (0.2, 0.4, 0.6):
            report(f"R 비율 |net/총거래대금|>={rr:.1f}", lambda r, rr=rr, g=grp: (1 if r["ratio"][g] >= rr else -1 if r["ratio"][g] <= -rr else 0))
        for zz in (1.5, 2.0, 3.0):
            report(f"Z 이례 |z|>={zz:.1f} (직전 {ZWIN}봉)", lambda r, zz=zz, g=grp: (0 if r["z"][g] is None else 1 if r["z"][g] >= zz else -1 if r["z"][g] <= -zz else 0))

    print("\n[D 누적·지속 — 큰손] 롤링 6봉(30분) 비율 / 당일 누적 비율 / 부호 3봉 지속")
    for rr in (0.1, 0.2, 0.3):
        report(f"롤링6봉 |net/총거래대금|>={rr:.1f}", lambda r, rr=rr: (0 if r.get("roll6") is None else 1 if r["roll6"] >= rr else -1 if r["roll6"] <= -rr else 0))
    for rr in (0.1, 0.2, 0.3):
        report(f"당일누적 |net/총거래대금|>={rr:.1f}", lambda r, rr=rr: (0 if r.get("cumr") is None else 1 if r["cumr"] >= rr else -1 if r["cumr"] <= -rr else 0))
    report("부호 3봉 지속", lambda r: r.get("persist3", 0))
    report("부호 3봉 지속 & 롤링6봉 비율>=0.2", lambda r: r.get("persist3", 0) if r.get("roll6") is not None and abs(r["roll6"]) >= 0.2 and (r["roll6"] > 0) == (r.get("persist3", 0) > 0) else 0)

    print("\n[큰손 R>=0.4 신호를 같은 봉 수익률로 층화] 같은 봉이 이미 그 방향이면 모멘텀, 아니면 선행")
    def sel_r(r, want):
        s = 1 if r["ratio"]["whale"] >= 0.4 else -1 if r["ratio"]["whale"] <= -0.4 else 0
        if not s or r["same_bp"] is None:
            return 0
        agree = (r["same_bp"] > 0) == (s > 0) and r["same_bp"] != 0
        return s if agree == want else 0
    report("같은 봉 동행(이미 움직임)", lambda r: sel_r(r, True))
    report("같은 봉 비동행/정체(선행 후보)", lambda r: sel_r(r, False))

    print("\n[지수 결합] EX = 지수 대비 초과수익률(종목 − 지수). 기준선은 0·50% 근처여야 한다")
    report("전 봉 (+1 고정)", lambda r: 1, excess=True)
    def sig_r(r, thr=0.4):
        return 1 if r["ratio"]["whale"] >= thr else -1 if r["ratio"]["whale"] <= -thr else 0
    def not_moved_ex(r):
        s = sig_r(r)
        return s if s and r["same_ex"] is not None and (r["same_ex"] * s) <= 0 else 0
    def absorb_vs_market(r):
        """큰손 매수인데 지수는 그 봉에 내린(≤−5bp) 봉, 또는 큰손 매도인데 지수는 오른 봉 — 시장을 거스른 큰손"""
        s = sig_r(r)
        return s if s and r["idx_bp"] is not None and (r["idx_bp"] * s) <= -5 else 0
    print("  ① 큰손 흡수 — 큰손 R>=0.4 & 같은 봉 초과수익률 <= 0 (종목이 지수만큼도 못 움직임)")
    report("    raw", not_moved_ex); report("    excess", not_moved_ex, excess=True)
    print("  ①' 시장 역행 — 큰손 R>=0.4 & 지수 같은 봉 반대(|idx|>=5bp)")
    report("    raw", absorb_vs_market); report("    excess", absorb_vs_market, excess=True)
    print("  ② 당일 누적 사분면 — 큰손 누적비율 >=0.2 × 종목 누적 초과수익률 부호")
    def quad(r, want_ex_sign):
        s = 0 if r.get("cumr") is None else 1 if r["cumr"] >= 0.2 else -1 if r["cumr"] <= -0.2 else 0
        if not s or r.get("cum_ex") is None:
            return 0
        ex_sign = (r["cum_ex"] > 0) - (r["cum_ex"] < 0)
        return s if ex_sign * s == want_ex_sign else 0
    report("    큰손과 초과수익 불일치(숨은 매집/분산)", lambda r: quad(r, -1)); report("      〃 excess", lambda r: quad(r, -1), excess=True)
    report("    큰손과 초과수익 일치(확인)", lambda r: quad(r, 1)); report("      〃 excess", lambda r: quad(r, 1), excess=True)
    print("  ③ 시장 국면별 큰손 R>=0.4 — breadth 하락우위 vs 상승우위 봉")
    report("    하락우위(falling>rising)", lambda r: sig_r(r) if r["breadth"] and r["breadth"][1] > r["breadth"][0] else 0, excess=True)
    report("    상승우위(rising>=falling)", lambda r: sig_r(r) if r["breadth"] and r["breadth"][0] >= r["breadth"][1] else 0, excess=True)
    print("  ④ Z 이례도 |z|>=1.5 의 되돌림이 지수 되돌림인가 종목 고유인가")
    zsel = lambda r: (0 if r["z"]["whale"] is None else 1 if r["z"]["whale"] >= 1.5 else -1 if r["z"]["whale"] <= -1.5 else 0)
    report("    raw", zsel); report("    excess", zsel, excess=True)

    print("\n[큰손 비율 상위 봉 (|net/총거래대금| 순, 상위 15)] sym ts 큰손net(억) 비율 건수(매수/매도) 같은봉bp | k1 k3 k6")
    top = sorted((r for r in bars if r["ratio"]["whale"] != 0), key=lambda r: -abs(r["ratio"]["whale"]))[:15]
    for r in top:
        f = r["fwd"]
        print(f"  {r['sym']} {r['ts'][11:16]} {r['net']['whale']/1e8:+8.1f} {r['ratio']['whale']:+.2f} {r['cnt']['whale']} {r['same_bp'] if r['same_bp'] is None else round(r['same_bp']):>6} | "
              + " ".join("  --" if f[k] is None else f"{f[k]:+5.0f}" for k in (1, 3, 6)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
