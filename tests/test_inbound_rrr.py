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
SUB = {"account_id": "acct1", "symbols": ["336260", "003230"], "event_types": ["support_return", "resistance_break", "tick"],
       "sessions": ["REG_KRX_NXT"], "expires_at": "2026-06-18T20:00:00"}
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
    def __init__(self, script, *, cursor=None, deliver=None, sub=SUB, max_reconnects=3):
        self.srv, self.base = make_server(script)
        self.tmp = tempfile.TemporaryDirectory()
        self.cursor_path = os.path.join(self.tmp.name, "events.cursor")
        if cursor is not None:
            open(self.cursor_path, "w").write(cursor)
        self.out = []
        self.sleeps = []
        cfg = json.loads(json.dumps(CFG)); cfg["gw"]["base_url"] = self.base
        self.log = core.DeliveryLog(os.path.join(self.tmp.name, "delivery.jsonl"))
        self.proc = core.Processor(cfg, sub, self.log, deliver=deliver or self.out.append, now_fn=lambda: NOW)
        self.client = rr.RrrStreamClient(cfg, sub, self.proc, cursor_path=self.cursor_path, sleep=self.sleeps.append,
                                         max_reconnects=max_reconnects, rand=lambda: 0.5)

    def close(self):
        self.srv.shutdown(); self.srv.server_close(); self.tmp.cleanup()

    def cursor(self):
        return open(self.cursor_path).read().strip() if os.path.exists(self.cursor_path) else None


class SseParser(unittest.TestCase):
    def test_frames_and_comments(self):
        lines = ["id: 1-0", "event: zone", 'data: {"a":1}', "", ": keepalive t", "", "id: 2-0", "event: error", 'data: {"reason":"redis"}', ""]
        got = list(rr.parse_sse(iter(lines)))
        self.assertEqual(got[0], ("frame", {"id": "1-0", "event": "zone", "data": '{"a":1}'}))
        self.assertEqual(got[1], ("comment", "keepalive t"))
        self.assertEqual(got[2][1]["event"], "error")


class UrlBuilding(unittest.TestCase):
    def test_query_from_subscription(self):
        url = rr.build_stream_url("http://h:1", "/events/stream", SUB, since="$")
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.assertEqual(q["since"], "$")
        self.assertEqual(q["symbols"], "336260,003230")
        self.assertEqual(q["kinds"], "zone")
        self.assertNotIn("evts", q)
        self.assertNotIn("tick", q.get("kinds", ""))

    def test_macro_subscription_includes_symbols_filter(self):
        url = rr.build_stream_url("http://h:1", "/events/stream", dict(SUB, event_types=["support_return", "macro"]), since="0")
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.assertIn("symbols", q)
        self.assertEqual(q["symbols"], "336260,003230")
        self.assertEqual(q["kinds"], "zone,macro")
        self.assertNotIn("evts", q)

    def test_heartbeat_entry_parsed_as_zone_and_filtered_locally(self):
        e = {"id": "1-0", "kind": "zone", "schema": "rrr_mon_alert_v1", "sym": "336260", "evt": "heartbeat",
             "px": "22300", "bar_ts": "2026-06-18T10:35:00", "as_of": "2026-06-18T10:35:07", "sess": "REG_KRX_NXT"}
        tagged = core.entry_to_tagged(e)
        self.assertIsNotNone(tagged)
        self.assertEqual(tagged["tag"], "#mon")
        self.assertEqual(tagged["kv"]["evt"], "heartbeat")

        # Subscribed to heartbeat -> matches
        sub_with_hb = dict(SUB, event_types=["heartbeat"])
        ok, reason = core.subscription_matches(sub_with_hb, tagged, NOW)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

        # Not subscribed to heartbeat -> unsubscribed
        sub_no_hb = dict(SUB, event_types=["support_return"])
        ok, reason = core.subscription_matches(sub_no_hb, tagged, NOW)
        self.assertFalse(ok)
        self.assertEqual(reason, "unsubscribed")


