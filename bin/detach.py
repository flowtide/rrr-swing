#!/usr/bin/env python3
"""명령을 **자기 세션·자기 프로세스 그룹**으로 띄운다.  bin/detach.py <cmd> [args…]

start.sh 는 어댑터를 `&` 로 띄운 뒤 `exec` 로 claude 가 된다. 스크립트에서는 job control 이
꺼져 있어 `&` 가 새 그룹을 만들지 않고, exec 는 PID 를 지킨다 — 그래서 어댑터의 그룹이 곧
claude 의 그룹이 되고, 그 그룹에 가는 시그널(pane 정리·인터럽트·자식 정리)을 어댑터도 함께
맞는다. 이 스크립트는 setsid(2) 로 그 결합을 끊는다. macOS 에는 setsid(1) 이 없다.

exec 로 넘기므로 pid 가 그대로다 — 띄운 셸의 `$!` 가 곧 그 명령이다.
"""
import os
import sys


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: detach.py <cmd> [args…]", file=sys.stderr)
        return 2
    try:
        os.setsid()
    except OSError:
        # 이미 그룹 리더라면 그것으로 충분하다 — 목적은 띄운 쪽의 그룹에 남지 않는 것이다.
        pass
    try:
        os.execvp(argv[1], argv[1:])
    except OSError as exc:
        print(f"detach.py: {argv[1]}: {exc}", file=sys.stderr)
        return 2
    return 0  # unreachable


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
