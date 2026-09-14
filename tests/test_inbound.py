"""inbound_core 단위 테스트 — 네트워크 0, stdlib unittest."""
import json
import os
import sys
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import inbound_core as ib  # noqa: E402

MON = ("📈 두산퓨얼셀(336260) R1 22,150 저항 돌파 ↑ [강함]\n"
       "↳ 전고점 + 거래량 매물대\n"
       "#mon schema=rrr_mon_alert_v1 evt=resistance_break sym=336260 lvl=r1 lvl_px=22150 px=22300 "
       "bar_ts=2026-06-18T10:35:00 as_of=2026-06-18T10:35:07 str=strong sess=REG_KRX_NXT")
TICK = ("🔁 [워치틱] 003230 삼양 · 새 확정봉 09:45\n"
        "#mon-tick schema=rrr_mon_tick_v1 sym=003230 bar_ts=2026-08-17T09:45:00 o=1240000 h=1250000 "
        "l=1238000 c=1247000 v=12345 watch_id=7")
MACRO = ("🌐 매크로\n#macro schema=rrr_mon_macro_v1 sess=REG_KRX_NXT as_of=2026-06-29T15:01:36 vix=18.4")

CFG = {"operator_chat_id": "111", "signal_chat_id": "-500", "signal_sender_id": "777", "account_id": "acct1"}


class ParseTagLine(unittest.TestCase):
    def test_mon_tag_last_line_is_authority(self):
        t = ib.parse_tag_line(MON)
        self.assertEqual(t["tag"], "#mon")
        self.assertEqual(t["kv"]["evt"], "resistance_break")
        self.assertEqual(t["kv"]["sym"], "336260")
        self.assertEqual(t["kv"]["bar_ts"], "2026-06-18T10:35:00")
        self.assertTrue(t["line"].startswith("#mon schema="))

    def test_tick_tag_is_not_matched_by_mon_prefix(self):
        t = ib.parse_tag_line(TICK)
        self.assertEqual(t["tag"], "#mon-tick")
        self.assertEqual(t["kv"]["sym"], "003230")

    def test_macro_tag(self):
        self.assertEqual(ib.parse_tag_line(MACRO)["tag"], "#macro")

    def test_wrong_schema_is_bad_tag(self):
        self.assertIsNone(ib.parse_tag_line("x\n#mon schema=other evt=support_return sym=1 lvl=s1 lvl_px=1 px=1 bar_ts=t as_of=t"))

    def test_missing_required_key_is_bad_tag(self):
        self.assertIsNone(ib.parse_tag_line("x\n#mon schema=rrr_mon_alert_v1 evt=support_return sym=336260"))

    def test_no_tag_line_is_none(self):
        self.assertIsNone(ib.parse_tag_line("그냥 문장"))

    def test_unknown_keys_ignored_and_values_preserved_verbatim(self):
        t = ib.parse_tag_line(MON + " zz=1 str=strong")
        self.assertEqual(t["kv"]["str"], "strong")
        self.assertIn("zz", t["kv"])  # 알 수 없는 key 는 버리지 않고 보존(값 변형 0)


