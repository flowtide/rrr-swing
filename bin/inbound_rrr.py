#!/usr/bin/env python3
"""rrr-swing 인바운드 어댑터 — rrr 스트림 소스(기본). stdlib SSE 클라이언트 → 코어(inbound_core) → 배달.

결정 3: 배달만, 판단 0. `/context`·`/flow`·주문 호출 없음(허용: /api/health, /api/events, /api/events/stream).
- 소스: `GET {gw.base_url}/api/events/stream?since=&symbols=&kinds=&evts=`(text/event-stream, X-API-Key). 서버 필터(쿼리)는
  편의이고 **로컬 구독 재검사가 권위**다(세션·만료는 서버가 모른다). macro 를 구독하면 symbols 필터를 보내지 않는다(macro 는 종목이 없어
  서버 필터에 걸리므로) — 로컬 재검사가 나머지를 거른다.
- 커서: local/events.cursor — 엔트리가 **처리(다이제스트 배달 또는 skip 기록)된 뒤** 저장(at-least-once). 커서 없으면 since=$(접속 이후만,
  소급 없음 — /mon/watch 의미론), --since 0|<id> 로 override.
- 재접속: backoff 1→2→4→…→60s(지터 ±20%), since=<커서>. 하트비트 부재 heartbeat_timeout_sec(기본 45) 초과 → 끊고 재접속.
  401 → 종료(exit 2, 설정 오류). 503(busy)·기타 → backoff. `event: error` 프레임 → 재접속.
- --once: `GET {gw.base_url}/api/events?since=<커서>&limit=500` 페이지로 밀린 구간만 처리하고 종료(재시작 뒤 catch-up).

종료 조건(이 목록이 전부다. 어떤 경로로 끝나든 마지막 줄에 `EXIT code=<n> reason=<이유>` 를 남긴다 —
조용히 사라지면 rs-lead 는 "이벤트가 없는 장"과 구분할 수 없다):

    0  ok          --once catch-up 완료, 또는 --max-reconnects 도달(테스트)
    2  config      설정·인자 오류, 또는 401(키가 틀렸다 — 재시도하지 않는다)
    3  undelivered 배달 실패가 재시도 창(기본 5분, RS_DELIVER_RETRY_SEC)을 넘겼다.
                   그 안에서는 백오프 재시도하며 살아 있다. agent_blocked 는 여기 해당하지
                   않는다 — 차단은 큐(상한 3, FIFO)가 받고 계속 돈다
    4  crashed     그 밖의 예상 밖 예외

신호: SIGTERM 으로 죽는다(start.sh 의 이전 어댑터 정리가 쓴다). SIGHUP(nohup)·SIGINT(비대화형 셸의
백그라운드 자식)은 무시하므로 pane 을 닫거나 Ctrl-C 를 눌러도 살아 있고, claude 가 끝나면 고아가 되어
계속 돈다.
"""
from __future__ import annotations

import json
import os
import random
import socket
import subprocess
import sys
import traceback
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inbound_core as core  # noqa: E402

LIVE_SINCE = "$"
MAX_BACKOFF_SEC = 60.0
KIND_ORDER = ("zone", "macro")

# 종료 코드 — 위 docstring 의 표와 짝이다(tests/test_inbound_exit.py 가 둘을 맞춰 둔다).
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_UNDELIVERED = 3
EXIT_CRASHED = 4


def log_exit(code: int, reason: str, detail: str = "", out=None) -> int:
    """마지막 줄에 종료 사유를 남긴다.

    트레이스백만 남기면 rs-lead 도 운영자도 "왜 멈췄는지"를 로그에서 바로 읽을 수 없다.
    """
    line = f"{datetime.now().isoformat(timespec='seconds')} EXIT code={code} reason={reason}"
    if detail:
        line += f" detail={str(detail).splitlines()[-1][:200]}"
    print(line, file=out or sys.stderr, flush=True)
    return code


# ---------------------------------------------------------------- 순수 함수

