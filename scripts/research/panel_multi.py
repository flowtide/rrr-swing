#!/usr/bin/env python3
"""다일 동기화 패널 집계 — 날짜별 panel_<date>.jsonl 을 읽어 합치고 가설을 채점한다.

panel_analyze.py 의 함수들을 재사용하며 사건 병합은 날짜 경계를 넘지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

# panel_analyze.py 의 함수 재사용 (복붙 금지)
from panel_analyze import (
    HORIZONS,
    agree,
    episodes,
    make_absorb,
    quantile_match_thresholds,
    report,
    s_absorb,
    s_hidden,
    s_z,
    sgn,
    with_axis,
)

# docs/09 §3 사전등록 가설 통과 조건 상수
H1_MIN_EVENTS = 30
H1_MIN_ACC = 60.0
ANT_MAX_ACC = 55.0

H4_MIN_EVENTS = 20
H4_MIN_ACC = 60.0

H5_MIN_EVENTS = 30
H5_MIN_ACC = 60.0


def resolve_dates(dates_arg: str | None, from_arg: str | None, to_arg: str | None) -> list[str]:
    if dates_arg:
        return [d.strip() for d in dates_arg.split(",") if d.strip()]
    if from_arg and to_arg:
        d0 = datetime.fromisoformat(from_arg).date()
        d1 = datetime.fromisoformat(to_arg).date()
        out = []
        cur = d0
        while cur <= d1:
            out.append(cur.isoformat())
            cur += timedelta(days=1)
        return out
    if from_arg:
        return [from_arg.strip()]
    return []


def load_multi_panel(root: str, dates: list[str]) -> tuple[list[dict], list[str]]:
    panel: list[dict] = []
    loaded_dates: list[str] = []
    for d in dates:
        fp = os.path.join(root, d, f"panel_{d}.jsonl")
        if not os.path.exists(fp):
            print(f"[WARN] {fp} 파일이 존재하지 않아 건너뜁니다: {d}", file=sys.stderr)
            continue
        count = 0
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    panel.append(json.loads(line))
                    count += 1
        loaded_dates.append(d)
    return panel, loaded_dates


import statistics


def compute_stats(panel: list[dict], sel, excess: bool = True) -> dict:
    eps = episodes(panel, sel)
    stats = {"events": len(eps), "horizons": {}}
    for k in HORIZONS:
        xs = []
        for e in eps:
            v = e.get(f"fwdex{k}") if (excess and e.get(f"fwdex{k}") is not None) else e.get(f"fwd{k}")
            if v is not None:
                xs.append(v * e["_dir"])
        same = sum(1 for x in xs if x > 0)
        tot = sum(1 for x in xs if x != 0)
        pct = 100 * same / tot if tot else 0.0
        mean_bp = statistics.mean(xs) if xs else 0.0
        stats["horizons"][k] = {"mean_bp": mean_bp, "same": same, "tot": tot, "pct": pct}

    for h_name in ("krx", "nxt"):
        xs = []
        for e in eps:
            v = e.get(f"fwdex_{h_name}") if (excess and e.get(f"fwdex_{h_name}") is not None) else e.get(f"fwd_{h_name}")
            if v is not None:
                xs.append(v * e["_dir"])
        same = sum(1 for x in xs if x > 0)
        tot = sum(1 for x in xs if x != 0)
        pct = 100 * same / tot if tot else 0.0
        mean_bp = statistics.mean(xs) if xs else 0.0
        stats["horizons"][h_name] = {"mean_bp": mean_bp, "same": same, "tot": tot, "pct": pct}

    return stats


def print_scorecard(panel: list[dict], ant_th: float, q: float) -> None:
    # H1
    h1 = compute_stats(panel, s_absorb)
    # 개미 대조군 (분위수 매칭)
    ant = compute_stats(panel, make_absorb("ant_ratio", ant_th))
    # H2
    h2 = compute_stats(panel, with_axis(s_absorb, lambda r: r.get("frgn_cum"), -1))
    # H3
    h3 = compute_stats(panel, with_axis(s_absorb, lambda r: r.get("orgn_cum"), 1))
    # H4
    h4 = compute_stats(panel, s_hidden)
    # H5
    h5 = compute_stats(panel, s_z(1.5, True))

    # 채점 판정
    h1_n = h1["events"]
    h1_k6 = h1["horizons"][6]["pct"]
    h1_k12 = h1["horizons"][12]["pct"]
    ant_k6 = ant["horizons"][6]["pct"]

    # H1: 사건 >= 30, 일치 >= 60%, 개미 대조군 < 55%
    if h1_n >= H1_MIN_EVENTS and h1_k6 >= H1_MIN_ACC and ant_k6 < ANT_MAX_ACC:
        h1_eval = "통과"
        h1_passed = True
    elif h1_n < H1_MIN_EVENTS:
        h1_eval = "보류"
        h1_passed = False
    else:
        h1_eval = "미달"
        h1_passed = False

    # H2: H1 통과 후, 부분집합 일치가 H1 전체보다 높음
    h2_n = h2["events"]
    h2_k6 = h2["horizons"][6]["pct"]
    if h1_passed and h2_k6 > h1_k6 and h2_n >= 20:
        h2_eval = "통과"
    elif not h1_passed or h2_n < 20:
        h2_eval = "보류"
    else:
        h2_eval = "미달"

    # H3: H1 통과 후, 부분집합 일치가 H1 전체보다 높음
    h3_n = h3["events"]
    h3_k6 = h3["horizons"][6]["pct"]
    if h1_passed and h3_k6 > h1_k6 and h3_n >= 20:
        h3_eval = "통과"
    elif not h1_passed or h3_n < 20:
        h3_eval = "보류"
    else:
        h3_eval = "미달"

    # H4: 사건 >= 20, 일치 >= 60%
    h4_n = h4["events"]
    h4_k6 = h4["horizons"][6]["pct"]
    h4_k12 = h4["horizons"][12]["pct"]
    if h4_n >= H4_MIN_EVENTS and h4_k6 >= H4_MIN_ACC and h4_k12 >= H4_MIN_ACC:
        h4_eval = "통과"
    elif h4_n < H4_MIN_EVENTS:
        h4_eval = "보류"
    else:
        h4_eval = "미달"

    # H5: 사건 >= 30, 반대 일치 >= 60%
    h5_n = h5["events"]
    h5_k6 = h5["horizons"][6]["pct"]
    h5_k12 = h5["horizons"][12]["pct"]
    if h5_n >= H5_MIN_EVENTS and (h5_k6 >= H5_MIN_ACC or h5_k12 >= H5_MIN_ACC):
        h5_eval = "통과"
    elif h5_n < H5_MIN_EVENTS:
        h5_eval = "보류"
    else:
        h5_eval = "미달"

    def fmt_cell(h_dict, key):
        c = h_dict["horizons"].get(key, {})
        pct = c.get("pct", 0.0)
        bp = c.get("mean_bp", 0.0)
        return f"{pct:4.0f}% ({bp:+5.1f}bp)"

    print("\n" + "=" * 128)
    print(" [docs/09 §3 사전등록 가설 채점표 — 6·12봉 및 KRX/NXT 종가 종합]")
    print("-" * 128)
    print(f" {'가설':<28} | {'사건 수':<6} | {'6봉 일치율(bp)':<16} | {'12봉 일치율(bp)':<16} | {'KRX 종가(bp)':<16} | {'NXT 종가(bp)':<16} | {'개미 대조군(6봉/KRX)':<20} | {'판정':<8}")
    print("-" * 128)
    ant_summary = f"{ant['horizons'][6]['pct']:.0f}% / {ant['horizons']['krx']['pct']:.0f}% (R>={ant_th:.4f})"
    print(f" {'H1 ① 큰손 흡수':<24} | {h1_n:6d} | {fmt_cell(h1, 6):<16} | {fmt_cell(h1, 12):<16} | {fmt_cell(h1, 'krx'):<16} | {fmt_cell(h1, 'nxt'):<16} | {ant_summary:<20} | {h1_eval:<8}")
    print(f" {'H2 큰손 흡수 & 외국인 반대':<20} | {h2_n:6d} | {fmt_cell(h2, 6):<16} | {fmt_cell(h2, 12):<16} | {fmt_cell(h2, 'krx'):<16} | {fmt_cell(h2, 'nxt'):<16} | {'—':<20} | {h2_eval:<8}")
    print(f" {'H3 큰손 흡수 & 기관 동행':<22} | {h3_n:6d} | {fmt_cell(h3, 6):<16} | {fmt_cell(h3, 12):<16} | {fmt_cell(h3, 'krx'):<16} | {fmt_cell(h3, 'nxt'):<16} | {'—':<20} | {h3_eval:<8}")
    print(f" {'H4 ② 숨은 매집':<25} | {h4_n:6d} | {fmt_cell(h4, 6):<16} | {fmt_cell(h4, 12):<16} | {fmt_cell(h4, 'krx'):<16} | {fmt_cell(h4, 'nxt'):<16} | {'—':<20} | {h4_eval:<8}")
    print(f" {'H5 종목 이례도 |z|>=1.5 되돌림':<18} | {h5_n:6d} | {fmt_cell(h5, 6):<16} | {fmt_cell(h5, 12):<16} | {fmt_cell(h5, 'krx'):<16} | {fmt_cell(h5, 'nxt'):<16} | {'—':<20} | {h5_eval:<8}")
    print(f" {'H0 축 단독':<28} | {'—':<6} | {'—':<16} | {'—':<16} | {'—':<16} | {'—':<16} | {'—':<20} | {'기준선 안':<8}")
    print("=" * 128)

    # 시총 규모별 (대형주 vs 중형주/소형주) 층화 채점표
    large_panel = [r for r in panel if r.get("size_tier") == "대형주"]
    mid_panel = [r for r in panel if r.get("size_tier") in ("중형주", "소형주")]

    if large_panel and mid_panel:
        print("\n" + "=" * 116)
        print(" [시가총액 규모별 층화 채점표 (대형주 vs 중형주/소형주)]")
        print("-" * 116)
        print(f" {'시총 구분':<10} | {'가설':<22} | {'사건 수':<6} | {'6봉 일치율(bp)':<16} | {'12봉 일치율(bp)':<16} | {'KRX 종가(bp)':<16} | {'NXT 종가(bp)':<16}")
        print("-" * 116)
        for tier_name, sub_p in [("대형주 (16종)", large_panel), ("중형주 (2종)", mid_panel)]:
            sh1 = compute_stats(sub_p, s_absorb)
            sh4 = compute_stats(sub_p, s_hidden)
            sh5 = compute_stats(sub_p, s_z(1.5, True))
            print(f" {tier_name:<9} | {'H1 ① 큰손 흡수':<18} | {sh1['events']:6d} | {fmt_cell(sh1, 6):<16} | {fmt_cell(sh1, 12):<16} | {fmt_cell(sh1, 'krx'):<16} | {fmt_cell(sh1, 'nxt'):<16}")
            print(f" {'〃':<12} | {'H4 ② 숨은 매집':<20} | {sh4['events']:6d} | {fmt_cell(sh4, 6):<16} | {fmt_cell(sh4, 12):<16} | {fmt_cell(sh4, 'krx'):<16} | {fmt_cell(sh4, 'nxt'):<16}")
            print(f" {'〃':<12} | {'H5 이례도 되돌림':<20} | {sh5['events']:6d} | {fmt_cell(sh5, 6):<16} | {fmt_cell(sh5, 12):<16} | {fmt_cell(sh5, 'krx'):<16} | {fmt_cell(sh5, 'nxt'):<16}")
            if tier_name.startswith("대형주"):
                print("-" * 116)
        print("=" * 116)

    # 세션별 (NXT 프리마켓 vs KRX 정규장 vs NXT 야간장) 층화 채점표
    pre_panel = [r for r in panel if r.get("session") == "pre"]
    reg_panel = [r for r in panel if r.get("session") == "reg"]
    post_panel = [r for r in panel if r.get("session") == "post"]

    if pre_panel or reg_panel or post_panel:
        print("\n" + "=" * 116)
        print(" [세션별 층화 채점표 (NXT 프리마켓 vs KRX 정규장 vs NXT 야간장)]")
        print("-" * 116)
        print(f" {'세션 구분':<14} | {'가설':<22} | {'사건 수':<6} | {'6봉 일치율(bp)':<16} | {'12봉 일치율(bp)':<16} | {'KRX 종가(bp)':<16} | {'NXT 종가(bp)':<16}")
        print("-" * 116)
        for s_label, sub_p in [
            ("NXT 프리 (08:00~08:50)", pre_panel),
            ("KRX 정규 (09:00~15:20)", reg_panel),
            ("NXT 야간 (15:40~20:00)", post_panel),
        ]:
            if not sub_p:
                continue
            sh1 = compute_stats(sub_p, s_absorb)
            sh4 = compute_stats(sub_p, s_hidden)
            sh5 = compute_stats(sub_p, s_z(1.5, True))
            print(f" {s_label:<13} | {'H1 ① 큰손 흡수':<18} | {sh1['events']:6d} | {fmt_cell(sh1, 6):<16} | {fmt_cell(sh1, 12):<16} | {fmt_cell(sh1, 'krx'):<16} | {fmt_cell(sh1, 'nxt'):<16}")
            print(f" {'〃':<20} | {'H4 ② 숨은 매집':<20} | {sh4['events']:6d} | {fmt_cell(sh4, 6):<16} | {fmt_cell(sh4, 12):<16} | {fmt_cell(sh4, 'krx'):<16} | {fmt_cell(sh4, 'nxt'):<16}")
            print(f" {'〃':<20} | {'H5 이례도 되돌림':<20} | {sh5['events']:6d} | {fmt_cell(sh5, 6):<16} | {fmt_cell(sh5, 12):<16} | {fmt_cell(sh5, 'krx'):<16} | {fmt_cell(sh5, 'nxt'):<16}")
            print("-" * 116)
        print("=" * 116)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="다일 동기화 패널 누적 집계")
    ap.add_argument("--root", default="local/research/orderbook", help="연구 데이터 루트 디렉터리")
    ap.add_argument("--dates", help="분석 대상 날짜 쉼표 구분 (예: 2026-09-16,2026-09-17)")
    ap.add_argument("--from", dest="from_date", help="시작 날짜 (YYYY-MM-DD)")
    ap.add_argument("--to", dest="to_date", help="종료 날짜 (YYYY-MM-DD)")
    a = ap.parse_args(argv)

    dates = resolve_dates(a.dates, a.from_date, a.to_date)
    if not dates:
        print("[ERROR] --dates 또는 --from/--to 인자가 필요합니다.", file=sys.stderr)
        return 1

    panel, loaded_dates = load_multi_panel(a.root, dates)
    if not panel:
        print("[ERROR] 로드된 패널 데이터가 없습니다.", file=sys.stderr)
        return 1

    syms = sorted({r["sym"] for r in panel})
    print(f"multi-panel loaded rows={len(panel)} symbols={len(syms)} across {len(loaded_dates)} dates: {loaded_dates}")

    print("\n[기준선] 초과수익률(EX) — 0·50% 근처여야 한다")
    report("전 봉 +1", panel, lambda r: 1)
    report("직전 봉 초과수익 부호 추종", panel, lambda r: sgn(r.get("same_ex")))

    print("\n[A 큰손 vs 투자자 — 같은 것을 보고 있나] 부호 일치율")
    agree("큰손 봉 순매수 vs 외국인 5분 증분(증분≠0 봉)", panel, lambda r: r.get("whale_net"), lambda r: r.get("frgn_d5"))
    agree("큰손 봉 순매수 vs 기관 5분 증분", panel, lambda r: r.get("whale_net"), lambda r: r.get("orgn_d5"))
    agree("큰손 당일누적 vs 외국인 당일누적", panel, lambda r: r.get("whale_cum_ratio"), lambda r: r.get("frgn_cum"))
    agree("큰손 당일누적 vs 기관 당일누적", panel, lambda r: r.get("whale_cum_ratio"), lambda r: r.get("orgn_cum"))
    agree("큰손 봉 순매수 vs 시장 프로그램 5분 증분(ka90005)", panel, lambda r: r.get("whale_net"), lambda r: r.get("mprog_d5"))
    agree("큰손 당일누적 vs 업종 외국인 당일누적", panel, lambda r: r.get("whale_cum_ratio"), lambda r: r.get("sect_frgn_cum"))

    print("\n[① 큰손 흡수] 큰손 R>=0.4 & 같은 봉 초과수익<=0 — 사건 단위, EX")
    report("① 전체", panel, s_absorb)
    for name, ax in (("외국인 당일누적", lambda r: r.get("frgn_cum")), ("기관 당일누적", lambda r: r.get("orgn_cum")), ("외국인 5분증분", lambda r: r.get("frgn_d5")),
                     ("시장 프로그램 5분증분", lambda r: r.get("mprog_d5")), ("업종 외국인 누적", lambda r: r.get("sect_frgn_cum")), ("시장 외국인 누적", lambda r: r.get("mkt_frgn_cum"))):
        report(f"  + {name} 같은 방향", panel, with_axis(s_absorb, ax, 1))
        report(f"  + {name} 반대 방향", panel, with_axis(s_absorb, ax, -1))

    print("\n[② 숨은 매집/분산] 큰손 당일누적 |비율|>=0.2 & 종목 누적 초과수익 반대 — 사건 단위, EX")
    report("② 전체", panel, s_hidden)

    # T1 대조군
    q, th_w, th_m, th_a, w_cnt, tot_cnt = quantile_match_thresholds(panel, 0.4)
    print("\n[⑤ 큰손 흡수 대조군 — 고정 및 분위수 매칭 (T1)]")
    print(f"  [분위수 임계값] q={q:.4f}, 큰손={th_w:.4f}, 중간={th_m:.4f}, 개미={th_a:.4f}")
    report("큰손 R>=0.4 (H1 기준)", panel, make_absorb("whale_ratio", 0.4))
    report("중간 R>=0.4 (고정 0.4)", panel, make_absorb("mid_ratio", 0.4))
    report("개미 R>=0.4 (고정 0.4)", panel, make_absorb("ant_ratio", 0.4))
    report(f"중간 R>={th_m:.4f} (분위수 매칭)", panel, make_absorb("mid_ratio", th_m))
    report(f"개미 R>={th_a:.4f} (분위수 매칭)", panel, make_absorb("ant_ratio", th_a))

    # T2 H5
    print("\n[⑥ H5 이례도 z-score 신호 (T2)]")
    report("H5 |whale_z|>=1.5 순방향 추종", panel, s_z(1.5, False))
    report("H5 |whale_z|>=1.5 되돌림(반대)", panel, s_z(1.5, True))
    report("H5 |whale_z|>=2.0 되돌림(반대)", panel, s_z(2.0, True))

    # T4 투자자 신선도
    print("\n[⑦ 투자자 신선도별 큰손 흡수 ① 층화 (T4)]")
    report("① frgn_stale_min <= 30 (샘플 as_of)", panel, lambda r: s_absorb(r) if r.get("frgn_stale_min") is not None and r["frgn_stale_min"] <= 30 else 0)
    report("① frgn_stale_min > 30 (샘플 as_of)", panel, lambda r: s_absorb(r) if r.get("frgn_stale_min") is not None and r["frgn_stale_min"] > 30 else 0)

    # 채점표 출력
    print_scorecard(panel, th_a, q)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
