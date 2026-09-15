"""inbound_rrr — stdlib SSE 클라이언트. 스레드 http.server 가짜 rrr(네트워크=127.0.0.1 만)."""
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import inbound_core as core  # noqa: E402
import inbound_rrr as rr  # noqa: E402

NOW = datetime(2026, 6, 18, 10, 50)
WATCH = {"336260": {}, "003230": {}}
CFG = {"account_id": "acct1", "operator_chat_id": "111", "inbound": {"quiet_sec": 30, "inject_max_chars": 1800, "heartbeat_timeout_sec": 0.4},
       "gw": {"base_url": "", "api_key": "tkn"}}


def entry(i, kind="zone", **over):
    base = {"zone": {"schema": "rrr_mon_alert_v1", "sym": "336260", "bar_ts": "2026-06-18T10:35:00", "evt": "support_return", "lvl": "s1",
                     "lvl_px": "22150", "px": "22300", "as_of": "2026-06-18T10:35:07", "sess": "REG_KRX_NXT"},
            "tick": {"schema": "rrr_mon_tick_v1", "sym": "003230", "bar_ts": "2026-06-18T10:40:00", "o": "1", "h": "2", "l": "1", "c": "2", "v": "3",
                     "watch_id": "7", "as_of": "2026-06-18T10:45:02"},
            "macro": {"schema": "rrr_mon_macro_v1", "slot": "h1044", "sess": "REG_KRX_NXT", "as_of": "2026-06-18T10:44:00"}}[kind]
    e = {"id": f"{1750210507000 + i}-0", "kind": kind, "trade_date": "2026-06-18", **base, **over}
    return e


def frame(e):
    return f"id: {e['id']}\nevent: {e['kind']}\ndata: {json.dumps(e, ensure_ascii=False, separators=(',', ':'))}\n\n"


class Script:
    """연결마다 실행할 대본. 각 항목: {"status": 200, "body": [chunk,...], "sleep_between": s} 또는 {"status": 401|503}."""

    def __init__(self, connections, replay_pages=None):
        self.connections = list(connections)
        self.replay_pages = list(replay_pages or [])
        self.seen = []  # (path, query dict, headers)
        self.lock = threading.Lock()


def make_server(script: Script):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            with script.lock:
                script.seen.append((parsed.path, q, dict(self.headers)))
            if parsed.path == "/api/health":
                body = b'{"status":"ok"}'
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            if parsed.path == "/api/events":
                with script.lock:
                    page = script.replay_pages.pop(0) if script.replay_pages else {"schema": "events.1.0", "entries": [], "next_since": q.get("since", "0"), "count": 0, "truncated": False}
                body = json.dumps(page).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            with script.lock:
                step = script.connections.pop(0) if script.connections else {"status": 200, "body": []}
            status = step.get("status", 200)
            if status != 200:
                body = json.dumps({"detail": "events stream busy" if status == 503 else "authentication required"}).encode()
                self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache"); self.end_headers()
            for chunk in step.get("body", []):
                if isinstance(chunk, (int, float)):
                    time.sleep(chunk); continue
                try:
                    self.wfile.write(chunk.encode()); self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
            # 연결 종료(EOF)

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class Harness:
    def __init__(self, script, *, deliver=None, watch=None, max_reconnects=3):
        self.srv, self.base = make_server(script)
        self.tmp = tempfile.TemporaryDirectory()
        self.watch_path = os.path.join(self.tmp.name, "watchlist.json")
        with open(self.watch_path, "w", encoding="utf-8") as f:
            json.dump(WATCH if watch is None else watch, f)
        self.out = []
        self.sleeps = []
        cfg = json.loads(json.dumps(CFG)); cfg["gw"]["base_url"] = self.base
        self.log = core.DeliveryLog(os.path.join(self.tmp.name, "delivery.jsonl"))
        self.proc = core.Processor(cfg, core.WatchList(self.watch_path), self.log,
                                   deliver=deliver or self.out.append, now_fn=lambda: NOW)
        self.client = rr.RrrStreamClient(cfg, self.proc, sleep=self.sleeps.append,
                                         max_reconnects=max_reconnects, rand=lambda: 0.5)

    def close(self):
        self.srv.shutdown(); self.srv.server_close(); self.tmp.cleanup()