def parse_sse(lines):
    """줄 iterable → ('frame', {id,event,data}) | ('comment', text). 빈 줄이 프레임을 완성한다. data 여러 줄은 \n 으로 잇는다."""
    fields: dict[str, str] = {}
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n") if isinstance(raw, str) else raw.decode("utf-8", "replace").rstrip("\r\n")
        if line == "":
            if fields or data:
                out = dict(fields)
                if data:
                    out["data"] = "\n".join(data)
                yield "frame", out
                fields, data = {}, []
            continue
        if line.startswith(":"):
            yield "comment", line[1:].strip()
            continue
        key, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if key == "data":
            data.append(value)
        else:
            fields[key] = value
    if fields or data:
        out = dict(fields)
        if data:
            out["data"] = "\n".join(data)
        yield "frame", out


def subscription_query(sub: dict) -> dict[str, str]:
    events = list(sub.get("event_types") or [])
    zone_evts = [e for e in core.ZONE_EVENTS if e in events]
    kinds = [k for k in KIND_ORDER if (k == "zone" and zone_evts) or (k == "macro" and "macro" in events)]
    q: dict[str, str] = {}
    if sub.get("symbols"):
        q["symbols"] = ",".join(sub["symbols"])
    if kinds:
        q["kinds"] = ",".join(kinds)
    return q


STREAM_PATH = "/api/events/stream"
REPLAY_PATH = "/api/events"


def build_stream_url(base_url: str, arg2: dict | str, arg3: dict | None = None, *, since: str, path: str = STREAM_PATH) -> str:
    if isinstance(arg2, str):
        p, sub = arg2, (arg3 or {})
    else:
        p, sub = path, arg2
    p = p if p.startswith("/") else f"/{p}"
    q = {"since": since, **subscription_query(sub)}
    return f"{base_url.rstrip('/')}{p}?{urllib.parse.urlencode(q)}"


def build_replay_url(base_url: str, arg2: dict | str, arg3: dict | None = None, *, since: str, limit: int = 500, path: str = REPLAY_PATH) -> str:
    if isinstance(arg2, str):
        p, sub = arg2, (arg3 or {})
    else:
        p, sub = path, arg2
    p = p if p.startswith("/") else f"/{p}"
    q = {"since": since, "limit": str(limit), **subscription_query(sub)}
    return f"{base_url.rstrip('/')}{p}?{urllib.parse.urlencode(q)}"


def read_cursor(path: str) -> str | None:
    try:
        value = open(path, encoding="utf-8").read().strip()
    except OSError:
        return None
    return value or None