class SubscriptionMatch(unittest.TestCase):
    SUB = {"symbols": ["336260"], "event_types": ["resistance_break", "macro"], "sessions": ["REG_KRX_NXT"],
           "expires_at": "2026-06-30T20:00:00"}
    NOW = datetime(2026, 6, 18, 10, 36)

    def test_match(self):
        ok, reason = ib.subscription_matches(self.SUB, ib.parse_tag_line(MON), self.NOW)
        self.assertTrue(ok); self.assertEqual(reason, "")

    def test_unsubscribed_symbol(self):
        t = ib.parse_tag_line(MON.replace("sym=336260", "sym=000660"))
        self.assertEqual(ib.subscription_matches(self.SUB, t, self.NOW), (False, "unsubscribed"))

    def test_unsubscribed_event_type(self):
        t = ib.parse_tag_line(MON.replace("evt=resistance_break", "evt=support_break"))
        self.assertEqual(ib.subscription_matches(self.SUB, t, self.NOW), (False, "unsubscribed"))

    def test_session_mismatch(self):
        t = ib.parse_tag_line(MON.replace("sess=REG_KRX_NXT", "sess=POST_NXT"))
        self.assertEqual(ib.subscription_matches(self.SUB, t, self.NOW), (False, "session_mismatch"))

    def test_session_unknown_when_sess_absent(self):
        t = ib.parse_tag_line(MON.replace(" sess=REG_KRX_NXT", ""))
        self.assertEqual(ib.subscription_matches(self.SUB, t, self.NOW), (False, "session_unknown"))

    def test_expired_subscription(self):
        ok, reason = ib.subscription_matches(self.SUB, ib.parse_tag_line(MON), datetime(2026, 7, 1, 9, 0))
        self.assertEqual((ok, reason), (False, "subscription_expired"))

    def test_tick_needs_explicit_tick_event_type(self):
        t = ib.parse_tag_line(TICK)
        sub = dict(self.SUB, symbols=["003230"])
        self.assertEqual(ib.subscription_matches(sub, t, self.NOW), (False, "unsubscribed"))
        sub2 = dict(sub, event_types=["tick"])
        self.assertEqual(ib.subscription_matches(sub2, t, self.NOW), (True, ""))

    def test_macro_needs_explicit_macro_event_type(self):
        t = ib.parse_tag_line(MACRO)
        self.assertEqual(ib.subscription_matches(self.SUB, t, self.NOW), (True, ""))
        self.assertEqual(ib.subscription_matches(dict(self.SUB, event_types=["resistance_break"]), t, self.NOW),
                         (False, "unsubscribed"))


if __name__ == "__main__":
    unittest.main()


FIX = os.path.join(ROOT, "tests", "fixtures")
CFG2 = dict(CFG, inbound={"quiet_sec": 30, "inject_max_chars": 1800, "deliver": "stdout"})


def _sub():
    return json.load(open(os.path.join(FIX, "subscriptions.json"), encoding="utf-8"))


class DigestBufferTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 6, 18, 10, 35, 10)
        self.buf = ib.DigestBuffer("acct1", quiet_sec=30)

    def test_same_bar_ts_merges_into_one_digest(self):
        self.buf.add(ib.parse_tag_line(MON), self.now)
        self.buf.add(ib.parse_tag_line(MON.replace("evt=resistance_break", "evt=support_return")), self.now)
        digests = self.buf.flush_all()
        self.assertEqual(len(digests), 1)
        self.assertEqual(digests[0]["raw_event_count"], 2)
        self.assertEqual(digests[0]["digest_id"], "acct1:2026-06-18T10:35:00")

    def test_duplicate_key_is_rejected(self):
        self.assertEqual(self.buf.add(ib.parse_tag_line(MON), self.now)[0], "added")
        self.assertEqual(self.buf.add(ib.parse_tag_line(MON), self.now)[0], "duplicate")
        self.assertEqual(self.buf.flush_all()[0]["raw_event_count"], 1)

    def test_new_bar_ts_flushes_previous(self):
        self.buf.add(ib.parse_tag_line(MON), self.now)
        status, flushed = self.buf.add(ib.parse_tag_line(MON.replace("10:35:00", "10:40:00")), self.now)
        self.assertEqual(status, "added")
        self.assertEqual([d["bar_ts"] for d in flushed], ["2026-06-18T10:35:00"])

    def test_quiet_sec_flushes(self):
        self.buf.add(ib.parse_tag_line(MON), self.now)
        self.assertEqual(self.buf.flush_due(self.now.replace(second=20)), [])
        self.assertEqual(len(self.buf.flush_due(self.now.replace(second=50))), 1)

    def test_macro_is_its_own_digest_keyed_by_as_of(self):
        _, flushed = self.buf.add(ib.parse_tag_line(MACRO), self.now)
        self.assertEqual(flushed[0]["bar_ts"], "2026-06-29T15:01:36")
        self.assertEqual(flushed[0]["raw_event_count"], 1)