class StreamClient(unittest.TestCase):
    def test_replay_frames_then_reconnect_with_cursor(self):
        e1, e2, e3, e4 = entry(0), entry(1, evt="resistance_break"), entry(2, kind="tick"), entry(3, sym="000660")  # e4 미구독
        script = Script([
            {"body": [frame(e1), frame(e2), ": keepalive 2026-06-18T10:50:00\n\n", frame(e3), frame(e4)]},
            {"body": [": keepalive 2026-06-18T10:51:00\n\n"]},
            {"body": []},
        ])
        h = Harness(script, max_reconnects=2)
        try:
            rc = h.client.run()
            self.assertEqual(rc, 0)
            # 첫 접속: 커서 없음 → since=$ ; 재접속: since=마지막 처리 id
            self.assertEqual(script.seen[0][1]["since"], "$")
            hdrs = {k.lower(): v for k, v in script.seen[0][2].items()}
            self.assertEqual(hdrs.get("x-api-key"), "tkn")
            self.assertEqual(script.seen[1][1]["since"], e4["id"])
            self.assertEqual(h.cursor(), e4["id"])
            digests = [ln for ln in h.out if ln.startswith("[digest")]
            self.assertEqual(len(digests), 2)  # 10:35 zone×2, tick 10:40
            self.assertIn("n=2]", digests[0])
            rows = [json.loads(x) for x in open(h.log.path, encoding="utf-8")]
            self.assertIn(("skip", "unsubscribed"), [(r["kind"], r.get("reason")) for r in rows])
            self.assertTrue(all(r.get("source") == "rrr_stream" for r in rows if r.get("kind") != "market_check"))  # wake 행은 소스 무관
            self.assertEqual(h.sleeps, [1.0, 2.0])  # backoff 1→2 (rand=0.5 → 지터 0)
        finally:
            h.close()

    def test_explicit_since_override(self):
        script = Script([{"body": []}])
        h = Harness(script, max_reconnects=0)
        try:
            h.client.since_override = "0"
            h.client.run()
            self.assertEqual(script.seen[0][1]["since"], "0")
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

    def test_cursor_advances_only_after_processing(self):
        calls = {"n": 0}

        def flaky_deliver(text):
            if not text.startswith("[digest"):  # market-check wake 는 best-effort라 이 검사의 대상이 아니다
                return
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("pane gone")

        e1 = entry(0)
        script = Script([{"body": [frame(e1)]}])
        h = Harness(script, deliver=flaky_deliver, max_reconnects=0)
        try:
            # 다이제스트는 finalize/flush 때 배달된다 → 배달 실패는 run() 종료 시점에 드러난다
            with self.assertRaises(RuntimeError):
                h.client.run()
            self.assertIsNone(h.cursor())  # 처리(배달) 전에는 커서를 쓰지 않는다 → 재실행 시 재처리(at-least-once)
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

    def test_once_replays_pages_from_cursor_and_exits(self):
        e1, e2 = entry(0), entry(1, kind="tick")
        pages = [
            {"schema": "events.1.0", "entries": [e1], "next_since": e1["id"], "count": 1, "truncated": True},
            {"schema": "events.1.0", "entries": [e2], "next_since": e2["id"], "count": 1, "truncated": False},
        ]
        script = Script([], replay_pages=pages)
        h = Harness(script, cursor="1750210500000-0")
        try:
            self.assertEqual(h.client.run(once=True), 0)
            self.assertEqual([s[0] for s in script.seen], ["/api/events", "/api/events"])
            self.assertEqual(script.seen[0][1]["since"], "1750210500000-0")
            self.assertEqual(script.seen[1][1]["since"], e1["id"])
            self.assertEqual(h.cursor(), e2["id"])
            self.assertEqual(sum(1 for ln in h.out if ln.startswith("[digest")), 2)
        finally:
            h.close()

    def test_agent_blocked_cursor_does_not_advance(self):
        def blocked_deliver(text):
            if text.startswith("[digest"):
                raise core.HerdrBlockedError("agent_blocked")

        e1 = entry(0)
        script = Script([{"body": [frame(e1)]}])
        h = Harness(script, deliver=blocked_deliver, max_reconnects=0)
        try:
            h.client.run()
            # agent_blocked 거부 시 커서는 전진하지 않는다!
            self.assertIsNone(h.cursor())
        finally:
            h.close()

    def test_queue_overflow_advances_cursor_past_discarded_item(self):
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
            # 4 bars while blocked -> queue depth 3 exceeded -> oldest (e1) discarded!
            # Discarded item commits cursor so it won't be refetched!
            self.assertEqual(h.cursor(), e1["id"])
            self.assertEqual(h.proc.skipped_bars, 1)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