def write_cursor(path: str, value: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 클라이언트

class RrrStreamClient:
    def __init__(self, cfg: dict, sub: dict, proc: core.Processor, *, cursor_path: str, sleep=None, max_reconnects: int | None = None,
                 rand=None, log=None):
        self.cfg = cfg
        self.sub = sub
        self.proc = proc
        self.cursor_path = cursor_path
        self.sleep = sleep or time.sleep
        self.max_reconnects = max_reconnects
        self.rand = rand or random.random
        self.log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
        gw = core.resolve_gw_config(cfg)
        self.base_url = gw.base_url
        self.api_key = gw.api_key
        self.token = self.api_key
        inbound = cfg.get("inbound") or {}
        self.heartbeat_timeout = float(inbound.get("heartbeat_timeout_sec", 45))
        self.since_override: str | None = None
        self._newest_ref: str | None = None

    # -- 커서: 배달·skip 이 끝난 엔트리까지만 저장 --------------------------------
    def _note_ref(self, ref: str) -> None:
        self._newest_ref = ref
        self._commit_cursor_if_settled()

    def _commit_cursor_if_settled(self) -> None:
        ref_to_commit = getattr(self.proc, "cursor_committed_ref", None)
        if ref_to_commit is not None:
            write_cursor(self.cursor_path, ref_to_commit)

    def _initial_since(self) -> str:
        if self.since_override:
            return self.since_override
        return read_cursor(self.cursor_path) or LIVE_SINCE

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key, "Accept": "text/event-stream", "Cache-Control": "no-cache"}

    # -- 엔트리 처리 ----------------------------------------------------------------
    def _process_entry(self, entry: dict, *, source: str) -> None:
        ref = str(entry.get("id", ""))
        self.proc.handle_event(core.entry_to_tagged(entry), source=source, ref=ref)
        self._note_ref(ref)

    # -- 라이브 스트림 1회 접속 ------------------------------------------------------
    def _stream_once(self, since: str) -> tuple[str, int]:
        """반환: (종료 사유 eof|error|timeout, 받은 프레임 수). HTTPError·URLError 는 전파."""
        url = build_stream_url(self.base_url, self.sub, since=since)
        req = urllib.request.Request(url, headers=self._headers())
        frames = 0
        with urllib.request.urlopen(req, timeout=self.heartbeat_timeout) as resp:
            try:
                for kind, payload in parse_sse(iter(resp.readline, b"")):
                    if kind == "comment":
                        self.proc.tick(); self._commit_cursor_if_settled()
                        continue
                    frames += 1
                    if payload.get("event") == "error":
                        self.log(f"rrr_stream_error data={payload.get('data')}")
                        return "error", frames
                    try:
                        entry = json.loads(payload.get("data") or "{}")
                    except ValueError:
                        entry = {}
                    if payload.get("id") and "id" not in entry:
                        entry["id"] = payload["id"]
                    self._process_entry(entry, source="rrr_stream")
                    self.proc.tick(); self._commit_cursor_if_settled()
            except (socket.timeout, TimeoutError):
                return "timeout", frames
        return "eof", frames

    # -- catch-up (--once) -------------------------------------------------------------
    def _catch_up(self, since: str) -> int:
        cursor = since if since != LIVE_SINCE else "0"
        while True:
            url = build_replay_url(self.base_url, self.sub, since=cursor)
            req = urllib.request.Request(url, headers={"X-API-Key": self.api_key, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                page = json.loads(resp.read().decode("utf-8"))
            for entry in page.get("entries", []):
                self._process_entry(entry, source="rrr_replay")
            next_since = str(page.get("next_since") or cursor)
            if next_since != cursor:
                self._newest_ref = next_since
            cursor = next_since
            if not page.get("truncated") and int(page.get("count", 0)) < 500:
                break
        self.proc.finalize()
        self._commit_cursor_if_settled()
        return 0

    # -- 루프 -------------------------------------------------------------------------
    def run(self, *, once: bool = False) -> int:
        since = self._initial_since()
        if once:
            return self._catch_up(since)
        attempts = 0
        backoff = 1.0
        while True:
            reason = None
            try:
                reason, frames = self._stream_once(since)
                if frames > 0:
                    backoff = 1.0
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    self.log("rrr_stream_unauthorized — API 키 설정을 확인하라(재시도 없음)")
                    return EXIT_CONFIG
                reason = f"http_{exc.code}"
            except (urllib.error.URLError, OSError) as exc:
                reason = f"net_{type(exc).__name__}"
            finally:
                self.proc.finalize()
                self._commit_cursor_if_settled()
            self.log(f"rrr_stream_disconnected reason={reason} cursor={self._newest_ref}")
            if self.max_reconnects is not None and attempts >= self.max_reconnects:
                return 0
            attempts += 1
            delay = min(MAX_BACKOFF_SEC, backoff) * (1.0 + (self.rand() - 0.5) * 0.4)
            self.sleep(delay)
            backoff = min(MAX_BACKOFF_SEC, backoff * 2)
            since = read_cursor(self.cursor_path) or since


def selftest() -> bool:
    got = list(parse_sse(iter(["id: 1-0", "event: zone", 'data: {"kind":"zone"}', "", ": keepalive t", ""])))
    assert got[0] == ("frame", {"id": "1-0", "event": "zone", "data": '{"kind":"zone"}'}) and got[1] == ("comment", "keepalive t")
    sub = {"symbols": ["336260"], "event_types": ["support_return", "tick"], "sessions": ["REG_KRX_NXT"], "expires_at": "2026-06-18T20:00:00"}
    u1 = build_stream_url("http://h", sub, since="$")
    assert "symbols=336260" in u1 and u1.startswith("http://h/api/events/stream?")
    assert "evts=" not in u1 and "tick" not in u1
    u2 = build_stream_url("http://h", dict(sub, event_types=["macro"]), since="0")
    assert "symbols=336260" in u2 and u2.startswith("http://h/api/events/stream?")
    return True


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="rrr-swing 인바운드 어댑터 — rrr 스트림 소스 (배달만, 판단 0)")
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--subscriptions", default="local/subscriptions.json")
    ap.add_argument("--delivery-log", default="local/delivery.jsonl")
    ap.add_argument("--cursor", default="local/events.cursor")
    ap.add_argument("--since", default=None, help="0 | <stream id> | $ (기본: 커서, 없으면 $)")
    ap.add_argument("--deliver", choices=["stdout", "file", "herdr"], default=None)
    ap.add_argument("--target", default=None)
    ap.add_argument("--inbox", default="local/inbox.jsonl")
    ap.add_argument("--once", action="store_true", help="GET /events 페이지로 밀린 구간만 처리하고 종료")
    ap.add_argument("--redeliver", action="store_true")
    ap.add_argument("--max-reconnects", type=int, default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        selftest(); print("inbound_rrr selftest ok"); return 0
    cfg = core.load_config(a.config)
    try:
        core.resolve_gw_config(cfg)
    except ValueError as exc:
        return log_exit(EXIT_CONFIG, "config", exc)
    inbound = cfg.get("inbound") or {}
    mode = a.deliver or inbound.get("deliver") or "stdout"
    # 기본값이 있으므로 target 은 항상 채워진다 — "target 없음" 분기는 도달할 수 없어 두지 않는다.
    target = a.target or inbound.get("target") or "rs-lead"
    if mode == "stdout":
        deliver = core.deliver_stdout
    elif mode == "file":
        deliver = lambda text: core.deliver_file(text, a.inbox)  # noqa: E731
        deliver.__name__ = "file"
    else:
        # 재시도 창은 테스트가 줄여 쓴다(라이브 기본 5분). config 키로 만들지 않는다.
        retry_sec = float(os.environ.get("RS_DELIVER_RETRY_SEC") or core.DELIVER_RETRY_WINDOW_SEC)
        deliver = lambda text: core.deliver_herdr(text, target, retry_window_sec=retry_sec)  # noqa: E731
        deliver.__name__ = f"herdr:{target}"
    sub = core.load_subscriptions(a.subscriptions)
    proc = core.Processor(cfg, sub, core.DeliveryLog(a.delivery_log), deliver, redeliver=a.redeliver)
    client = RrrStreamClient(cfg, sub, proc, cursor_path=a.cursor, max_reconnects=a.max_reconnects)
    client.since_override = a.since
    try:
        code = client.run(once=a.once)
    except core.HerdrBlockedError as exc:
        # 코어가 삼키는 것이 정상이다. 여기까지 올라왔다면 삼키지 못한 경로가 생긴 것이다.
        return log_exit(EXIT_UNDELIVERED, "undelivered_blocked", exc)
    except subprocess.CalledProcessError as exc:
        # herdr 가 남긴 말까지 실어야 "왜 배달이 안 됐는지"가 로그 한 줄로 끝난다.
        said = ((exc.stderr or "") + (exc.stdout or "")).strip()
        cmd = " ".join(str(c) for c in (exc.cmd or [])[:4])
        return log_exit(EXIT_UNDELIVERED, "undelivered", f"{cmd} → exit {exc.returncode}: {said}")
    except KeyboardInterrupt:
        return log_exit(EXIT_OK, "interrupted")
    except Exception as exc:  # noqa: BLE001 — 조용히 사라지는 경로를 남기지 않는다
        traceback.print_exc()
        return log_exit(EXIT_CRASHED, "crashed", f"{type(exc).__name__}: {exc}")
    reason = "catch_up_done" if a.once else "reconnect_limit"
    return log_exit(code, reason if code == EXIT_OK else "unauthorized")


if __name__ == "__main__":
    raise SystemExit(main())