class SseParser(unittest.TestCase):
    def test_frames_and_comments(self):
        lines = ["id: 1-0", "event: zone", 'data: {"a":1}', "", ": keepalive t", "", "id: 2-0", "event: error", 'data: {"reason":"redis"}', ""]
        got = list(rr.parse_sse(iter(lines)))
        self.assertEqual(got[0], ("frame", {"id": "1-0", "event": "zone", "data": '{"a":1}'}))
        self.assertEqual(got[1], ("comment", "keepalive t"))
        self.assertEqual(got[2][1]["event"], "error")


class UrlHasNoQuery(unittest.TestCase):
    """서버에 아무것도 맡기지 않는다 — 필터도, 재개 지점도.

    쿼리가 하나라도 붙으면 서버가 접속 시점의 값으로 걸러 버리고, 그 값은 접속이 끝날 때까지
    굳는다. 그러면 감시 목록을 고쳐도 듣지 않는다 — 2026-09-15 에 하루를 눈멀게 한 구조다.
    """

    def test_stream_url_carries_no_parameters(self):
        self.assertEqual(rr.build_stream_url("http://h:1"), "http://h:1/api/events/stream")
        self.assertEqual(urlparse(rr.build_stream_url("http://h:1")).query, "")

    def test_trailing_slash_does_not_double_up(self):
        self.assertEqual(rr.build_stream_url("http://h:1/"), "http://h:1/api/events/stream")

    def test_heartbeat_entry_is_delivered_when_the_symbol_is_watched(self):
        """유형 필터가 없어졌다 — heartbeat 도 목록에 있으면 온다."""
        e = {"id": "1-0", "kind": "zone", "schema": "rrr_mon_alert_v1", "sym": "336260", "evt": "heartbeat",
             "px": "22300", "bar_ts": "2026-06-18T10:35:00", "as_of": "2026-06-18T10:35:07", "sess": "REG_KRX_NXT"}
        tagged = core.entry_to_tagged(e)
        self.assertEqual(tagged["kv"]["evt"], "heartbeat")
        self.assertEqual(core.watchlist_matches(WATCH, tagged), (True, ""))
        self.assertEqual(core.watchlist_matches({"000660": {}}, tagged), (False, "unwatched"))