class FormatTests(unittest.TestCase):
    def test_digest_one_line_with_header_and_tag_lines_preserved(self):
        buf = ib.DigestBuffer("acct1", 30)
        t1 = ib.parse_tag_line(MON); t2 = ib.parse_tag_line(MON.replace("evt=resistance_break", "evt=support_return"))
        buf.add(t1, datetime(2026, 6, 18)); buf.add(t2, datetime(2026, 6, 18))
        lines = ib.format_digest(buf.flush_all()[0], max_chars=1800)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("[digest account=acct1 bar_ts=2026-06-18T10:35:00 n=2] "))
        self.assertIn(t1["line"], lines[0]); self.assertIn(t2["line"], lines[0])
        self.assertNotIn("\n", lines[0])

    def test_digest_split_into_parts_when_over_max_chars(self):
        buf = ib.DigestBuffer("acct1", 30)
        for i in range(6):
            buf.add(ib.parse_tag_line(MON.replace("evt=resistance_break", f"evt={ib.ZONE_EVENTS[i]}")), datetime(2026, 6, 18))
        lines = ib.format_digest(buf.flush_all()[0], max_chars=400)
        self.assertGreater(len(lines), 1)
        self.assertIn("part=1/%d" % len(lines), lines[0])
        for ln in lines:
            self.assertLessEqual(len(ln), 400)


class DeliverTests(unittest.TestCase):
    def test_herdr_prompt_call(self):
        calls = []
        class MockRes:
            returncode = 0
            stdout = ""
            stderr = ""
        def mock_run(cmd, capture_output=True, text=True):
            calls.append(cmd)
            return MockRes()
        ib.deliver_herdr("hello", "rs-lead", run=mock_run)
        self.assertEqual(calls[0], ["herdr", "agent", "prompt", "rs-lead", "hello"])

    def test_herdr_blocked_raises_herdr_blocked_error(self):
        class MockRes:
            returncode = 1
            stdout = '{"error":{"code":"agent_blocked","message":"agent blocked"}}'
            stderr = ""
        def mock_run(cmd, capture_output=True, text=True):
            return MockRes()
        with self.assertRaises(ib.HerdrBlockedError):
            ib.deliver_herdr("hello", "rs-lead", run=mock_run)

    def test_file_deliver_appends_json_line(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "inbox.jsonl")
            ib.deliver_file("a", p); ib.deliver_file("b", p)
            rows = [json.loads(x) for x in open(p, encoding="utf-8")]
            self.assertEqual([r["text"] for r in rows], ["a", "b"])


