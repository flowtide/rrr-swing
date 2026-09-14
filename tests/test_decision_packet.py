"""decision_packet — GET 전용 결정 패킷 조립. 스레드 http.server 가짜 rrr(127.0.0.1). 판단·점수 0."""
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import decision_packet as dp  # noqa: E402

# `/context` 의 symbol 은 **객체**다(라이브 확인: {"code","name","market"}). flow·momentum 은 문자열.
# 픽스처가 문자열이라 테스트가 실제 모양을 한 번도 겪지 못했고, context 는 라이브에서 매 조회마다
# symbol_mismatch 로 오탐했다.
CONTEXT = {"schema_version": "1.0", "symbol": {"code": "336260", "name": "네오팜", "market": None},
           "created_at": "2026-06-18T10:36:00",
           "supply": {"sampled_at": "2026-06-18T10:35:50", "age_sec": 10, "program_net_eok": "12.3"},
           "market_context": {"sampled_at": "2026-06-18T10:35:00", "age_sec": 60},
           "daily_candle": {"date": "2026-06-18", "close": 22300}}
FLOW = {"schema_version": "flow.1.0", "symbol": "336260", "created_at": "2026-06-18T10:36:00",
        "freshness": {"investor_freshness": "sampled", "investor_age_sec": 40},
        "bucket_bars": [{"t": "2026-06-18T10:35:00", "partial": True}], "program_cum": [{"t": "2026-06-18T10:35:00", "partial": True}], "investor_cum": []}
MOMENTUM = {"schema_version": "momentum.1.0", "symbol": "336260", "trade_date": "2026-06-17", "age_days": 1, "momentum": {}}


class Script:
    def __init__(self, responses):
        self.responses = dict(responses)  # path → (status, payload)
        self.seen = []
        self.lock = threading.Lock()


def make_server(script):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            path = urlparse(self.path).path
            with script.lock:
                script.seen.append((path, dict(self.headers)))
            status, payload = script.responses.get(path, (404, {"detail": "not found"}))
            body = json.dumps(payload).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_POST(self):  # GET 전용 — 어떤 경로도 POST 하지 않는다
            self.send_response(405); self.send_header("Content-Length", "0"); self.end_headers()

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


OK = {"/api/health": (200, {"ok": True}), "/api/stocks/336260/context": (200, CONTEXT), "/api/stocks/336260/flow": (200, FLOW), "/api/stocks/336260/momentum": (200, MOMENTUM)}