class ProbeIsTheOnlyPlaceACursorLives(unittest.TestCase):
    """`--probe` 는 연결 테스트 전용이며, `since` 를 쓰는 유일한 자리다.

    운전 중에는 언제나 현시점부터 받는다 — 지나간 봉은 실시간 판단에 쓸모가 없기 때문이다.
    그런데 연결이 살아 있는지 보려면 이야기가 다르다: `$` 로 열면 장이 조용한 동안 아무것도
    오지 않아 "연결이 안 됐다"와 "이벤트가 없다"를 가를 수 없다. 과거 지점부터 열면 즉시
    흘러나오므로 그 둘이 갈린다.

    그래서 능력을 남기되 **배달 경로에서 떼어 둔다** — probe 는 Processor 를 만들지 않으므로
    무엇도 세션에 넣지 못한다. 문서로만 "테스트 때만 쓰라" 고 적으면 그건 규칙이 아니다.
    """

    def test_url_carries_since_and_nothing_else(self):
        self.assertEqual(rr.build_probe_url("http://h:1", since="0"),
                         "http://h:1/api/events/stream?since=0")
        q = parse_qs(urlparse(rr.build_probe_url("http://h:1", since="1750210507000-0")).query)
        self.assertEqual(sorted(q), ["since"], "probe 에 필터가 섞였다")

    def test_runtime_url_has_no_way_to_pass_a_cursor(self):
        """운전용 URL 빌더에는 since 를 받을 자리가 아예 없다 — 넘기려 해도 TypeError 다."""
        with self.assertRaises(TypeError):
            rr.build_stream_url("http://h:1", since="0")

    def test_reports_what_arrives_and_exits(self):
        e1, e2 = entry(0), entry(1, sym="000660")     # 감시 목록과 무관하게 전부 보여 준다
        script = Script([{"body": [frame(e1), ": keepalive t\n\n", frame(e2)]}])
        srv, base = make_server(script)
        try:
            out = []
            rc = rr.probe(base, "tkn", since="0", limit=2, out=out.append)
            self.assertEqual(rc, 0, out)
            self.assertEqual(script.seen[0][1], {"since": "0"})
            body = "\n".join(out)
            self.assertIn(e1["id"], body)
            self.assertIn("000660", body, "감시 목록 밖 종목도 보여야 연결 확인이 된다")
        finally:
            srv.shutdown(); srv.server_close()

    def test_never_touches_the_delivery_path(self):
        """probe 가 배달 경로를 타면 테스트 한 번이 세션에 다이제스트를 밀어 넣는다."""
        import inspect
        src = inspect.getsource(rr.probe)
        for forbidden in ("Processor", "deliver", "DeliveryLog", "WatchList"):
            self.assertNotIn(forbidden, src, f"probe 가 {forbidden} 를 건드린다")

    def test_start_sh_never_reaches_for_the_probe(self):
        """기동 스크립트가 probe 를 부르면 그 순간 커서가 운전 경로로 들어온다.

        "테스트 때만 쓴다" 를 문서에만 적으면 그건 규칙이 아니다 — 부를 수 있는 자리에서
        부르지 않는지를 본다.
        """
        with open(os.path.join(ROOT, "bin", "start.sh"), encoding="utf-8") as f:
            src = f.read()
        for token in ("--probe", "--since", "build_probe_url"):
            self.assertNotIn(token, src, f"start.sh 가 {token} 를 쓴다")

    def test_reports_an_unreachable_stream(self):
        script = Script([{"status": 401}])
        srv, base = make_server(script)
        try:
            out = []
            self.assertNotEqual(rr.probe(base, "tkn", since="0", limit=1, out=out.append), 0)
            self.assertIn("401", "\n".join(out))
        finally:
            srv.shutdown(); srv.server_close()


