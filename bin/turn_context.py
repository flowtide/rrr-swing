#!/usr/bin/env python3
"""turn_context — 매 턴 주입되는 짧은 컨텍스트. 판단 0, 요약 0(절 이름으로 고르고 자르기만 한다).

시스템 프롬프트는 그날의 기준이라 장중에 바뀌지 않는다. **변화는 이쪽이 나른다**
(docs/05-context.md). 매 턴 실리므로 짧아야 한다 — 한글 1,000자 ≈ 600 토큰이고
장중 수백 턴이다.

싣는 것(앞이 우선, 상한에서 뒤부터 잘린다):

    mode              config.mode
    ## 운영자 지시     memory/state/ 최신
    ## 태도            memory/state/ 최신
    ## 열린 협의       memory/state/ 최신
    (상태 파일의 최종 수정 시각)

보유는 종목·수량이 잘 안 바뀌고 가격은 세션이 조회한다. 메모는 급하지 않다. 장세는 세션이
30분마다 직접 본다 — 그래서 뺀다.

훅이 실패해도 턴은 진행한다. 컨텍스트가 없다고 세션을 막지 않는다.

  python3 bin/turn_context.py [--local local] [--config config/config.json] [--cap N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import context_load as cl  # noqa: E402

DEFAULT_CAP_CHARS = 1000
# 싣는 절과 순서. 앞이 우선 — 상한에 걸리면 뒤가 잘린다.
SECTIONS = ("## 운영자 지시", "## 태도", "## 열린 협의")


def section_body(text: str, heading: str) -> str | None:
    """`## 이름` 절의 본문. 없으면 None. 고르기만 하고 요약하지 않는다."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == heading), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = "\n".join(lines[start + 1:end]).strip()
    return body or None


def build(local_dir: str, *, mode: str = "", cap_chars: int = DEFAULT_CAP_CHARS) -> str:
    state_path = cl.latest(cl.layer_dir(local_dir, "state"))
    head = f"[턴 컨텍스트] mode={mode or '미상'}"
    if state_path:
        age = datetime.fromtimestamp(os.path.getmtime(state_path)).strftime("%F %T")
        head += f" | 상태 {os.path.relpath(state_path, cl.memory_dir(local_dir))} (수정 {age})"
    else:
        return cl._cap(head + "\n상태 파일이 없다 — local/memory/state/ 가 비어 있다.\n", cap_chars)

    text = cl._read(state_path) or ""
    out = [head]
    for heading in SECTIONS:
        body = section_body(text, heading)
        out.append(f"{heading}\n{body if body else '(없음)'}")
    return cl._cap("\n".join(out) + "\n", cap_chars)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="turn_context", description="매 턴 주입 컨텍스트(mode·운영자 지시·태도·열린 협의). 판단 0")
    p.add_argument("--local", default="local")
    p.add_argument("--config", default="config/config.json")
    p.add_argument("--cap", type=int, default=DEFAULT_CAP_CHARS)
    a = p.parse_args(argv)
    mode = ""
    if os.path.exists(a.config):
        try:
            with open(a.config, encoding="utf-8") as f:
                mode = str(json.load(f).get("mode") or "")
        except ValueError:
            mode = ""
    sys.stdout.write(build(a.local, mode=mode, cap_chars=a.cap))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