class Packet(unittest.TestCase):
    def run_packet(self, responses, **kw):
        script = Script(responses)
        srv, base = make_server(script)
        try:
            with tempfile.TemporaryDirectory() as d:
                packet, path, rc = dp.build_and_save("336260", base_url=base, token="tkn", out_dir=d, bar_ts="2026-06-18T10:35:00",
                                                     now="2026-06-18T10:36:05", **kw)
                with open(path, encoding="utf-8") as f:
                    saved = json.load(f)
        finally:
            srv.shutdown(); srv.server_close()
        return packet, saved, rc, script

    def test_normal_packet_is_verbatim_plus_summary_and_saved(self):
        packet, saved, rc, script = self.run_packet(OK)
        self.assertEqual(rc, 0)
        self.assertEqual(packet["schema_version"], dp.PACKET_SCHEMA_VERSION)
        self.assertEqual(packet["sym"], "336260"); self.assertEqual(packet["bar_ts"], "2026-06-18T10:35:00"); self.assertEqual(packet["fetched_at"], "2026-06-18T10:36:05")
        self.assertTrue(packet["packet_id"])
        self.assertEqual(packet["sources"]["context"]["data"], CONTEXT)  # 원문 그대로
        self.assertEqual(packet["sources"]["flow"]["data"], FLOW)
        self.assertNotIn("momentum", packet["sources"])  # 선택(기본 off)
        self.assertTrue(packet["summary"]["schema_ok"])
        fr = packet["summary"]["freshness"]
        self.assertEqual(fr["context.supply.age_sec"], 10)
        self.assertEqual(fr["flow.freshness.investor_freshness"], "sampled")
        self.assertIn("flow.bucket_bars[0].partial", fr)
        self.assertEqual(packet["summary"]["warnings"], ["context:quote_freshness_not_exposed"])
        self.assertEqual(saved["packet_id"], packet["packet_id"])
        self.assertTrue(os.path.basename(packet["path"]).endswith("-336260.json"))
        # GET 전용 + X-API-Key + /api/health 1회
        self.assertEqual([p for p, _ in script.seen], ["/api/health", "/api/stocks/336260/context", "/api/stocks/336260/flow"])
        self.assertTrue(all(h.get("X-Api-Key") == "tkn" or h.get("X-API-Key") == "tkn" for _, h in script.seen))
        self.assertTrue(all("Authorization" not in h for _, h in script.seen))
        for key in ("score", "recommendation", "action"):
            self.assertNotIn(key, packet)

    def test_momentum_optional_and_freshness_days(self):
        packet, _, rc, script = self.run_packet(OK, with_momentum=True)
        self.assertEqual(rc, 0)
        self.assertEqual(packet["sources"]["momentum"]["data"], MOMENTUM)
        self.assertEqual(packet["summary"]["freshness"]["momentum.age_days"], 1)

    def test_required_source_503_marks_failure_and_exit_1(self):
        responses = dict(OK); responses["/api/stocks/336260/flow"] = (503, {"detail": "unavailable"})
        packet, saved, rc, _ = self.run_packet(responses)
        self.assertEqual(rc, 1)
        self.assertFalse(packet["sources"]["flow"]["success"])
        self.assertEqual(packet["sources"]["flow"]["status_code"], 503)
        self.assertIn("flow:fetch_failed", packet["summary"]["warnings"])
        self.assertFalse(packet["summary"]["required_ok"])
        self.assertEqual(saved["summary"]["required_ok"], False)  # 실패해도 패킷은 남긴다(증거)

    def test_symbol_is_matched_in_both_shapes(self):
        """context 는 객체, flow·momentum 은 문자열 — 둘 다 같은 종목으로 읽어야 한다."""
        self.assertEqual(dp.payload_symbol({"symbol": {"code": "336260", "name": "x"}}), "336260")
        self.assertEqual(dp.payload_symbol({"symbol": "336260"}), "336260")
        self.assertIsNone(dp.payload_symbol({"symbol": None}))
        self.assertIsNone(dp.payload_symbol(None))

    def test_object_symbol_does_not_raise_mismatch(self):
        """오탐이 없어야 한다 — 경고가 늘 떠 있으면 경고 채널이 무뎌진다."""
        q = dp.source_quality("context", CONTEXT, True, "336260")
        self.assertNotIn("context:symbol_mismatch", q["warnings"])

    def test_real_symbol_mismatch_is_still_caught(self):
        """오탐을 없애다 검사를 죽이지 않는다 — 다른 종목이면 여전히 잡혀야 한다."""
        other = dict(CONTEXT, symbol={"code": "000660", "name": "y", "market": None})
        q = dp.source_quality("context", other, True, "336260")
        self.assertIn("context:symbol_mismatch", q["warnings"])
        q2 = dp.source_quality("flow", dict(FLOW, symbol="000660"), True, "336260")
        self.assertIn("flow:symbol_mismatch", q2["warnings"])

    def test_schema_mismatch_is_flagged_not_hidden(self):
        responses = dict(OK); responses["/api/stocks/336260/context"] = (200, dict(CONTEXT, schema_version="0.9"))
        packet, _, rc, _ = self.run_packet(responses)
        self.assertEqual(rc, 0)
        self.assertFalse(packet["summary"]["schema_ok"])
        self.assertIn("context:schema_mismatch", packet["summary"]["warnings"])
        self.assertEqual(packet["sources"]["context"]["quality"]["schema"], {"expected": "1.0", "actual": "0.9", "match": False})

    def test_stale_freshness_warns(self):
        responses = dict(OK); responses["/api/stocks/336260/flow"] = (200, dict(FLOW, freshness={"investor_freshness": "stale", "investor_age_sec": None}))
        packet, _, _, _ = self.run_packet(responses)
        self.assertIn("flow:freshness.investor_freshness=stale", packet["summary"]["warnings"])
        self.assertIn("flow:freshness.investor_age_sec=missing", packet["summary"]["warnings"])


if __name__ == "__main__":
    unittest.main()