class HerdrDeliveryQueueTests(unittest.TestCase):
    def test_processor_queue_overflow_drops_oldest_and_adds_skip_prefix(self):
        delivered = []
        blocked = [True]

        def flaky_deliver(text):
            if blocked[0]:
                raise ib.HerdrBlockedError("agent_blocked")
            delivered.append(text)

        import tempfile
        with tempfile.TemporaryDirectory() as d:
            log = ib.DeliveryLog(os.path.join(d, "del.jsonl"))
            sub = {"symbols": ["336260"], "event_types": ["support_return"], "sessions": ["REG_KRX_NXT"], "expires_at": "2026-06-30T20:00:00"}
            proc = ib.Processor({"account_id": "acct1", "inbound": {"quiet_sec": 0}}, sub, log, deliver=flaky_deliver,
                                now_fn=lambda: datetime(2026, 6, 18, 10, 50))

            # Helper to create tagged event
            def make_tagged(bar_ts):
                return {"tag": "#mon", "kv": {"schema": "rrr_mon_alert_v1", "evt": "support_return", "sym": "336260",
                                               "lvl": "s1", "lvl_px": "100", "px": "105", "bar_ts": bar_ts,
                                               "as_of": "2026-06-18T10:00:00", "sess": "REG_KRX_NXT"},
                        "line": f"#mon schema=rrr_mon_alert_v1 evt=support_return sym=336260 lvl=s1 lvl_px=100 px=105 bar_ts={bar_ts} as_of=2026-06-18T10:00:00 sess=REG_KRX_NXT"}

            # Bar 1 -> buffered, then flushed when Bar 2 arrives
            proc.handle_event(make_tagged("2026-06-18T10:00:00"), source="s", ref="1-0")
            proc.handle_event(make_tagged("2026-06-18T10:05:00"), source="s", ref="2-0")
            # Bar 1 delivery attempted -> blocked! In queue. Queue has 1.
            self.assertEqual(len(proc.delivery_queue), 1)
            self.assertIsNone(proc.cursor_committed_ref)

            # Bar 3 arrives -> flushes Bar 2. Bar 2 delivery attempted -> blocked! Queue has 2.
            proc.handle_event(make_tagged("2026-06-18T10:10:00"), source="s", ref="3-0")
            self.assertEqual(len(proc.delivery_queue), 2)
            self.assertIsNone(proc.cursor_committed_ref)

            # Bar 4 arrives -> flushes Bar 3. Bar 3 delivery attempted -> blocked! Queue has 3.
            proc.handle_event(make_tagged("2026-06-18T10:15:00"), source="s", ref="4-0")
            self.assertEqual(len(proc.delivery_queue), 3)
            self.assertIsNone(proc.cursor_committed_ref)

            # Bar 5 arrives -> flushes Bar 4. Bar 4 delivery attempted -> blocked!
            # Queue would be 4 -> overflow! Oldest (Bar 1) is dropped.
            proc.handle_event(make_tagged("2026-06-18T10:20:00"), source="s", ref="5-0")
            self.assertEqual(len(proc.delivery_queue), 3)
            self.assertEqual(proc.skipped_bars, 1)
            # Dropped Bar 1 ref commits cursor!
            self.assertEqual(proc.cursor_committed_ref, "1-0")

            # Now target unblocks!
            blocked[0] = False
            # Next event or finalize flushes remaining
            proc.finalize()
            # All delivered!
            self.assertEqual(len(proc.delivery_queue), 0)
            self.assertEqual(proc.skipped_bars, 0)
            # The first delivered item must have [생략 1봉] prefix!
            self.assertTrue(delivered[0].startswith("[생략 1봉] [digest"))
            # Newest ref commits cursor
            self.assertEqual(proc.cursor_committed_ref, "5-0")

    def _setup_blocked_proc(self):
        import tempfile
        blocked = [True]
        delivered = []

        def flaky_deliver(text):
            if blocked[0]:
                raise ib.HerdrBlockedError("agent_blocked")
            delivered.append(text)

        tmp = tempfile.TemporaryDirectory()
        log = ib.DeliveryLog(os.path.join(tmp.name, "del.jsonl"))
        sub = {"symbols": ["336260"], "event_types": ["support_return"], "sessions": ["REG_KRX_NXT"], "expires_at": "2026-06-30T20:00:00"}
        proc = ib.Processor({"account_id": "acct1", "inbound": {"quiet_sec": 0}}, sub, log, deliver=flaky_deliver,
                            now_fn=lambda: datetime(2026, 6, 18, 10, 50))
        return proc, blocked, tmp

    @staticmethod
    def _make_tagged(bar_ts="2026-06-18T10:00:00", sym="336260", evt="support_return"):
        return {
            "tag": "#mon",
            "kv": {"schema": "rrr_mon_alert_v1", "evt": evt, "sym": sym,
                   "lvl": "s1", "lvl_px": "100", "px": "105", "bar_ts": bar_ts,
                   "as_of": "2026-06-18T10:00:00", "sess": "REG_KRX_NXT"},
            "line": f"#mon schema=rrr_mon_alert_v1 evt={evt} sym={sym} lvl=s1 lvl_px=100 px=105 bar_ts={bar_ts} as_of=2026-06-18T10:00:00 sess=REG_KRX_NXT",
        }

    def test_queue_nonempty_prevents_cursor_advance_on_bad_tag(self):
        proc, blocked, tmp = self._setup_blocked_proc()
        with tmp:
            proc.handle_event(self._make_tagged("2026-06-18T10:00:00"), source="s", ref="1-0")
            proc.finalize()
            self.assertTrue(proc.buf.is_empty())
            self.assertEqual(len(proc.delivery_queue), 1)
            self.assertIsNone(proc.cursor_committed_ref)

            # While delivery_queue is non-empty, bad_tag must NOT advance cursor
            res = proc.handle_event(None, source="s", ref="bad-1")
            self.assertEqual(res, "skip:bad_tag")
            self.assertIsNone(proc.cursor_committed_ref)

            # When queue is unblocked and drained, cursor catches up through delivered item and _last_seen_ref
            blocked[0] = False
            proc.finalize()
            self.assertEqual(len(proc.delivery_queue), 0)
            self.assertEqual(proc.cursor_committed_ref, "bad-1")

            # With queue now empty, subsequent bad_tag immediately advances cursor
            res = proc.handle_event(None, source="s", ref="bad-2")
            self.assertEqual(res, "skip:bad_tag")
            self.assertEqual(proc.cursor_committed_ref, "bad-2")

    def test_queue_nonempty_prevents_cursor_advance_on_unsubscribed(self):
        proc, blocked, tmp = self._setup_blocked_proc()
        with tmp:
            proc.handle_event(self._make_tagged("2026-06-18T10:00:00"), source="s", ref="1-0")
            proc.finalize()
            self.assertTrue(proc.buf.is_empty())
            self.assertEqual(len(proc.delivery_queue), 1)
            self.assertIsNone(proc.cursor_committed_ref)

            # While delivery_queue is non-empty, unsubscribed event must NOT advance cursor
            unsub_tagged = self._make_tagged("2026-06-18T10:05:00", sym="005930")
            res = proc.handle_event(unsub_tagged, source="s", ref="unsub-1")
            self.assertEqual(res, "skip:unsubscribed")
            self.assertIsNone(proc.cursor_committed_ref)

            # When queue is unblocked and drained, cursor catches up through delivered item and _last_seen_ref
            blocked[0] = False
            proc.finalize()
            self.assertEqual(len(proc.delivery_queue), 0)
            self.assertEqual(proc.cursor_committed_ref, "unsub-1")

            # With queue now empty, subsequent unsubscribed event immediately advances cursor
            res = proc.handle_event(unsub_tagged, source="s", ref="unsub-2")
            self.assertEqual(res, "skip:unsubscribed")
            self.assertEqual(proc.cursor_committed_ref, "unsub-2")

    def test_queue_nonempty_prevents_cursor_advance_on_duplicate(self):
        proc, blocked, tmp = self._setup_blocked_proc()
        with tmp:
            tagged1 = self._make_tagged("2026-06-18T10:00:00")
            proc.handle_event(tagged1, source="s", ref="1-0")
            proc.finalize()
            self.assertTrue(proc.buf.is_empty())
            self.assertEqual(len(proc.delivery_queue), 1)
            self.assertIsNone(proc.cursor_committed_ref)

            # While delivery_queue is non-empty, duplicate event must NOT advance cursor
            res = proc.handle_event(tagged1, source="s", ref="dup-1")
            self.assertEqual(res, "skip:duplicate")
            self.assertIsNone(proc.cursor_committed_ref)

            # When queue is unblocked and drained, cursor catches up through delivered item and _last_seen_ref
            blocked[0] = False
            proc.finalize()
            self.assertEqual(len(proc.delivery_queue), 0)
            self.assertEqual(proc.cursor_committed_ref, "dup-1")

            # With queue now empty, subsequent duplicate event immediately advances cursor
            res = proc.handle_event(tagged1, source="s", ref="dup-2")
            self.assertEqual(res, "skip:duplicate")
            self.assertEqual(proc.cursor_committed_ref, "dup-2")





