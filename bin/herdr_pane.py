#!/usr/bin/env python3
"""이 프로세스가 실제로 들어 있는 herdr pane 을 찾는다.

왜 필요한가: `herdr pane current` 는 **포커스된** pane 을 돌려줄 뿐, 호출자가 어느 pane
안에 있는지는 알려주지 않는다. 기동 직후 운영자가 포커스를 옮기면 역할 이름이 엉뚱한
pane 에 붙는다 — 실제로 rs-lead 가 exec 쓸 pane(w3:p2)에 붙어 다이제스트 배달이 한 건
실패했다. herdr 에 "내 pane" 질의가 없으므로 프로세스 조상으로 찾는다.

판별: 내 pid 에서 ppid 를 따라 올라가며 조상 집합을 만들고, 각 pane 의 `shell_pid`·
전면 프로세스 pid 와 교차시킨다. 교차하는 pane 이 내가 들어 있는 pane 이다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

MAX_DEPTH = 40  # 조상 추적 상한(순환·비정상 트리 방어)


def _ppid(pid: int) -> int | None:
    r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out.isdigit():
        return None
    parent = int(out)
    return parent if parent > 0 else None


def ancestors(pid: int) -> set[int]:
    """pid 자신과 그 조상 pid 집합. pid 1 또는 상한에서 멈춘다."""
    seen: set[int] = set()
    cur: int | None = int(pid)
    while cur and cur > 1 and len(seen) < MAX_DEPTH and cur not in seen:
        seen.add(cur)
        cur = _ppid(cur)
    return seen


def _herdr_json(herdr_bin: str, *args: str) -> dict:
    r = subprocess.run([herdr_bin, *args], capture_output=True, text=True)
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout or "{}")
    except ValueError:
        return {}


def pane_pids(herdr_bin: str, pane_id: str) -> set[int]:
    """pane 의 셸 pid 와 전면 프로세스 pid."""
    info = ((_herdr_json(herdr_bin, "pane", "process-info", "--pane", pane_id).get("result") or {})
            .get("process_info") or {})
    pids = set()
    shell = info.get("shell_pid")
    if isinstance(shell, int):
        pids.add(shell)
    for p in info.get("foreground_processes") or []:
        if isinstance(p, dict) and isinstance(p.get("pid"), int):
            pids.add(p["pid"])
    return pids


def resolve_self_pane(pid: int | None = None, herdr_bin: str = "herdr") -> str | None:
    """이 프로세스가 들어 있는 pane id. 찾지 못하면 None — 포커스 pane 으로 대신하지 않는다."""
    mine = ancestors(os.getpid() if pid is None else int(pid))
    panes = (_herdr_json(herdr_bin, "pane", "list").get("result") or {}).get("panes") or []
    for pane in panes:
        pane_id = pane.get("pane_id")
        if not pane_id:
            continue
        if pane_pids(herdr_bin, pane_id) & mine:
            return pane_id
    return None


def main(argv: list[str]) -> int:
    pid = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else os.getpid()
    pane = resolve_self_pane(pid, os.environ.get("HERDR_BIN") or "herdr")
    print(pane or "")
    return 0 if pane else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
