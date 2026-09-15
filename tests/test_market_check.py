"""market-check — 어댑터 코어의 30분 시장 체크 wake: 창(inbound.market_check) 안에서 every_min 슬롯마다 "[market-check ts=HH:MM]" 1줄.
내용은 시각뿐(코드가 시장을 요약하지 않는다). 다이제스트와 독립, 창 밖 0, 소스 공통(core.Processor). now_fn 주입."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import inbound_core as core  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
MON = ("📈 두산퓨얼셀(336260)\n#mon schema=rrr_mon_alert_v1 evt=resistance_break sym=336260 lvl=r1 lvl_px=22150 px=22300 "
       "bar_ts=2026-06-18T09:30:00 as_of=2026-06-18T09:30:07 str=strong sess=REG_KRX_NXT")


def T(s):
    return datetime.fromisoformat(s)


class Clock:
    def __init__(self, at):
        self.at = T(at)

    def __call__(self):
        return self.at

    def set(self, at):
        self.at = T(at)


class MarketCheckBase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.logp = os.path.join(self.d, "delivery.jsonl")
        self.out = []
        self.watch_path = os.path.join(self.d, "watchlist.json")
        with open(self.watch_path, "w", encoding="utf-8") as f:
            json.dump({"336260": {}, "003230": {}}, f)
        self.clock = Clock("2026-06-18T08:30:00")

    def proc(self, mc=None, **inbound_over):
        inbound = {"quiet_sec": 30, "inject_max_chars": 1800}
        if mc is not None:
            inbound["market_check"] = mc
        inbound.update(inbound_over)
        cfg = {"account_id": "acct1", "operator_chat_id": "111", "signal_chat_id": "-500", "signal_sender_id": "777", "inbound": inbound}
        return core.Processor(cfg, core.WatchList(self.watch_path), core.DeliveryLog(self.logp), deliver=self.out.append, now_fn=self.clock)

    def log_rows(self, kind=None):
        if not os.path.exists(self.logp):
            return []
        with open(self.logp, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        return [r for r in rows if kind is None or r.get("kind") == kind]

    def ticks(self, p, *times):
        for t in times:
            self.clock.set(t)
            p.tick()


class MarketCheckTest(MarketCheckBase):
    def test_defaults_are_in_code_and_pinned(self):
        self.assertEqual(core.MARKET_CHECK_DEFAULTS, {"every_min": 30, "from": "09:00", "to": "15:30"})
        p = self.proc()  # config 에 market_check 없음 → 기본값
        self.assertEqual(p.market_check, core.MARKET_CHECK_DEFAULTS)

    def test_two_wakes_inside_window(self):
        p = self.proc()
        self.ticks(p, "2026-06-18T09:00:05", "2026-06-18T09:10:00", "2026-06-18T09:29:59", "2026-06-18T09:31:00", "2026-06-18T09:45:00")
        self.assertEqual(self.out, ["[market-check ts=09:00]", "[market-check ts=09:31]"])
        rows = self.log_rows("market_check")
        self.assertEqual([(r["slot"], r["ts"][11:16]) for r in rows], [("09:00", "09:00"), ("09:30", "09:31")])
        self.assertTrue(all(r["delivered_to"] for r in rows))
        self.assertEqual(p.stats["market_checks"], 2)

    def test_zero_outside_window(self):
        p = self.proc()
        self.ticks(p, "2026-06-18T08:59:59", "2026-06-18T08:30:00", "2026-06-18T15:31:00", "2026-06-18T16:00:00", "2026-06-18T20:00:00")  # 창은 분 단위 양끝 포함(15:30:xx 는 안)
        self.assertEqual(self.out, [])
        self.assertEqual(self.log_rows("market_check"), [])

    def test_window_edges_inclusive(self):
        p = self.proc()
        self.ticks(p, "2026-06-18T09:00:00", "2026-06-18T15:30:00")
        self.assertEqual(self.out, ["[market-check ts=09:00]", "[market-check ts=15:30]"])

    def test_missed_slots_collapse_to_one(self):
        p = self.proc()
        self.ticks(p, "2026-06-18T11:07:00")  # 어댑터가 09:00~11:00 사이 끊겨 있었어도 wake 는 현재 슬롯 1회
        self.assertEqual(self.out, ["[market-check ts=11:07]"])
        self.assertEqual(self.log_rows("market_check")[0]["slot"], "11:00")

    def test_next_day_wakes_again(self):
        p = self.proc()
        self.ticks(p, "2026-06-18T09:00:00", "2026-06-19T09:00:00")
        self.assertEqual(self.out, ["[market-check ts=09:00]", "[market-check ts=09:00]"])

    def test_independent_of_digests(self):
        p = self.proc()
        self.clock.set("2026-06-18T09:30:10")
        self.assertEqual(p.handle_event(core.parse_tag_line(MON), source="telegram", ref="1"), "buffered")
        p.tick()  # quiet_sec 미경과 → 다이제스트는 아직, market-check 는 슬롯 09:30 → 1줄
        self.assertEqual(self.out, ["[market-check ts=09:30]"])
        self.clock.set("2026-06-18T09:31:00")
        p.tick()  # quiet_sec 경과 → 다이제스트 flush. market-check 는 같은 슬롯이라 0
        self.assertEqual(len(self.out), 2)
        self.assertTrue(self.out[1].startswith("[digest account=acct1 bar_ts=2026-06-18T09:30:00"))
        self.assertNotIn("market-check", self.out[1])
        kinds = [r["kind"] for r in self.log_rows()]
        self.assertEqual(kinds, ["market_check", "digest"])
        self.assertEqual(p.stats["digests"], 1)

    def test_config_window_and_disable(self):
        p = self.proc(mc={"every_min": 60, "from": "10:00", "to": "12:00"})
        self.ticks(p, "2026-06-18T09:30:00", "2026-06-18T10:00:00", "2026-06-18T10:30:00", "2026-06-18T11:00:00", "2026-06-18T12:30:00")
        self.assertEqual(self.out, ["[market-check ts=10:00]", "[market-check ts=11:00]"])
        self.out.clear()
        p = self.proc(mc={"every_min": 0})  # 0 = 끔
        self.ticks(p, "2026-06-18T09:00:00", "2026-06-18T09:30:00")
        self.assertEqual(self.out, [])

    def test_line_carries_time_only(self):
        p = self.proc()
        self.ticks(p, "2026-06-18T13:05:00")
        self.assertEqual(self.out, ["[market-check ts=13:05]"])  # 지수·업종·요약 0 — 읽는 것은 세션의 일


if __name__ == "__main__":
    unittest.main()
