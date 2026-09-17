#!/usr/bin/env python3
"""호가 증거 연구 분석 — docs/07-orderbook_evidence_study.md §7 조건부 검정. 판단·추천 0, 집계만.

입력: ob_sampler.py 출력 디렉터리(최신 스냅샷의 <sym>.json 전체봉 + events.jsonl).
- 봉 유효성은 응답 단위 `gap` 플래그를 무시하고 봉 자체로 재판정한다(weakness 비고 orderbook 있고 n>=N_THIN, partial 아님).
  `gap` 은 `_has_gap(ts_values)` 를 응답 전체에 한 번 적용한 값이라 bars=0 에서는 전날 결손 하나가 전 봉을 물들인다.
- §4.1: 같은 봉 net_ofi 부호 vs return_bp 부호 일치율을 |return_bp| 층별로.
- §7-2: 존 이벤트 봉에서 bid_flow(지지)/ask_flow(저항) 부호별 다음 1·3봉 레벨 유지율. |return_bp| 층화.
- §7-3: n 분포. 부가: 가격 정체 봉의 net_ofi 부호 vs 다음 봉 return_bp.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta

N_THIN = 50
REG_START, REG_END = "09:00", "15:20"  # 확정봉 ts_start 기준 정규장(15:20 봉 = 15:20~15:25, 15:25 봉은 동시호가 걸림)
ZONE_EVTS = ("support_enter", "support_return", "support_break", "resistance_enter", "resistance_return", "resistance_break")


def load(out_dir: str, date: str):
    snaps = sorted(d for d in os.listdir(out_dir) if d.isdigit())
    snap = snaps[-1]
    bars: dict[str, dict[str, dict]] = {}
    for fp in glob.glob(os.path.join(out_dir, snap, "*.json")):
        d = json.load(open(fp, encoding="utf-8"))
        bars[d["symbol"]] = {b["ts_start"]: b for b in d["bars"] if b["ts_start"][:10] == date}
    events = [json.loads(l) for l in open(os.path.join(out_dir, "events.jsonl"), encoding="utf-8")]
    events = [e for e in events if e.get("kind") == "zone" and e.get("evt") in ZONE_EVTS and e.get("bar_ts", "")[:10] == date]
    return snap, bars, events


def valid(b: dict) -> bool:
    return (not b["is_partial"] and not b["weakness"] and b.get("orderbook") is not None and b["orderbook"]["n"] >= N_THIN
            and b.get("price") is not None and REG_START <= b["ts_start"][11:16] <= REG_END)


def sign(x) -> int:
    x = int(x)
    return (x > 0) - (x < 0)


def bp_bucket(bp: int) -> str:
    a = abs(bp)
    return "0" if a == 0 else "1-20" if a <= 20 else "21-50" if a <= 50 else ">50"


def next_ts(ts: str, k: int) -> str:
    return (datetime.fromisoformat(ts) + timedelta(minutes=5 * k)).isoformat(timespec="seconds")


def pct(a: int, n: int) -> str:
    return f"{a}/{n}={100 * a / n:.0f}%" if n else "0/0"


def load_detail(out_dir: str, date: str) -> dict[str, dict[str, dict]]:
    """detail 모드 스냅샷(orderbook_bars 원본: total_*_delta·ask_bid_ratio). 없으면 빈 dict."""
    ddir = os.path.join(out_dir, "detail")
    if not os.path.isdir(ddir):
        return {}
    snaps = sorted(d for d in os.listdir(ddir) if d.isdigit())
    if not snaps:
        return {}
    out: dict[str, dict[str, dict]] = {}
    for fp in glob.glob(os.path.join(ddir, snaps[-1], "*.json")):
        d = json.load(open(fp, encoding="utf-8"))
        sym = os.path.basename(fp)[:-5]
        out[sym] = {b["ts_start"]: b for b in d.get("orderbook_bars") or [] if b["ts_start"][:10] == date}
    return out


def extra(bars, events, detail):
    def deltas(b):
        ob, tr = b["orderbook"], b["trade"]
        return (ob["bid_flow_qty"] or 0) - tr["total_sell_quantity"], (ob["ask_flow_qty"] or 0) - tr["total_buy_quantity"]

    v = [(sym, ts, b) for sym, sb in bars.items() for ts, b in sb.items() if valid(b) and b.get("trade")]
    print("\n[라벨 vs 같은 봉 return_bp 부호] (창 이동 산물이면 withdrawal 은 한쪽 부호에 몰린다)")
    for lab in ("bid_withdrawal", "ask_withdrawal", "bid_replenishment", "ask_replenishment"):
        c = Counter(sign(b["price"]["return_bp"]) for _, _, b in v if lab in b["phenomena"])
        print(f"  {lab:<18} n={sum(c.values()):3d}  ret>0 {c[1]:3d}  ret=0 {c[0]:3d}  ret<0 {c[-1]:3d}")

    print("\n[정체 봉(|ret|<=10) Δ총잔량 부호 → 다음 봉 return_bp]  Δbid=bid_flow-매도체결, Δask=ask_flow-매수체결")
    grp = defaultdict(list)
    for sym, ts, b in v:
        if abs(b["price"]["return_bp"]) > 10:
            continue
        n1 = bars[sym].get(next_ts(ts, 1))
        if not (n1 and n1.get("price") and not n1["is_partial"]):
            continue
        db, da = deltas(b)
        grp[("Δbid", sign(db))].append(n1["price"]["return_bp"])
        grp[("Δask", sign(da))].append(n1["price"]["return_bp"])
        grp[("book", "bid↑ask↓" if db > 0 and da < 0 else "bid↓ask↑" if db < 0 and da > 0 else "mixed")].append(n1["price"]["return_bp"])
        l1 = float(b["orderbook"]["l1_imbalance"] or 0.5)
        grp[("l1", ">0.55" if l1 > 0.55 else "<0.45" if l1 < 0.45 else "mid")].append(n1["price"]["return_bp"])
        dtl = detail.get(sym, {}).get(ts)
        if dtl and dtl.get("ask_bid_ratio"):
            r = float(dtl["ask_bid_ratio"])
            grp[("ask/bid", ">1.5" if r > 1.5 else "<0.67" if r < 0.67 else "mid")].append(n1["price"]["return_bp"])
    for k in sorted(grp, key=lambda k: (k[0], str(k[1]))):
        xs = grp[k]
        up, dn = sum(1 for x in xs if x > 0), sum(1 for x in xs if x < 0)
        print(f"  {k[0]:<8} {str(k[1]):<9} n={len(xs):3d} mean={statistics.mean(xs):+6.1f}bp 상승 {up:3d} 하락 {dn:3d}  상승비 {100*up/(up+dn) if up+dn else 0:.0f}%")

    print("\n[지지 이벤트 봉: Δbid 부호 / l1 / ask_bid_ratio 별 1·3봉 유지율]")
    def hold(sym, ts, lvl, k):
        nb = bars[sym].get(next_ts(ts, k))
        return None if not (nb and nb.get("price") and not nb["is_partial"]) else float(nb["price"]["close"]) >= lvl
    rows = []
    for e in events:
        if not e["evt"].startswith("support"):
            continue
        b = bars.get(e["sym"], {}).get(e["bar_ts"])
        if not (b and valid(b) and b.get("trade")):
            continue
        db, _ = deltas(b)
        dtl = detail.get(e["sym"], {}).get(e["bar_ts"])
        rows.append((e, db, float(b["orderbook"]["l1_imbalance"] or 0.5), float(dtl["ask_bid_ratio"]) if dtl and dtl.get("ask_bid_ratio") else None,
                     hold(e["sym"], e["bar_ts"], float(e["lvl_px"]), 1), hold(e["sym"], e["bar_ts"], float(e["lvl_px"]), 3)))
    def line(name, sub):
        h1 = [r[4] for r in sub if r[4] is not None]; h3 = [r[5] for r in sub if r[5] is not None]
        print(f"  {name:<28} n={len(sub):2d}  1봉 {pct(sum(h1), len(h1)):>12}  3봉 {pct(sum(h3), len(h3)):>12}")
    for evt in ("support_enter", "support_return", "support_break"):
        sub = [r for r in rows if r[0]["evt"] == evt]
        print(f"  · {evt} (n={len(sub)})")
        line("Δbid>0 (벽 커짐)", [r for r in sub if r[1] > 0]); line("Δbid<0 (벽 줄어듦)", [r for r in sub if r[1] < 0])
        line("l1>=0.5", [r for r in sub if r[2] >= 0.5]); line("l1<0.5", [r for r in sub if r[2] < 0.5])
        line("ask/bid<1 (매수벽 두꺼움)", [r for r in sub if r[3] is not None and r[3] < 1]); line("ask/bid>=1", [r for r in sub if r[3] is not None and r[3] >= 1])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--date", required=True)
    a = ap.parse_args(argv)
    snap, bars, events = load(a.out, a.date)
    detail = load_detail(a.out, a.date)
    print(f"snapshot={snap} symbols={len(bars)} zone_events={len(events)} ({Counter(e['evt'] for e in events)})")

    # --- 봉 품질 ---
    all_reg = [b for sb in bars.values() for b in sb.values() if not b["is_partial"] and REG_START <= b["ts_start"][11:16] <= REG_END]
    v = [b for b in all_reg if valid(b)]
    print(f"\n[품질] 정규장 확정봉 {len(all_reg)} / 봉단위 유효 {len(v)} / 응답단위 confidence {Counter(b['confidence'] for b in all_reg)}")
    ns = sorted(b["orderbook"]["n"] for b in v)
    print(f"[§7-3 n] min={ns[0]} p10={ns[len(ns)//10]} median={statistics.median(ns)} p90={ns[9*len(ns)//10]} max={ns[-1]} thin(<{N_THIN})={sum(1 for x in ns if x < N_THIN)}")
    sp = sorted(float(b["orderbook"]["avg_spread"]) / float(b["price"]["close"]) * 1e4 for b in v if b["orderbook"]["avg_spread"])
    print(f"[avg_spread bp] median={statistics.median(sp):.1f} p90={sp[9*len(sp)//10]:.1f}")
    print(f"[phenomena] {Counter(p for b in v for p in b['phenomena'])}")

    # --- §4.1 ---
    print("\n[§4.1 같은 봉 net_ofi 부호 vs return_bp 부호 일치율, |return_bp| 층별]")
    agree = defaultdict(lambda: [0, 0])
    for b in v:
        so, sr = sign(b["orderbook"]["net_ofi_qty"] or 0), sign(b["price"]["return_bp"])
        if so and sr:
            k = bp_bucket(b["price"]["return_bp"])
            agree[k][1] += 1
            agree[k][0] += so == sr
    tot = [sum(x[0] for x in agree.values()), sum(x[1] for x in agree.values())]
    for k in ("1-20", "21-50", ">50"):
        print(f"  |bp| {k:>5}: 일치 {pct(*agree[k])}")
    print(f"  전체: {pct(*tot)}   (애프터마켓 09-15: 15/61=24.6%)")
    # exhaustion_delta vs return sign (동어반복 확인)
    ex = [0, 0]
    for b in v:
        se, sr = sign(b["orderbook"]["exhaustion_delta"]), sign(b["price"]["return_bp"])
        if se and sr:
            ex[1] += 1; ex[0] += se == sr
    print(f"  exhaustion_delta 부호 vs return 부호 일치: {pct(*ex)}")

    # --- 정체 봉 → 다음 봉 ---
    print("\n[부가] 가격 정체 봉(|return_bp|<=10) net_ofi 부호 → 다음 봉 return_bp")
    nxt = defaultdict(list)
    for sym, sb in bars.items():
        for ts, b in sb.items():
            if not valid(b) or abs(b["price"]["return_bp"]) > 10:
                continue
            n1 = sb.get(next_ts(ts, 1))
            if n1 and n1.get("price") and not n1["is_partial"]:
                nxt[sign(b["orderbook"]["net_ofi_qty"] or 0)].append(n1["price"]["return_bp"])
    for s, label in ((1, "net_ofi>0"), (-1, "net_ofi<0"), (0, "net_ofi=0")):
        xs = nxt[s]
        if xs:
            up = sum(1 for x in xs if x > 0); dn = sum(1 for x in xs if x < 0)
            print(f"  {label}: n={len(xs)} 다음봉 mean={statistics.mean(xs):+.1f}bp median={statistics.median(xs):+.0f}bp 상승 {up} 하락 {dn}")

    # --- §7-2 ---
    print("\n[§7-2 존 이벤트 봉: flow 부호별 다음 1·3봉 레벨 유지율]")
    rows = []
    for e in events:
        sb = bars.get(e["sym"], {})
        b = sb.get(e["bar_ts"])
        if not b or not valid(b):
            rows.append((e, None, None, None)); continue
        lvl = float(e["lvl_px"])
        is_sup = e["evt"].startswith("support")
        flow = b["orderbook"]["bid_flow_qty"] if is_sup else b["orderbook"]["ask_flow_qty"]
        holds = []
        for k in (1, 3):
            nb = sb.get(next_ts(e["bar_ts"], k))
            if nb and nb.get("price") and not nb["is_partial"]:
                c = float(nb["price"]["close"])
                holds.append(c >= lvl if is_sup else c > lvl)
            else:
                holds.append(None)
        rows.append((e, b, flow, holds))
    usable = [r for r in rows if r[1] is not None]
    print(f"  이벤트 {len(rows)}, 봉 유효 {len(usable)}, 봉 없음/무효 {len(rows) - len(usable)}")

    def table(title, subset, key):
        print(f"\n  {title}")
        for grp_name, grp in (("flow>0 (적층/흡수)", [r for r in subset if key(r) > 0]), ("flow<0 (철수)", [r for r in subset if key(r) < 0])):
            h1 = [r[3][0] for r in grp if r[3][0] is not None]; h3 = [r[3][1] for r in grp if r[3][1] is not None]
            print(f"    {grp_name:<18} n={len(grp):2d}  1봉 유지 {pct(sum(h1), len(h1)):>12}  3봉 유지 {pct(sum(h3), len(h3)):>12}")

    for fam, label in (("support", "지지 이벤트(bid_flow 부호, 유지=close>=lvl)"), ("resistance", "저항 이벤트(ask_flow 부호, 유지=close>lvl 돌파)")):
        sub = [r for r in usable if r[0]["evt"].startswith(fam)]
        table(f"{label} — 전체 n={len(sub)}", sub, lambda r: r[2] or 0)
        clean = [r for r in sub if abs(r[1]["price"]["return_bp"]) <= 20]
        table(f"{label} — |return_bp|<=20 정체봉만 n={len(clean)}", clean, lambda r: r[2] or 0)
        for evt in ZONE_EVTS:
            if not evt.startswith(fam):
                continue
            s2 = [r for r in sub if r[0]["evt"] == evt]
            if s2:
                table(f"  · {evt} n={len(s2)}", s2, lambda r: r[2] or 0)

    extra(bars, events, detail)

    print("\n[이벤트 봉 상세]")
    print("  evt sym bar_ts lvl px ret_bp | bid_flow ask_flow l1 exh phenomena | hold1 hold3")
    for e, b, flow, holds in sorted(rows, key=lambda r: (r[0]["bar_ts"], r[0]["sym"])):
        if b is None:
            print(f"  {e['evt']:<18} {e['sym']} {e['bar_ts'][11:16]} {e['lvl_px']:>7} {e['px']:>7}   -- 봉 무효/없음"); continue
        ob, pr = b["orderbook"], b["price"]
        print(f"  {e['evt']:<18} {e['sym']} {e['bar_ts'][11:16]} {e['lvl_px']:>7} {e['px']:>7} {pr['return_bp']:+5d} | {ob['bid_flow_qty']:>8} {ob['ask_flow_qty']:>8} {float(ob['l1_imbalance'] or 0):.2f} {ob['exhaustion_delta']:+3d} {','.join(p for p in b['phenomena'] if p not in ('neutral',))} | {holds[0]} {holds[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
