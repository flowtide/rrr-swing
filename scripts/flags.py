#!/usr/bin/env python3
"""flags — 회계(독립 코드) 시간 규율: horizon_d 만료·recheck_by 도달을 원장 `flag` 로만 남긴다. 결정 0(청산·계획 변경 없음).

입력(모두 읽기 전용): local/stories/<symbol>.md front matter(`plan_id`·`status`·`horizon_d`·`recheck_by`·`activated_at`·`as_of`, 선택 `account_id`),
local/positions.jsonl(최신 마크의 보유 종목), 원장(`plan_activated` 시각, 최신 `coverage.recheck_by`), config(`position_expiry_days`=horizon_d 기본값).
- expired: 기준일(anchor) + horizon_d **거래일**(주말·`--holidays` 제외) ≤ 오늘. anchor = 스토리 `activated_at` > 원장 plan_activated > 스토리 `as_of` > 첫 마크일(보유만).
- recheck_due: 스토리 `recheck_by` 또는 최신 `coverage.recheck_by`(둘 다 있으면 늦은 쪽) ≤ 오늘.
- 살아있는 스토리 = status ∈ {active, triggered}. draft·expired·cancelled·invalidated 는 대상 아님. 종목당 flag 1건(plan_id·position_id 동시 기재).
  스토리 디렉터리는 평면(clone 당 계좌 하나). front matter 에 `account_id` 가 있고 config 와 다르면 제외.
- 1회/일 멱등: dup_key=flag|<date>|<kind>|<account>|<sym>. 기한이 지나도 매일 다시 남긴다(overdue_decision 집계 근거). 결정(청산·새 plan)은 LLM 이 다음 야간 세션까지.
크론(20:30, marks 뒤) 설치는 사람이 한다(docs/02-runbook.md §4).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ledger as lg  # noqa: E402

LIVE_STATUSES = ("active", "triggered")


@dataclass
class Paths:
    stories: str = "local/stories"
    marks: str = "local/positions.jsonl"
    ledger: str = "local/ledger.jsonl"


# --- 달력(naive KST, 거래일 = 주중 - holidays) ---------------------------------------------
def is_trading_day(d: date, holidays=()) -> bool:
    return d.weekday() < 5 and d.isoformat() not in set(holidays)


def next_trading_day(day: str, holidays=()) -> str:
    d = date.fromisoformat(day) + timedelta(days=1)
    while not is_trading_day(d, holidays):
        d += timedelta(days=1)
    return d.isoformat()


def trading_days_after(anchor: str, n: int, holidays=()) -> str:
    """anchor 로부터 n 거래일 뒤 날짜(anchor 당일은 세지 않는다)."""
    d = anchor
    for _ in range(int(n)):
        d = next_trading_day(d, holidays)
    return d


def trading_days_between(a: str, b: str, holidays=()) -> int:
    """(a, b] 구간의 거래일 수. a ≥ b 면 0."""
    n, d = 0, a
    while d < b:
        d = next_trading_day(d, holidays)
        if d <= b:
            n += 1
    return n


# --- front matter(YAML 부분집합: key: scalar | {k: v, …} | [a, b] | 다음 줄 '- item' 목록) ---------
def _scalar(s: str):
    s = s.strip()
    if not s:
        return ""
    if (s[0] == s[-1] == '"') or (s[0] == s[-1] == "'"):
        return s[1:-1]
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~"):
        return None
    if s.startswith("{") and s.endswith("}"):
        out = {}
        for part in filter(None, (p.strip() for p in s[1:-1].split(","))):
            k, _, v = part.partition(":")
            out[k.strip()] = _scalar(v)
        return out
    if s.startswith("[") and s.endswith("]"):
        return [_scalar(p) for p in s[1:-1].split(",") if p.strip()]
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def parse_front_matter(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm: dict = {}
    key = None
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        if ln.startswith("  - ") or ln.startswith("- "):
            if key is not None:
                fm.setdefault(key, [])
                if isinstance(fm[key], list):
                    fm[key].append(_scalar(ln.split("- ", 1)[1]))
            continue
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        k, sep, v = ln.partition(":")
        if not sep or ln.startswith(" "):
            continue
        key = k.strip()
        fm[key] = _scalar(v) if v.strip() else []
    return fm


def load_stories(stories_dir: str) -> list[dict]:
    """<stories_dir>/<symbol>.md 전부(평면). sym 은 front matter `symbol`, 없으면 파일명. account_id 는 front matter 에 있을 때만."""
    stories = []
    if not os.path.isdir(stories_dir):
        return stories
    for name in sorted(os.listdir(stories_dir)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(stories_dir, name)
        with open(path, encoding="utf-8") as f:
            fm = parse_front_matter(f.read())
        fm["sym"] = str(fm.get("symbol") or name[:-3])
        fm["path"] = path
        stories.append(fm)
    return stories


# --- 계산 -----------------------------------------------------------------------------------
def _date10(v) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s[:10] if len(s) >= 10 else None


def _current_positions(marks: list[dict]) -> tuple[set[str], dict[str, str]]:
    """최신 마크의 보유 종목과, 종목별 현재 보유 run 의 첫 마크일."""
    rows = sorted((r for r in marks if r.get("trade_date")), key=lambda r: r["trade_date"])
    if not rows:
        return set(), {}
    held = {p["sym"] for p in rows[-1].get("positions", [])}
    first: dict[str, str] = {}
    for sym in held:
        i = len(rows) - 1
        while i >= 0 and any(p["sym"] == sym for p in rows[i].get("positions", [])):
            first[sym] = rows[i]["trade_date"]
            i -= 1
    return held, first


def compute_flags(*, date: str, stories: list[dict], marks: list[dict], ledger_events: list[dict], config: dict, holidays=(), now: str | None = None) -> list[dict]:
    account = str(config.get("account_id") or "")
    default_h = int(config.get("position_expiry_days") or 20)
    ts = now or (f"{date}T{datetime.now().strftime('%H:%M:%S')}" if date == datetime.now().date().isoformat() else f"{date}T20:30:00")
    activated: dict[str, str] = {}
    coverage: dict[str, tuple[str, str]] = {}
    for e in sorted((e for e in ledger_events if isinstance(e, dict) and e.get("ts")), key=lambda e: e["ts"]):
        if e.get("evt") == "plan_activated" and e.get("plan_id"):
            activated[str(e["plan_id"])] = e["ts"]
        elif e.get("evt") == "coverage" and e.get("sym") and _date10(e.get("recheck_by")):
            coverage[str(e["sym"])] = (_date10(e["recheck_by"]), e["ts"])
    held, first_mark = _current_positions(marks)
    live: dict[str, dict] = {}
    for p in stories:
        if account and p.get("account_id") is not None and str(p.get("account_id")) != account:
            continue
        if str(p.get("status")) in LIVE_STATUSES:
            live[p["sym"]] = p
    flags: list[dict] = []
    for sym in sorted(set(live) | held):
        plan = live.get(sym)
        ids: dict = {}
        if plan:
            ids["plan_id"] = str(plan.get("plan_id") or f"plan:{sym}")
        if sym in held:
            ids["position_id"] = f"pos:{sym}"
        anchor = anchor_src = None
        if plan:
            if _date10(plan.get("activated_at")):
                anchor, anchor_src = _date10(plan["activated_at"]), "story_activated_at"
            elif activated.get(ids["plan_id"]):
                anchor, anchor_src = activated[ids["plan_id"]][:10], "ledger_plan_activated"
            elif _date10(plan.get("as_of")):
                anchor, anchor_src = _date10(plan["as_of"]), "story_as_of"
            elif sym in first_mark:
                anchor, anchor_src = first_mark[sym], "first_mark"
            h = plan.get("horizon_d")
            horizon, h_src = (int(h), "story") if isinstance(h, int) and not isinstance(h, bool) and h >= 1 else (default_h, "config_default")
        else:
            anchor, anchor_src = first_mark.get(sym), "first_mark"
            horizon, h_src = default_h, "config_default"
        if anchor:
            due = trading_days_after(anchor, horizon, holidays)
            if date >= due:
                flags.append({"kind": "expired", "ts": ts, "sym": sym, **ids, "anchor": anchor, "anchor_source": anchor_src, "horizon_d": horizon,
                              "horizon_source": h_src, "due": due, "days_overdue": trading_days_between(due, date, holidays),
                              "dup_key": f"flag|{date}|expired|{account}|{sym}"})
        candidates = []
        if plan and _date10(plan.get("recheck_by")):
            candidates.append((_date10(plan["recheck_by"]), "story"))
        if sym in held and sym in coverage:
            candidates.append((coverage[sym][0], "ledger_coverage"))
        if candidates:
            recheck, r_src = max(candidates)  # 가장 늦은 약속이 유효
            if date >= recheck:
                flags.append({"kind": "recheck_due", "ts": ts, "sym": sym, **ids, "due": recheck, "recheck_source": r_src,
                              "days_overdue": trading_days_between(recheck, date, holidays), "dup_key": f"flag|{date}|recheck_due|{account}|{sym}"})
    return flags


def _read_jsonl(path: str) -> list[dict]:
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    return out


def run(paths: Paths, *, date: str, config: dict, holidays=(), dry_run: bool = False) -> dict:
    flags = compute_flags(date=date, stories=load_stories(paths.stories), marks=_read_jsonl(paths.marks), ledger_events=_read_jsonl(paths.ledger),
                          config=config, holidays=holidays)
    appended = dup = 0
    if not dry_run:
        for f in flags:
            rec = lg.append_event(paths.ledger, "flag", f, role="accounting", account_id=str(config.get("account_id") or ""), ts=f["ts"])
            if rec is None:
                dup += 1
            else:
                appended += 1
    return {"date": date, "flags": flags, "appended": appended, "dup": dup, "dry_run": dry_run}


def selftest() -> bool:
    story = parse_front_matter("---\nsymbol: 336260\nplan_id: p1\nstatus: active\nhorizon_d: 5\nrecheck_by: 2026-06-24\nactivated_at: 2026-06-17T21:00:00\nentry_zone: {lo: 1, hi: 2}\n---\n")
    story["sym"] = "336260"
    assert trading_days_after("2026-06-17", 5) == "2026-06-24" and trading_days_after("2026-06-17", 5, holidays=("2026-06-19",)) == "2026-06-25"
    cfg = {"account_id": "a", "position_expiry_days": 3}
    assert compute_flags(date="2026-06-23", stories=[story], marks=[], ledger_events=[], config=cfg) == []
    fl = compute_flags(date="2026-06-24", stories=[story], marks=[], ledger_events=[], config=cfg)
    assert sorted(f["kind"] for f in fl) == ["expired", "recheck_due"] and all(lg.validate_event("flag", f) == [] for f in fl)
    marks = [{"trade_date": "2026-06-01", "positions": [{"sym": "005930", "qty": 1}]}]
    pos = compute_flags(date="2026-06-04", stories=[], marks=marks, ledger_events=[], config=cfg)
    assert [f["position_id"] for f in pos] == ["pos:005930"] and pos[0]["horizon_source"] == "config_default"
    assert compute_flags(date="2026-06-03", stories=[], marks=marks, ledger_events=[], config=cfg) == []
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="flags", description="회계: 만료·재검토 플래그 (결정 0)")
    p.add_argument("--date", default=None, help="기준일 YYYY-MM-DD (기본 오늘)")
    p.add_argument("--stories", default="local/stories")
    p.add_argument("--marks", default="local/positions.jsonl")
    p.add_argument("--ledger", default="local/ledger.jsonl")
    p.add_argument("--config", default="config/config.json")
    p.add_argument("--account", default=None)
    p.add_argument("--holidays", default=None, help="휴장일 JSON 배열 파일 [\"YYYY-MM-DD\", …]")
    p.add_argument("--dry-run", action="store_true", help="계산만, 원장 기록 없음")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)
    if a.selftest:
        selftest()
        print(json.dumps({"selftest": "ok"}))
        return 0
    cfg = {}
    if os.path.exists(a.config):
        try:
            with open(a.config, encoding="utf-8") as f:
                cfg = json.load(f)
        except ValueError:
            print(f"ERROR: config 파싱 실패: {a.config}", file=sys.stderr)
            return 2
    if a.account:
        cfg["account_id"] = a.account
    if not cfg.get("account_id"):
        print("ERROR: account_id 없음 (--account 또는 config.account_id)", file=sys.stderr)
        return 2
    holidays = ()
    if a.holidays:
        with open(a.holidays, encoding="utf-8") as f:
            holidays = tuple(json.load(f))
    res = run(Paths(stories=a.stories, marks=a.marks, ledger=a.ledger), date=a.date or date.today().isoformat(), config=cfg, holidays=holidays, dry_run=a.dry_run)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