class StreamClient(unittest.TestCase):
    def test_reconnect_does_not_replay_the_gap(self):
        """재접속도 무쿼리다 — 끊긴 동안의 엔트리는 돌아오지 않는다.

        커서를 지운 대가가 여기에 있다. 대가 자체를 테스트로 적어 둔다: 두 번째 접속 요청에
        since 가 실리지 않는다는 것은 곧 그 구간을 포기했다는 뜻이고, 로그의
        `gap=not_replayed` 가 그 사실을 남기는 유일한 자리다.
        """
        e1, e2, e3 = entry(0), entry(1, evt="resistance_break"), entry(3, sym="000660")  # e3 미감시
        script = Script([
            {"body": [frame(e1), frame(e2), ": keepalive 2026-06-18T10:50:00\n\n", frame(e3)]},
            {"body": [": keepalive 2026-06-18T10:51:00\n\n"]},
            {"body": []},
        ])
        h = Harness(script, max_reconnects=2)
        try:
            self.assertEqual(h.client.run(), 0)
            for i, (_path, q, _hdr) in enumerate(script.seen):
                self.assertEqual(q, {}, f"{i}번째 접속에 쿼리가 실렸다: {q}")
            hdrs = {k.lower(): v for k, v in script.seen[0][2].items()}
            self.assertEqual(hdrs.get("x-api-key"), "tkn")
            self.assertEqual(sum(1 for ln in h.out if ln.startswith("[digest")), 1)
            rows = [json.loads(x) for x in open(h.log.path, encoding="utf-8")]
            self.assertIn(("skip", "unwatched"), [(r["kind"], r.get("reason")) for r in rows])
            self.assertEqual(h.sleeps, [1.0, 2.0])   # backoff 1→2 (rand=0.5 → 지터 0)
        finally:
            h.close()

    def test_401_stops_with_exit_2(self):
        script = Script([{"status": 401}])
        h = Harness(script, max_reconnects=5)
        try:
            self.assertEqual(h.client.run(), 2)
            self.assertEqual(len(script.seen), 1)
        finally:
            h.close()

    def test_503_backs_off_and_retries(self):
        script = Script([{"status": 503}, {"status": 503}, {"body": [frame(entry(0))]}])
        h = Harness(script, max_reconnects=3)
        try:
            h.client.run()
            self.assertGreaterEqual(len(script.seen), 3)
            self.assertEqual(h.sleeps[:2], [1.0, 2.0])
            self.assertEqual(sum(1 for ln in h.out if ln.startswith("[digest")), 1)
        finally:
            h.close()

    def test_heartbeat_timeout_reconnects(self):
        script = Script([{"body": [": keepalive t\n\n", 1.5]}, {"body": [frame(entry(0))]}])  # 첫 연결은 0.4s 넘게 침묵
        h = Harness(script, max_reconnects=1)
        try:
            h.client.run()
            self.assertEqual(len(script.seen), 2)
            self.assertEqual(sum(1 for ln in h.out if ln.startswith("[digest")), 1)
        finally:
            h.close()

    def test_error_frame_triggers_reconnect(self):
        script = Script([{"body": ['event: error\ndata: {"reason":"redis"}\n\n']}, {"body": [frame(entry(0))]}])
        h = Harness(script, max_reconnects=1)
        try:
            h.client.run()
            self.assertEqual(len(script.seen), 2)
        finally:
            h.close()

    def test_market_check_wake_on_keepalive_shares_core(self):
        # rrr 소스도 같은 core.Processor.tick 을 타므로 창 안(NOW=10:50)에서 keepalive 만 와도 wake 1줄. 다이제스트와 독립
        e1 = entry(0)
        script = Script([{"body": [": keepalive 2026-06-18T10:50:00\n\n", frame(e1)]}])
        h = Harness(script, max_reconnects=0)
        try:
            h.client.run()
            self.assertEqual([ln for ln in h.out if ln.startswith("[market-check")], ["[market-check ts=10:50]"])
            self.assertEqual(sum(1 for ln in h.out if ln.startswith("[digest")), 1)
            rows = [json.loads(x) for x in open(h.log.path, encoding="utf-8") if x.strip()]
            self.assertEqual([r["slot"] for r in rows if r["kind"] == "market_check"], ["10:30"])
        finally:
            h.close()

    def test_agent_blocked_keeps_the_digest_queued(self):
        def blocked_deliver(text):
            if text.startswith("[digest"):
                raise core.HerdrBlockedError("agent_blocked")

        e1 = entry(0)
        script = Script([{"body": [frame(e1)]}])
        h = Harness(script, deliver=blocked_deliver, max_reconnects=0)
        try:
            h.client.run()
            # 거부된 다이제스트는 버려지지 않고 큐에 남는다(깊이 3).
            self.assertEqual(len(h.proc.delivery_queue), 1)
        finally:
            h.close()

    def test_queue_overflow_discards_the_oldest_and_marks_it(self):
        def blocked_deliver(text):
            if text.startswith("[digest"):
                raise core.HerdrBlockedError("agent_blocked")

        # 4 different bars: 10:00, 10:05, 10:10, 10:15
        e1 = entry(0, bar_ts="2026-06-18T10:00:00")
        e2 = entry(1, bar_ts="2026-06-18T10:05:00")
        e3 = entry(2, bar_ts="2026-06-18T10:10:00")
        e4 = entry(3, bar_ts="2026-06-18T10:15:00")
        script = Script([{"body": [frame(e1), frame(e2), frame(e3), frame(e4)]}])
        h = Harness(script, deliver=blocked_deliver, max_reconnects=0)
        try:
            h.client.run()
            # 막힌 채 4봉 → 큐 깊이 3 초과 → 가장 오래된 e1 폐기. 버린 사실은 숨기지 않는다:
            # 다음 다이제스트 머리에 [생략 n봉] 이 붙는다.
            self.assertEqual(h.proc.skipped_bars, 1)
            rows = [json.loads(x) for x in open(h.log.path, encoding="utf-8")]
            self.assertIn("delivery_queue_overflow", [r.get("reason") for r in rows])
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