class TickKeyOrderTests(unittest.TestCase):
    """계약 §4.6.1: #mon-tick 은 전 키 필수이며 키 순서가 표 그대로 고정. 알 수 없는 key 는 무시."""
    GOOD = "x\n#mon-tick schema=rrr_mon_tick_v1 sym=003230 bar_ts=2026-08-17T09:45:00 o=1 h=2 l=1 c=2 v=3 watch_id=7"

    def test_fixed_order_accepted_and_unknown_key_ignored(self):
        t = ib.parse_tag_line(self.GOOD + " extra=1")
        self.assertIsNotNone(t)
        self.assertEqual(t["kv"]["watch_id"], "7")

    def test_out_of_order_keys_rejected(self):
        bad = "x\n#mon-tick schema=rrr_mon_tick_v1 bar_ts=2026-08-17T09:45:00 sym=003230 o=1 h=2 l=1 c=2 v=3 watch_id=7"
        self.assertIsNone(ib.parse_tag_line(bad))

    def test_mon_tag_order_is_not_enforced(self):
        reordered = "x\n#mon schema=rrr_mon_alert_v1 sym=336260 evt=support_return lvl=s1 lvl_px=1 px=1 bar_ts=t as_of=t"
        self.assertIsNotNone(ib.parse_tag_line(reordered))
