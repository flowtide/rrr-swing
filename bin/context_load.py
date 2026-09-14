#!/usr/bin/env python3
"""context_load — 기동 컨텍스트를 고정 예산으로 조립해 낸다. 판단 0, 요약 0(고르고 자르기만 한다).

local/memory/ 세 원본의 **최신 날짜 파일**을 머지한다(docs/05-context.md):

    ## 운영자 지시   memory/state/ 에서 끌어올린다
    ## 규칙          memory/playbook/ 최신
    ## 상태          memory/state/ 최신(운영자 지시 절 제외)
    ## 최근 일지     memory/journal/ 최신 N개, 최신부터

**담는 순서가 곧 우선순위다.** 예산은 공용 풀에서 앞 절부터 소진되므로 뒤 절이 먼저 굶는다.
그래서 운영자 지시와 규칙을 앞에, 지나간 서사를 뒤에 둔다. 일지 절 안에서도 최신 일자를
먼저 담는다 — 오래된 것부터 담으면 예산이 빠듯할 때 오늘 일지가 먼저 잘린다.

- 예산: config memory.load_budget_chars(기본 12000). 파일당 상한 memory.file_cap_chars(기본 4000).
  초과분은 잘라내고 "[truncated]" 를 붙인다.
- 예산이 모자라 절이 통째로 빠지면 맨 끝에 STARVED 한 줄을 남긴다. 빠진 것과 원래 비어 있던 것을
  구분하지 못하면 세션이 "규칙이 없다"고 읽는다.
- 원본이 하나도 없으면 거부한다(exit 2) — 최초 설치 상태이며 bin/init_local.sh 가 필요하다.

  python3 bin/context_load.py [--local local] [--config config/config.json] [--out <파일>]
                              [--days N] [--budget N] [--file-cap N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

DEFAULT_JOURNAL_DAYS = 3
DEFAULT_BUDGET_CHARS = 12000
DEFAULT_FILE_CAP_CHARS = 4000
TRUNC = "[truncated]"
STARVED = "[예산 소진 — 읽지 못한 절: {names}]"
DIRECTIVE_HEADING = "## 운영자 지시"
_DATE_MD = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")

# 원본 3층이 사는 곳. 기동 산출물(system-prompts/)은 여기 두지 않는다 — 고치는 곳과
# 만들어지는 곳을 붙여 두면 산출물을 고치고 다음 기동에 잃는다(docs/05-context.md).
MEMORY_SUBDIR = "memory"
LAYERS = ("playbook", "state", "journal")

# (라벨, 헤더 접두) — 담는 순서이자 우선순위. 뒤에 올수록 먼저 굶는다.
SECTION_HEADS = (
    ("DIRECTIVE", "## 운영자 지시"),
    ("PLAYBOOK", "## 규칙"),
    ("STATE", "## 상태"),
    ("JOURNAL", "## 최근 일지"),
)


def _read(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _cap(text: str, limit: int) -> str:
    """limit 자 안으로. 넘치면 잘라내고 TRUNC 를 붙인다(TRUNC 포함 길이 ≤ limit)."""
    if limit <= 0:
        return TRUNC[:max(0, limit)]
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(TRUNC) - 1)
    return text[:keep].rstrip() + "\n" + TRUNC if keep else TRUNC


def memory_dir(local_dir: str) -> str:
    """원본 3층의 뿌리. 경로 권위는 여기 하나다."""
    return os.path.join(local_dir, MEMORY_SUBDIR)


def layer_dir(local_dir: str, layer: str) -> str:
    return os.path.join(memory_dir(local_dir), layer)


def dated_files(layer_dir: str) -> list[str]:
    """<날짜>.md 파일, 오래된 것부터. 날짜가 아닌 이름은 무시한다."""
    if not os.path.isdir(layer_dir):
        return []
    return sorted(n for n in os.listdir(layer_dir) if _DATE_MD.match(n))


def latest(layer_dir: str) -> str | None:
    """그 층의 '현재' = 최신 날짜 파일의 경로. 없으면 None."""
    names = dated_files(layer_dir)
    return os.path.join(layer_dir, names[-1]) if names else None


def recent_journals(journal_dir: str, days: int) -> list[str]:
    """최신 days 개, **최신부터**.

    절 안에서도 담는 순서가 우선순위다. 오래된 것부터 담으면 예산이 빠듯할 때 오늘 일지가
    먼저 잘린다 — 가장 쓸모 있는 하루를 가장 먼저 버리는 셈이다.
    """
    if days <= 0:
        return []
    return list(reversed(dated_files(journal_dir)[-days:]))


def split_directive(state_text: str | None) -> tuple[str | None, str | None]:
    """상태 본문에서 `## 운영자 지시` 절을 떼어낸다. 반환: (지시 본문, 나머지 상태).

    떼어내기는 **절 이름으로 고르는 것**이지 요약이 아니다 — 본문은 그대로 옮긴다.
    지시를 맨 앞에 두는 것은 그것이 규칙보다 먼저 읽혀야 하기 때문이다.
    """
    if state_text is None:
        return None, None
    lines = state_text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == DIRECTIVE_HEADING), None)
    if start is None:
        return None, state_text
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = "\n".join(lines[start + 1:end]).strip()
    rest = "\n".join(lines[:start] + lines[end:])
    return (body or None), rest


def build(local_dir: str, *, journal_days: int = DEFAULT_JOURNAL_DAYS,
          budget_chars: int = DEFAULT_BUDGET_CHARS, file_cap_chars: int = DEFAULT_FILE_CAP_CHARS) -> str:
    """네 절을 순서대로 담되 전체 길이 ≤ budget_chars(헤더 포함)."""
    remaining = int(budget_chars)
    out: list[str] = []

    def emit(text: str) -> None:
        nonlocal remaining
        piece = _cap(text, remaining) if len(text) > remaining else text
        out.append(piece)
        remaining -= len(piece)

    def section(header: str, body: str | None) -> None:
        emit(header + "\n")
        if body is None:
            emit("(없음)\n\n")
            return
        emit(_cap(body.rstrip("\n"), int(file_cap_chars)) + "\n\n")

    root = memory_dir(local_dir)
    pb = latest(layer_dir(local_dir, "playbook"))
    st = latest(layer_dir(local_dir, "state"))
    directive, state_rest = split_directive(_read(st) if st else None)

    section(f"## 운영자 지시 ({os.path.relpath(st, root) if st else '없음'})", directive)
    section(f"## 규칙 ({os.path.relpath(pb, root) if pb else '없음'})", _read(pb) if pb else None)
    section(f"## 상태 ({os.path.relpath(st, root) if st else '없음'})", state_rest)

    jdir = layer_dir(local_dir, "journal")
    names = recent_journals(jdir, int(journal_days))
    # "최근 N일" 이 아니라 "최근 N개" 다 — 날짜가 아니라 파일 개수로 고른다. 휴장이 끼면
    # 사흘 전이 아닌 파일이 들어오므로 날짜를 함께 적고 라벨도 사실대로 쓴다.
    emit(f"## 최근 일지 ({int(journal_days)}개, 최신부터: {', '.join(n[:-3] for n in names) or '없음'})\n")
    if not names:
        emit("(없음)\n\n")
    for n in names:
        section(f"### {n}", _read(os.path.join(jdir, n)))

    text = "".join(out)
    if len(text) > int(budget_chars):
        text = _cap(text, int(budget_chars))
    return _mark_starved(text, int(budget_chars))


def _mark_starved(text: str, budget_chars: int) -> str:
    """예산이 모자라 통째로 빠진 절이 있으면 맨 끝에 한 줄로 알린다.

    빠진 절은 헤더조차 남지 않는다(`_cap(…, 0)` → 빈 문자열). 그러면 "규칙이 비어 있다" 와
    "규칙을 읽지 못했다" 가 구분되지 않는다.
    """
    if all(head in text for _, head in SECTION_HEADS):
        return text
    # 한 줄을 붙일 자리를 먼저 비우고, **비운 뒤의 본문**으로 무엇이 빠졌는지 센다.
    worst = "\n" + STARVED.format(names=", ".join(l for l, _ in SECTION_HEADS)) + "\n"
    kept = text[:max(0, budget_chars - len(worst))].rstrip("\n")
    missing = [label for label, head in SECTION_HEADS if head not in kept]
    out = kept + "\n" + STARVED.format(names=", ".join(missing)) + "\n"
    return out if len(out) <= budget_chars else out[:budget_chars]


def has_any_source(local_dir: str) -> bool:
    """원본이 하나라도 있나. 없으면 최초 설치 상태다."""
    return any(dated_files(layer_dir(local_dir, d)) for d in LAYERS)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="context_load",
                                description="기동 컨텍스트 조립(운영자 지시·규칙·상태·최근 일지 순, 고정 예산). 판단 0")
    p.add_argument("--local", default="local")
    p.add_argument("--config", default="config/config.json")
    p.add_argument("--out", default=None, help="산출물 경로. 생략하면 stdout")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--budget", type=int, default=None)
    p.add_argument("--file-cap", type=int, default=None)
    a = p.parse_args(argv)

    if not has_any_source(a.local):
        print(f"ERROR: 기억 원본이 하나도 없다 ({a.local}/{{playbook,state,journal}}) — bin/init_local.sh 를 먼저 실행하시오.",
              file=sys.stderr)
        return 2

    mem = {}
    if os.path.exists(a.config):
        try:
            with open(a.config, encoding="utf-8") as f:
                mem = (json.load(f).get("memory") or {})
        except ValueError:
            mem = {}
    days = a.days if a.days is not None else int(mem.get("journal_days") or DEFAULT_JOURNAL_DAYS)
    budget = a.budget if a.budget is not None else int(mem.get("load_budget_chars") or DEFAULT_BUDGET_CHARS)
    cap = a.file_cap if a.file_cap is not None else int(mem.get("file_cap_chars") or DEFAULT_FILE_CAP_CHARS)
    text = build(a.local, journal_days=days, budget_chars=budget, file_cap_chars=cap)

    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(a.out)
    else:
        sys.stdout.write(text)
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
