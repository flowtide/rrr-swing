#!/usr/bin/env python3
"""rrr-swing 운영자 보고 — console 채널(기본). stdout 에 출력하고 local/reports/<YYYY-MM-DD>.md 에 누적한다.

  bin/report.py --text "준비 완료"     |  echo "…" | bin/report.py
운영자 채널이 telegram 이면 bin/tg_send.py 를 쓴다(config.operator_channel). 종료 코드: 0 성공, 2 인자 오류.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime


def resolve_text(arg_text: str | None, stdin_text: str | None = None) -> str:
    if arg_text is not None:
        return arg_text
    if stdin_text is None:
        stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()
    return stdin_text.strip()


def write_report(text: str, *, reports_dir: str = "local/reports", now: str | None = None) -> str:
    stamp = now or datetime.now().isoformat(timespec="seconds")
    day, clock = stamp[:10], stamp[11:19]
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, f"{day}.md")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"## {clock}\n\n{text}\n\n")
    return path


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="운영자 보고 (console 채널)")
    ap.add_argument("--text", "-t", default=None)
    ap.add_argument("--reports-dir", default="local/reports")
    a = ap.parse_args(argv)
    text = resolve_text(a.text)
    if not text:
        print("ERROR: 보고 본문이 비어 있음", file=sys.stderr); return 2
    path = write_report(text, reports_dir=a.reports_dir)
    print(text)
    print(f"(saved: {path})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
