#!/usr/bin/env python3
"""동기화 패널 — 큰손·투자자·업종·프로그램·KOSPI 를 5분봉 한 줄로 맞추고 조건부로 센다. 판단·추천 0.

한 줄 = (종목, 5분봉 ts_start). 축과 결합 규칙:
| 축 | 원천 | 주기 | 결합 |
|---|---|---|---|
| 가격 | rrr /detail candles | 봉 | ts_start 일치 |
| 큰손/중간/개미 순매수 | rrr /detail trade_buckets(체결 1건 금액 구간) | 봉 | ts_start 일치 |
| 종목 외국인·기관 당일누적 | rrr /detail investor_flow(ka10059) | 90초 | as_of ≤ 봉 종료 시각인 마지막 샘플 (계단형·잠정치) |
| KOSPI 지수·breadth | rrr /market-context slots (0J/0U) | 봉 | slot_start 일치 |
| 시장 프로그램 당일누적 | ka90005 1분 버킷(market_sampler) | 1분 | cntr_tm ≤ 봉 종료 시각인 마지막 행 |
| 업종·시장 투자자 당일누적 | ka10051 스냅샷(market_sampler), 종목→업종 = ka10099 up_name | 5분 | sampled_at ≤ 봉 종료 시각인 마지막 스냅샷 |
결과는 지수 대비 초과수익률(종목 − KOSPI)로 읽고, 신호는 사건 단위(같은 종목 연속 신호봉 = 1건, 첫 봉 기준)로 센다.
KOSPI 종목만 쓴다(sector_map_kospi16.json 에 있는 종목).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta

REG_START, REG_END = "09:00", "15:20"
WHALE_LOWER, ANT_UPPER = 100_000_000, 10_000_000
HORIZONS = (1, 3, 6, 12)


def num(x) -> float:
    s = str(x).replace(",", "").strip()
    if not s:
        return 0.0
    neg = s.startswith("-")
    return -float(s.lstrip("+-") or 0) if neg else float(s.lstrip("+-") or 0)


def next_ts(ts: str, k: int) -> str:
    return (datetime.fromisoformat(ts) + timedelta(minutes=5 * k)).isoformat(timespec="seconds")


def bar_end(ts: str) -> str:
    return next_ts(ts, 1)


def asof(series: list[tuple[str, dict]], t: str):
    """series 는 (시각, 값) 오름차순. 시각 ≤ t 인 마지막 값."""
    best = None
    for k, v in series:
        if k <= t:
            best = v
        else:
            break
    return best


def tier_group(t: dict) -> str:
    up = t["upper_amount_won"]
    return "whale" if (up is None or up > WHALE_LOWER) else "ant" if up <= ANT_UPPER else "mid"


def build_panel(out_dir: str, date: str) -> list[dict]:
    rs_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    meta_path = os.path.join(rs_root, "config", "stock_meta_18.json")
    if not os.path.exists(meta_path):
        meta_path = os.path.join(os.path.dirname(out_dir.rstrip("/")), "stock_meta_18.json")
    if not os.path.exists(meta_path):
        meta_path = os.path.join(os.path.dirname(out_dir.rstrip("/")), "sector_map_kospi16.json")
    smap = json.load(open(meta_path, encoding="utf-8"))
    ddir = os.path.join(out_dir, "detail")
    snap = sorted(d for d in os.listdir(ddir) if d.isdigit())[-1]
    # KOSPI 슬롯
    kospi: dict[str, dict] = {}
    gfp = os.path.join(out_dir, "global_market_context.json")
    if os.path.exists(gfp):
        for slot in json.load(open(gfp, encoding="utf-8")).get("slots") or []:
            if slot["slot_start"][:10] != date or slot.get("is_partial"):
                continue
            node = (slot.get("markets") or {}).get("kospi") or {}
            idx, br, pg = node.get("index") or {}, node.get("breadth") or {}, node.get("program") or {}
            if idx.get("close") is None:
                continue
            kospi[slot["slot_start"]] = {"close": float(idx["close"]), "bp": idx.get("return_bp"), "rising": br.get("rising_count"),
                                         "falling": br.get("falling_count"), "prog_net_eok": num(pg["net_eok"]) if pg.get("net_eok") is not None else None,
                                         "prog_d5_eok": num(pg["delta_5m_eok"]) if pg.get("delta_5m_eok") is not None else None}
    # ka90005 병합(1분 누적, 백만원) → (HH:MM:SS, 억원)
    prog: list[tuple[str, dict]] = []
    mfp = os.path.join(out_dir, "market", "ka90005_merged.json")
    if os.path.exists(mfp):
        m = json.load(open(mfp, encoding="utf-8"))
        for key in sorted(m):
            k = key.zfill(6)
            prog.append((f"{date}T{k[:2]}:{k[2:4]}:{k[4:6]}", {"all_net_eok": num(m[key]["all_netprps"]) / 100, "dfrt_net_eok": num(m[key]["dfrt_trde_netprps"]) / 100,
                                                            "ndiff_net_eok": num(m[key]["ndiffpro_trde_netprps"]) / 100}))
    # ka10051 스냅샷 → (sampled_at, {inds_nm: row})
    sect: list[tuple[str, dict]] = []
    for fp in sorted(glob.glob(os.path.join(out_dir, "market", "*", "ka10051.json"))):
        d = json.load(open(fp, encoding="utf-8"))
        sect.append((d["sampled_at"], {r["inds_nm"]: r for r in d["rows"]}))
    panel: list[dict] = []
    for sym, meta in sorted(smap.items()):
        fp = os.path.join(ddir, snap, f"{sym}.json")
        if not os.path.exists(fp):
            continue
        d = json.load(open(fp, encoding="utf-8"))
        closes = {c["ts_start"]: float(c["close"]) for c in d.get("candles") or [] if not c["is_partial"] and c["ts_start"][:10] == date}
        inv = sorted(((s["as_of"], s) for s in d.get("investor_flow") or [] if s.get("as_of", "")[:10] == date), key=lambda x: x[0])
        inv_series = [(a, {"as_of": a, "frgn": num(s["frgnr_buy_eok"]) + num(s["frgnr_sell_eok"]), "orgn": num(s["orgn_buy_eok"]) + num(s["orgn_sell_eok"])}) for a, s in inv]
        f_change_series = []
        last_f = last_f_time = None
        for a, s in inv:
            f_val = (num(s["frgnr_buy_eok"]), num(s["frgnr_sell_eok"]))
            if f_val != last_f:
                last_f_time = a
                last_f = f_val
            f_change_series.append((a, last_f_time))
        candles_all = [c for c in (d.get("candles") or []) if c.get("ts_start", "")[:10] == date and not c.get("is_partial")]
        c_krx = closes.get(f"{date}T15:30:00") or closes.get(f"{date}T15:20:00")
        nxt_c = [c for c in candles_all if c.get("ts_start", "")[11:16] > "15:30"]
        c_nxt = float(nxt_c[-1]["close"]) if nxt_c else c_krx
        k_krx = (kospi.get(f"{date}T15:20:00") or kospi.get(f"{date}T15:30:00") or (sorted(kospi.values(), key=lambda x: x.get("close", 0))[-1] if kospi else {})).get("close")
        tb = {t["ts_start"]: t for t in d.get("trade_buckets") or [] if not t["is_partial"] and t["ts_start"][:10] == date}
        kclose = {t: v["close"] for t, v in kospi.items()}
        ts_list = sorted(t for t in tb if REG_START <= t[11:16] <= REG_END)
        cum_net = cum_tot = 0.0
        first = ts_list[0] if ts_list else None
        w_hist = []
        for ts in ts_list:
            g = defaultdict(lambda: {"buy": 0.0, "sell": 0.0})
            for t in tb[ts]["tiers"]:
                k = tier_group(t)
                g[k]["buy"] += num(t["buy_amount"]); g[k]["sell"] += num(t["sell_amount"])
            total = sum(v["buy"] + v["sell"] for v in g.values())
            net = {k: g[k]["buy"] - g[k]["sell"] for k in ("whale", "mid", "ant")}
            cum_net += net["whale"]; cum_tot += total
            row = {"sym": sym, "ts": ts, "name": meta.get("name", sym), "sector": meta.get("up_name", "기타"),
                   "size_tier": meta.get("size_tier", "대형주"), "market_cap_eok": meta.get("market_cap_eok"),
                   "whale_net": net["whale"], "mid_net": net["mid"], "ant_net": net["ant"], "bar_amt": total,
                   "whale_ratio": net["whale"] / total if total else 0.0, "mid_ratio": net["mid"] / total if total else 0.0,
                   "ant_ratio": net["ant"] / total if total else 0.0,
                   "whale_cum_ratio": cum_net / cum_tot if cum_tot else None}
            # T2: 종목별 롤링 24봉(최소 12) 큰손 순매수 z-score
            if len(w_hist) >= 12:
                mu, sd = statistics.mean(w_hist), statistics.pstdev(w_hist)
                whale_z = (net["whale"] - mu) / sd if sd > 0 else 0.0
            else:
                whale_z = None
            w_hist.append(net["whale"])
            if len(w_hist) > 24:
                w_hist.pop(0)
            row["whale_z"] = whale_z
            # 가격·지수
            prev, cur = closes.get(next_ts(ts, -1)), closes.get(ts)
            row["same_bp"] = (cur / prev - 1) * 1e4 if prev and cur else None
            ks = kospi.get(ts)
            row["idx_bp"] = ks["bp"] if ks else None
            row["breadth_down"] = (ks["falling"] > ks["rising"]) if ks and ks.get("rising") is not None else None
            row["same_ex"] = row["same_bp"] - ks["bp"] if (row["same_bp"] is not None and ks and ks["bp"] is not None) else None
            c0 = closes.get(next_ts(first, -1)) or closes.get(first); i0 = (kospi.get(next_ts(first, -1)) or kospi.get(first) or {}).get("close"); i1 = (ks or {}).get("close")
            row["cum_ex"] = ((cur / c0 - 1) - (i1 / i0 - 1)) * 1e4 if c0 and cur and i0 and i1 else None
            for k in HORIZONS:
                ck, ik = closes.get(next_ts(ts, k)), kclose.get(next_ts(ts, k))
                row[f"fwd{k}"] = (ck / cur - 1) * 1e4 if cur and ck else None
                row[f"fwdex{k}"] = (row[f"fwd{k}"] - (ik / i1 - 1) * 1e4) if (row[f"fwd{k}"] is not None and i1 and ik) else None
            # KRX 정규장 종가 및 NXT 야간 종가 초과수익률
            row["fwd_krx"] = (c_krx / cur - 1) * 1e4 if cur and c_krx else None
            row["fwdex_krx"] = (row["fwd_krx"] - (k_krx / i1 - 1) * 1e4) if (row["fwd_krx"] is not None and i1 and k_krx) else None
            row["fwd_nxt"] = (c_nxt / cur - 1) * 1e4 if cur and c_nxt else None
            row["fwdex_nxt"] = (row["fwd_nxt"] - (k_krx / i1 - 1) * 1e4) if (row["fwd_nxt"] is not None and i1 and k_krx) else None
            row["nxt_drift_bp"] = (c_nxt / c_krx - 1) * 1e4 if (c_krx and c_nxt) else None
            # 종목 투자자(as-of 봉 종료, 5분 증분)
            end = bar_end(ts)
            cur_inv, prev_inv = asof(inv_series, end), asof(inv_series, ts)
            row["frgn_cum"] = cur_inv["frgn"] if cur_inv else None
            row["orgn_cum"] = cur_inv["orgn"] if cur_inv else None
            row["frgn_d5"] = (cur_inv["frgn"] - prev_inv["frgn"]) if cur_inv and prev_inv else None
            row["orgn_d5"] = (cur_inv["orgn"] - prev_inv["orgn"]) if cur_inv and prev_inv else None
            # T4: 투자자 신선도 필드 (쓰인 샘플 as_of 및 봉 종료 - as_of 분)
            dt_end = datetime.fromisoformat(end)
            row["frgn_asof"] = cur_inv["as_of"] if cur_inv else None
            row["orgn_asof"] = cur_inv["as_of"] if cur_inv else None
            row["frgn_stale_min"] = round((dt_end - datetime.fromisoformat(cur_inv["as_of"])).total_seconds() / 60.0, 1) if cur_inv else None
            row["orgn_stale_min"] = round((dt_end - datetime.fromisoformat(cur_inv["as_of"])).total_seconds() / 60.0, 1) if cur_inv else None
            last_ch = asof(f_change_series, end)
            row["frgn_change_asof"] = last_ch if last_ch else None
            row["frgn_change_stale_min"] = round((dt_end - datetime.fromisoformat(last_ch)).total_seconds() / 60.0, 1) if last_ch else None
            # 시장 프로그램(ka90005 as-of, 5분 증분) + rrr 슬롯(ka90007) 대조
            pc, pp = asof(prog, end), asof(prog, ts)
            row["mprog_cum"] = pc["all_net_eok"] if pc else None
            row["mprog_d5"] = (pc["all_net_eok"] - pp["all_net_eok"]) if pc and pp else None
            row["mprog_slot_d5"] = ks["prog_d5_eok"] if ks else None
            # 업종·시장 투자자(ka10051 as-of)
            sc, sp = asof(sect, end), asof(sect, ts)
            srow = sc.get(meta["up_name"]) if sc else None
            mrow = sc.get("종합(KOSPI)") if sc else None
            row["sect_frgn_cum"] = num(srow["frgnr_netprps"]) if srow else None
            row["sect_orgn_cum"] = num(srow["orgn_netprps"]) if srow else None
            row["sect_ind_cum"] = num(srow["ind_netprps"]) if srow else None
            row["mkt_frgn_cum"] = num(mrow["frgnr_netprps"]) if mrow else None
            row["mkt_orgn_cum"] = num(mrow["orgn_netprps"]) if mrow else None
            if sc and sp and sc is not sp and srow and sp.get(meta["up_name"]):
                row["sect_frgn_d5"] = num(srow["frgnr_netprps"]) - num(sp[meta["up_name"]]["frgnr_netprps"])
            else:
                row["sect_frgn_d5"] = None
            panel.append(row)
    return panel


def sgn(x) -> int:
    return 0 if x is None else (x > 0) - (x < 0)


def episodes(panel: list[dict], sel) -> list[dict]:
    """같은 종목의 연속 신호봉을 하나로 — 첫 봉만 남긴다. 날짜 경계를 넘지 않는다."""
    out, last = [], {}
    for r in sorted(panel, key=lambda r: (r["sym"], r["ts"])):
        s = sel(r)
        if not s:
            last[r["sym"]] = (None, None); continue
        pts, ps = last.get(r["sym"], (None, None))
        if pts is not None and pts[:10] == r["ts"][:10] and next_ts(pts, 1) == r["ts"] and ps == s:
            last[r["sym"]] = (r["ts"], s); continue
        last[r["sym"]] = (r["ts"], s)
        out.append(dict(r, _dir=s))
    return out


def report(title: str, panel: list[dict], sel, excess: bool = True) -> dict:
    eps = episodes(panel, sel)
    cells = []
    stats = {"title": title, "events": len(eps), "symbols": len(Counter(e["sym"] for e in eps)), "horizons": {}}
    for k in HORIZONS:
        key = f"fwdex{k}" if excess else f"fwd{k}"
        xs = [e[key] * e["_dir"] for e in eps if e.get(key) is not None]
        same = sum(1 for x in xs if x > 0); tot = sum(1 for x in xs if x != 0)
        pct = 100 * same / tot if tot else 0.0
        mean_bp = statistics.mean(xs) if xs else 0.0
        cells.append(f"k={k}: {mean_bp:+6.1f}bp {same}/{tot}={pct:3.0f}%")
        stats["horizons"][k] = {"mean_bp": mean_bp, "same": same, "tot": tot, "pct": pct}
    k_key = "fwdex_krx" if excess else "fwd_krx"
    xs_k = [e[k_key] * e["_dir"] for e in eps if e.get(k_key) is not None]
    same_k = sum(1 for x in xs_k if x > 0); tot_k = sum(1 for x in xs_k if x != 0)
    pct_k = 100 * same_k / tot_k if tot_k else 0.0
    mean_k = statistics.mean(xs_k) if xs_k else 0.0
    cells.append(f"KRX: {mean_k:+6.1f}bp {same_k}/{tot_k}={pct_k:3.0f}%")
    stats["horizons"]["krx"] = {"mean_bp": mean_k, "same": same_k, "tot": tot_k, "pct": pct_k}

    n_key = "fwdex_nxt" if excess else "fwd_nxt"
    xs_n = [e[n_key] * e["_dir"] for e in eps if e.get(n_key) is not None]
    same_n = sum(1 for x in xs_n if x > 0); tot_n = sum(1 for x in xs_n if x != 0)
    pct_n = 100 * same_n / tot_n if tot_n else 0.0
    mean_n = statistics.mean(xs_n) if xs_n else 0.0
    cells.append(f"NXT: {mean_n:+6.1f}bp {same_n}/{tot_n}={pct_n:3.0f}%")
    stats["horizons"]["nxt"] = {"mean_bp": mean_n, "same": same_n, "tot": tot_n, "pct": pct_n}
    syms = Counter(e["sym"] for e in eps)
    print(f"  {title:<40} 사건 {len(eps):3d} (종목 {len(syms)}, 최다 {syms.most_common(1)[0][1] if syms else 0}) | " + " | ".join(cells))
    return stats


def quantile_match_thresholds(panel: list[dict], base_threshold: float = 0.4):
    """유효 봉 전체에서 큰손 |whale_ratio|>=base_threshold 가 차지하는 분위수 q 를 구하고,
    개미·중간의 |ratio| 분포에서 같은 q 에 해당하는 임계를 반환한다."""
    w_r = sorted([abs(r.get("whale_ratio") or 0.0) for r in panel])
    m_r = sorted([abs(r.get("mid_ratio") or 0.0) for r in panel])
    a_r = sorted([abs(r.get("ant_ratio") or 0.0) for r in panel])
    N = len(panel)
    if not N:
        return 0.0, base_threshold, 0.0, 0.0, 0, 0
    w_count = sum(1 for x in w_r if x >= base_threshold)
    q = 1.0 - w_count / N
    idx = max(0, min(N - 1, N - w_count))
    th_whale = base_threshold
    th_mid = m_r[idx]
    th_ant = a_r[idx]
    return q, th_whale, th_mid, th_ant, w_count, N


def agree(label: str, panel: list[dict], f1, f2) -> dict:
    n = same = 0
    for r in panel:
        a1, a2 = sgn(f1(r)), sgn(f2(r))
        if a1 and a2:
            n += 1; same += a1 == a2
    pct = 100 * same / n if n else 0.0
    print(f"  {label:<44} {same}/{n} = {pct:.0f}%")
    return {"label": label, "same": same, "n": n, "pct": pct}


def make_absorb(field: str = "whale_ratio", th: float = 0.4):
    def sel(r):
        v = r.get(field) or 0.0
        s = 1 if v >= th else -1 if v <= -th else 0
        return s if s and r.get("same_ex") is not None and r["same_ex"] * s <= 0 else 0
    return sel


def s_absorb(r: dict) -> int:
    return make_absorb("whale_ratio", 0.4)(r)


def s_hidden(r: dict) -> int:
    w = r.get("whale_cum_ratio")
    s = 0 if w is None else 1 if w >= 0.2 else -1 if w <= -0.2 else 0
    return s if s and r.get("cum_ex") is not None and sgn(r["cum_ex"]) * s == -1 else 0


def with_axis(base, axis, want: int):
    return lambda r: (base(r) if base(r) and sgn(axis(r)) == want * base(r) else 0) if want else (base(r) if base(r) and sgn(axis(r)) == 0 else 0)


def s_z(th: float = 1.5, reverse: bool = False):
    def sel(r):
        z = r.get("whale_z")
        if z is None:
            return 0
        s = 1 if z >= th else -1 if z <= -th else 0
        return -s if reverse else s
    return sel


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--date", required=True)
    a = ap.parse_args(argv)
    panel = build_panel(a.out, a.date)
    with open(os.path.join(a.out, f"panel_{a.date}.jsonl"), "w", encoding="utf-8") as f:
        for r in panel:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    syms = sorted({r["sym"] for r in panel})
    cov = {k: sum(1 for r in panel if r.get(k) is not None) for k in ("same_ex", "frgn_cum", "frgn_d5", "mprog_cum", "mprog_d5", "sect_frgn_cum", "sect_frgn_d5")}
    print(f"panel rows={len(panel)} symbols={len(syms)} {syms}\n축 커버리지(값 있는 봉 수): {cov}")

    print("\n[기준선] 초과수익률(EX) — 0·50% 근처여야 한다")
    report("전 봉 +1", panel, lambda r: 1)
    report("직전 봉 초과수익 부호 추종", panel, lambda r: sgn(r["same_ex"]))

    print("\n[A 큰손 vs 투자자 — 같은 것을 보고 있나] 부호 일치율")
    agree("큰손 봉 순매수 vs 외국인 5분 증분(증분≠0 봉)", panel, lambda r: r["whale_net"], lambda r: r["frgn_d5"])
    agree("큰손 봉 순매수 vs 기관 5분 증분", panel, lambda r: r["whale_net"], lambda r: r["orgn_d5"])
    agree("큰손 당일누적 vs 외국인 당일누적", panel, lambda r: r["whale_cum_ratio"], lambda r: r["frgn_cum"])
    agree("큰손 당일누적 vs 기관 당일누적", panel, lambda r: r["whale_cum_ratio"], lambda r: r["orgn_cum"])
    agree("큰손 봉 순매수 vs 시장 프로그램 5분 증분(ka90005)", panel, lambda r: r["whale_net"], lambda r: r["mprog_d5"])
    agree("큰손 당일누적 vs 업종 외국인 당일누적", panel, lambda r: r["whale_cum_ratio"], lambda r: r["sect_frgn_cum"])

    print("\n[① 흡수] 큰손 R>=0.4 & 같은 봉 초과수익<=0 — 사건 단위, EX")
    report("① 전체", panel, s_absorb)
    for name, ax in (("외국인 당일누적", lambda r: r["frgn_cum"]), ("기관 당일누적", lambda r: r["orgn_cum"]), ("외국인 5분증분", lambda r: r["frgn_d5"]),
                     ("시장 프로그램 5분증분", lambda r: r["mprog_d5"]), ("업종 외국인 누적", lambda r: r["sect_frgn_cum"]), ("시장 외국인 누적", lambda r: r["mkt_frgn_cum"])):
        report(f"  + {name} 같은 방향", panel, with_axis(s_absorb, ax, 1))
        report(f"  + {name} 반대 방향", panel, with_axis(s_absorb, ax, -1))

    print("\n[② 숨은 매집/분산] 큰손 당일누적 |비율|>=0.2 & 종목 누적 초과수익 반대 — 사건 단위, EX")
    report("② 전체", panel, s_hidden)
    for name, ax in (("외국인 당일누적", lambda r: r["frgn_cum"]), ("기관 당일누적", lambda r: r["orgn_cum"]), ("업종 외국인 누적", lambda r: r["sect_frgn_cum"]), ("시장 외국인 누적", lambda r: r["mkt_frgn_cum"])):
        report(f"  + {name} 같은 방향", panel, with_axis(s_hidden, ax, 1))
        report(f"  + {name} 반대 방향", panel, with_axis(s_hidden, ax, -1))

    print("\n[③ 투자자 단독] 외국인·기관 당일누적 부호 전환 봉(직전 봉과 부호 다름) → EX")
    def flip(key):
        prev = {}
        marks = {}
        for r in sorted(panel, key=lambda r: (r["sym"], r["ts"])):
            p = prev.get(r["sym"]); c = sgn(r[key])
            marks[(r["sym"], r["ts"])] = c if (p is not None and p != 0 and c != 0 and c != p) else 0
            prev[r["sym"]] = c if c else p
        return lambda r: marks.get((r["sym"], r["ts"]), 0)
    report("외국인 누적 부호 전환", panel, flip("frgn_cum"))
    report("기관 누적 부호 전환", panel, flip("orgn_cum"))
    report("외국인 5분증분 |Δ|>=봉거래대금 20%", panel, lambda r: (sgn(r["frgn_d5"]) if r["frgn_d5"] is not None and r["bar_amt"] and abs(r["frgn_d5"]) * 1e8 >= 0.2 * r["bar_amt"] else 0))

    print("\n[④ 시장 국면] 큰손 R>=0.4 을 breadth·프로그램 5분증분으로 층화, EX")
    base = lambda r: 1 if r["whale_ratio"] >= 0.4 else -1 if r["whale_ratio"] <= -0.4 else 0
    report("breadth 하락우위 봉", panel, lambda r: base(r) if r["breadth_down"] else 0)
    report("breadth 상승우위 봉", panel, lambda r: base(r) if r["breadth_down"] is False else 0)
    report("시장 프로그램 5분증분 같은 방향", panel, with_axis(base, lambda r: r["mprog_d5"], 1))
    report("시장 프로그램 5분증분 반대 방향", panel, with_axis(base, lambda r: r["mprog_d5"], -1))

    # T1: 흡수 대조군 (고정 0.2, 0.4 및 분위수 매칭)
    print("\n[⑤ 흡수 대조군 — 고정 및 분위수 매칭 (T1)]")
    def make_absorb(field, th):
        def sel(r):
            v = r.get(field) or 0.0
            s = 1 if v >= th else -1 if v <= -th else 0
            return s if s and r["same_ex"] is not None and r["same_ex"] * s <= 0 else 0
        return sel

    print("  -- (a) 고정 임계 0.4 --")
    report("큰손 R>=0.4 (H1 기준)", panel, make_absorb("whale_ratio", 0.4))
    report("중간 R>=0.4 (대조군)", panel, make_absorb("mid_ratio", 0.4))
    report("개미 R>=0.4 (대조군)", panel, make_absorb("ant_ratio", 0.4))

    print("  -- (a) 고정 임계 0.2 --")
    report("큰손 R>=0.2", panel, make_absorb("whale_ratio", 0.2))
    report("중간 R>=0.2 (대조군)", panel, make_absorb("mid_ratio", 0.2))
    report("개미 R>=0.2 (대조군)", panel, make_absorb("ant_ratio", 0.2))

    q, th_w, th_m, th_a, w_cnt, tot_cnt = quantile_match_thresholds(panel, 0.4)
    print(f"  -- (b) 분위수 매칭 (q={q:.4f}, 큰손 R>=0.4 비율 {w_cnt}/{tot_cnt}={100*w_cnt/tot_cnt:.1f}%) --")
    print(f"  [분위수 임계값] 큰손={th_w:.4f}, 중간={th_m:.4f}, 개미={th_a:.4f}")
    report(f"큰손 R>={th_w:.4f} (분위수)", panel, make_absorb("whale_ratio", th_w))
    report(f"중간 R>={th_m:.4f} (분위수)", panel, make_absorb("mid_ratio", th_m))
    report(f"개미 R>={th_a:.4f} (분위수)", panel, make_absorb("ant_ratio", th_a))

    # T2: H5 패널 기준 롤링 z-score
    print("\n[⑥ H5 이례도 z-score 신호 (T2)]")
    def s_z(th, reverse=False):
        def sel(r):
            z = r.get("whale_z")
            if z is None:
                return 0
            s = 1 if z >= th else -1 if z <= -th else 0
            return -s if reverse else s
        return sel
    report("H5 |whale_z|>=1.5 순방향 추종", panel, s_z(1.5, False))
    report("H5 |whale_z|>=1.5 되돌림(반대)", panel, s_z(1.5, True))
    report("H5 |whale_z|>=2.0 순방향 추종", panel, s_z(2.0, False))
    report("H5 |whale_z|>=2.0 되돌림(반대)", panel, s_z(2.0, True))

    # T4: 투자자 신선도 층화
    print("\n[⑦ 투자자 신선도별 흡수 ① 층화 (T4)]")
    report("① frgn_stale_min <= 30 (샘플 as_of)", panel, lambda r: s_absorb(r) if r.get("frgn_stale_min") is not None and r["frgn_stale_min"] <= 30 else 0)
    report("① frgn_stale_min > 30 (샘플 as_of)", panel, lambda r: s_absorb(r) if r.get("frgn_stale_min") is not None and r["frgn_stale_min"] > 30 else 0)
    report("① frgn_change_stale <= 30 (수치변동 as_of)", panel, lambda r: s_absorb(r) if r.get("frgn_change_stale_min") is not None and r["frgn_change_stale_min"] <= 30 else 0)
    report("① frgn_change_stale > 30 (수치변동 as_of)", panel, lambda r: s_absorb(r) if r.get("frgn_change_stale_min") is not None and r["frgn_change_stale_min"] > 30 else 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
