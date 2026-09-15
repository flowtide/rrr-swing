#!/usr/bin/env python3
"""rrr-swing 인바운드 어댑터 — rrr 스트림 소스(기본). stdlib SSE 클라이언트 → 코어(inbound_core) → 배달.

결정 3: 배달만, 판단 0. `/context`·`/flow`·주문 호출 없음(허용: /api/health, /api/events, /api/events/stream).
- 소스: `GET {gw.base_url}/api/events/stream`(text/event-stream, X-API-Key). **쿼리 0개** —
  서버 필터도 since 도 보내지 않는다. 무엇을 볼지는 어댑터가 `local/watchlist.json` 으로
  **이벤트마다** 정한다(inbound_core.WatchList). 서버에 상태를 두면 접속 시점에 굳어,
  목록을 고쳐도 듣지 않는다.
- 커서 없음: **언제나 현시점부터** 받는다. 끊겼다 이어져도 그 사이의 엔트리는 오지 않으며,
  이는 대가가 아니라 선택이다 — 세션의 판단은 실시간이라 지나간 봉을 뒤늦게 받으면 쓸모가
  없고 현재로 착각할 위험만 남는다. 재접속은 로그에 `gap=not_replayed` 로 남긴다.
- 재접속: backoff 1→2→4→…→60s(지터 ±20%). 하트비트 부재 heartbeat_timeout_sec(기본 45) 초과 → 끊고 재접속.
  401 → 종료(exit 2, 설정 오류). 503(busy)·기타 → backoff. `event: error` 프레임 → 재접속.

종료 조건(이 목록이 전부다. 어떤 경로로 끝나든 마지막 줄에 `EXIT code=<n> reason=<이유>` 를 남긴다 —
조용히 사라지면 rs-lead 는 "이벤트가 없는 장"과 구분할 수 없다):

    0  ok          --max-reconnects 도달(테스트)
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


STREAM_PATH = "/api/events/stream"


def build_stream_url(base_url: str, path: str = STREAM_PATH) -> str:
    """`GET {base}/api/events/stream` — **쿼리 0개**.

    서버 필터(symbols·kinds)도 since 도 보내지 않는다. 공급자는 상태를 갖지 않으므로
    (`rrr/src/rrr/api/events.py`: "필터는 요청 파라미터뿐이고 서버는 구독을 저장하지 않는다")
    파라미터를 생략하면 전량을, since 를 생략하면 접속 이후만 밀어준다. 무엇을 볼지는
    어댑터가 감시 목록으로 이벤트마다 정한다 — 서버 쪽에 상태를 두면 접속 시점에 굳어
    목록을 고쳐도 듣지 않는다.
    """
    p = path if path.startswith("/") else f"/{path}"
    return f"{base_url.rstrip('/')}{p}"


# ---------------------------------------------------------------- 클라이언트

class RrrStreamClient:
    def __init__(self, cfg: dict, proc: core.Processor, *, sleep=None, max_reconnects: int | None = None,
                 rand=None, log=None):
        self.cfg = cfg
        self.proc = proc
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

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key, "Accept": "text/event-stream", "Cache-Control": "no-cache"}

    # -- 엔트리 처리 ----------------------------------------------------------------
    def _process_entry(self, entry: dict, *, source: str) -> None:
        # ref 는 배달 로그의 추적용이다. 재개 지점으로는 쓰지 않는다.
        self.proc.handle_event(core.entry_to_tagged(entry), source=source, ref=str(entry.get("id", "")))

    # -- 라이브 스트림 1회 접속 ------------------------------------------------------
    def _stream_once(self) -> tuple[str, int]:
        """반환: (종료 사유 eof|error|timeout, 받은 프레임 수). HTTPError·URLError 는 전파.

        재접속도 같은 무-쿼리 URL 이라 언제나 현시점부터 받는다 — 지나간 봉은 실시간 판단에
        쓸모가 없으므로 되짚지 않는다.
        """
        url = build_stream_url(self.base_url)
        req = urllib.request.Request(url, headers=self._headers())
        frames = 0
        with urllib.request.urlopen(req, timeout=self.heartbeat_timeout) as resp:
            try:
                for kind, payload in parse_sse(iter(resp.readline, b"")):
                    if kind == "comment":
                        self.proc.tick()
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
                    self.proc.tick()
            except (socket.timeout, TimeoutError):
                return "timeout", frames
        return "eof", frames

    # -- 루프 -------------------------------------------------------------------------
    def run(self) -> int:
        attempts = 0
        backoff = 1.0
        while True:
            reason = None
            try:
                reason, frames = self._stream_once()
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
            # 재접속은 현시점부터다. 끊긴 동안의 엔트리는 오지 않으므로, 남길 것은 유실량이
            # 아니라 '끊겼다'는 사실 자체다.
            self.log(f"rrr_stream_disconnected reason={reason} gap=not_replayed")
            if self.max_reconnects is not None and attempts >= self.max_reconnects:
                return 0
            attempts += 1
            delay = min(MAX_BACKOFF_SEC, backoff) * (1.0 + (self.rand() - 0.5) * 0.4)
            self.sleep(delay)
            backoff = min(MAX_BACKOFF_SEC, backoff * 2)


def build_probe_url(base_url: str, *, since: str, path: str = STREAM_PATH) -> str:
    """연결 테스트 전용 — `since` 를 받는 유일한 빌더.

    운전용 `build_stream_url` 과 **일부러 나눠 둔다**. 한 함수에 선택 인자로 두면 운전 경로가
    언젠가 그것을 집어 들고, 그러면 지나간 봉이 현재로 배달된다.
    """
    p = path if path.startswith("/") else f"/{path}"
    return f"{base_url.rstrip('/')}{p}?{urllib.parse.urlencode({'since': since})}"


def probe(base_url: str, api_key: str, *, since: str = "0", limit: int = 10,
          seconds: float = 30.0, out=print) -> int:
    """연결 테스트 — 스트림을 열어 받은 것을 그대로 보여 주고 끝난다.

    운전 중에는 언제나 현시점부터 받지만, 연결이 살아 있는지 볼 때는 이야기가 다르다.
    `$` 로 열면 장이 조용한 동안 아무것도 오지 않아 **"연결이 안 됐다"와 "이벤트가 없다"를
    가를 수 없다.** 과거 지점부터 열면 즉시 흘러나오므로 그 둘이 갈린다.

    이 함수는 감시 목록도 배달 경로도 건드리지 않는다 — 연결만 본다. 그래서 연결 테스트가
    세션에 무엇도 밀어 넣지 못한다.

    반환: 0 = 스트림이 열렸고 무언가 도착했다. 그 밖 = 열리지 않았거나 끝까지 조용했다.
    """
    url = build_probe_url(base_url, since=since)
    req = urllib.request.Request(url, headers={"X-API-Key": api_key, "Accept": "text/event-stream",
                                               "Cache-Control": "no-cache"})
    out(f"probe {url}")
    seen = 0
    deadline = time.monotonic() + seconds
    try:
        with urllib.request.urlopen(req, timeout=seconds) as resp:
            out(f"HTTP {resp.status} {resp.headers.get('Content-Type')}")
            for kind, payload in parse_sse(iter(resp.readline, b"")):
                if time.monotonic() > deadline:
                    break
                if kind == "comment":
                    out(f"  keepalive {payload}")
                    continue
                try:
                    e = json.loads(payload.get("data") or "{}")
                except ValueError:
                    e = {}
                out(f"  {payload.get('id','-')} {e.get('kind','-')} "
                    f"{e.get('sym','-')} {e.get('evt') or e.get('slot') or ''} sess={e.get('sess','-')}")
                seen += 1
                if seen >= limit:
                    break
    except urllib.error.HTTPError as exc:
        out(f"ERROR HTTP {exc.code} — 키·주소를 확인하라")
        return EXIT_CONFIG if exc.code == 401 else EXIT_CRASHED
    except (urllib.error.URLError, OSError) as exc:
        out(f"ERROR {type(exc).__name__}: {exc} — 게이트웨이에 닿지 않는다")
        return EXIT_CRASHED
    out(f"받은 이벤트 {seen}건")
    if seen == 0:
        out("스트림은 열렸으나 아무것도 오지 않았다 — --since 를 더 과거로 두고 다시 보라")
        return EXIT_CRASHED
    return EXIT_OK


def selftest() -> bool:
    got = list(parse_sse(iter(["id: 1-0", "event: zone", 'data: {"kind":"zone"}', "", ": keepalive t", ""])))
    assert got[0] == ("frame", {"id": "1-0", "event": "zone", "data": '{"kind":"zone"}'}) and got[1] == ("comment", "keepalive t")
    # 쿼리가 하나라도 붙으면 서버가 필터를 걸어 어댑터의 감시 목록이 무의미해진다.
    assert build_stream_url("http://h") == "http://h/api/events/stream"
    return True


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="rrr-swing 인바운드 어댑터 — rrr 스트림 소스 (배달만, 판단 0)")
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--watchlist", default="local/watchlist.json")
    ap.add_argument("--delivery-log", default="local/delivery.jsonl")
    ap.add_argument("--deliver", choices=["stdout", "file", "herdr"], default=None)
    ap.add_argument("--target", default=None)
    ap.add_argument("--inbox", default="local/inbox.jsonl")
    ap.add_argument("--redeliver", action="store_true")
    ap.add_argument("--max-reconnects", type=int, default=None)
    ap.add_argument("--selftest", action="store_true")
    # 커서를 받는 유일한 인자다. 값 자체가 시작 지점이므로 운전용 플래그와 섞일 자리가 없다.
    ap.add_argument("--probe", metavar="SINCE", default=None,
                    help="연결 테스트: 이 지점부터 열어 받은 것을 보여 주고 끝난다(0 | <stream id>). 배달 0")
    a = ap.parse_args(argv)
    if a.selftest:
        selftest(); print("inbound_rrr selftest ok"); return 0
    if a.probe is not None:
        # 연결만 본다 — 감시 목록도 배달 로그도 열지 않으므로 세션에 무엇도 들어가지 않는다.
        cfg = core.load_config(a.config)
        gw = core.resolve_gw_config(cfg)
        return probe(gw.base_url, gw.api_key, since=a.probe)
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
    watch = core.WatchList(a.watchlist)
    proc = core.Processor(cfg, watch, core.DeliveryLog(a.delivery_log), deliver, redeliver=a.redeliver)
    client = RrrStreamClient(cfg, proc, max_reconnects=a.max_reconnects)
    try:
        code = client.run()
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
    reason = "reconnect_limit"
    return log_exit(code, reason if code == EXIT_OK else "unauthorized")


if __name__ == "__main__":
    raise SystemExit(main())
