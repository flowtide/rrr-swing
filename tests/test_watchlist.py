"""감시 목록(local/watchlist.json) — 어댑터의 유일한 필터.

규칙은 한 문장이다: **이벤트에 sym 이 있으면 목록에 있어야 배달하고, sym 이 없으면(시장 전체)
항상 배달한다.**

이 설계가 지키는 두 가지를 못박는다.

- **종목 외에는 거르지 않는다.** `sess` 는 주문정책을 고르는 재료이지(장전=지정가만·본장=SOR·
  동시호가=취소만) 배달 기준이 아니다. 세션이나 만료로 배달을 거르면 장 마감 뒤 좁힌 값이
  다음 거래일까지 살아남아, 보유 종목의 신호가 전부 버려지고도 조용한 장과 구분되지 않는다.
- **목록은 이벤트마다 읽는다.** 기동 시 1회 읽어 고정하면 세션이 자기 눈을 런타임에 고칠 수
  없다 — 고쳐 쓴 파일을 돌고 있는 어댑터가 보지 않기 때문이다.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import inbound_core as core  # noqa: E402


def mon(sym="336260", evt="resistance_break", sess="REG_KRX_NXT", bar_ts="2026-06-18T09:30:00"):
    return (f"#mon schema=rrr_mon_alert_v1 evt={evt} sym={sym} lvl=r1 lvl_px=22150 px=22300 "
            f"bar_ts={bar_ts} as_of={bar_ts} str=strong sess={sess}")


def macro(as_of="2026-06-18T09:44:00", sess="REG_KRX_NXT"):
    return f"#macro schema=rrr_mon_macro_v1 sess={sess} as_of={as_of} slot=h0944"


class MatchRule(unittest.TestCase):
    """판정은 두 줄이다 — 그 두 줄을 양방향으로 못박는다."""

    def test_watched_symbol_is_delivered(self):
        self.assertEqual(core.watchlist_matches({"336260": {}}, core.parse_tag_line(mon())), (True, ""))

    def test_unwatched_symbol_is_dropped(self):
        ok, reason = core.watchlist_matches({"003230": {}}, core.parse_tag_line(mon(sym="336260")))
        self.assertEqual((ok, reason), (False, "unwatched"))

    def test_event_without_symbol_is_always_delivered(self):
        """macro 는 시장 전체라 종목 개념이 없다.

        목록에 없다고 버리면 이 유형은 어떤 목록으로도 도달할 수 없다. 그래서 종목 없는
        이벤트는 목록과 무관하게 통과시킨다.
        """
        for wl in ({}, {"336260": {}}):
            with self.subTest(watchlist=wl):
                self.assertEqual(core.watchlist_matches(wl, core.parse_tag_line(macro())), (True, ""))

    def test_session_and_event_type_no_longer_filter(self):
        """sess·evt 는 판단 재료로 실려 올 뿐, 배달 여부를 정하지 않는다.

        heartbeat 도, 장전(PRE_NXT)도, 동시호가 뒤 어떤 세션도 목록에만 있으면 온다.
        """
        for evt in ("heartbeat", "support_break", "resistance_enter"):
            for sess in ("PRE_NXT", "REG_KRX_NXT", "POST_NXT", "CLOSE_BREAK"):
                with self.subTest(evt=evt, sess=sess):
                    self.assertEqual(
                        core.watchlist_matches({"336260": {}}, core.parse_tag_line(mon(evt=evt, sess=sess))),
                        (True, ""))

    def test_empty_watchlist_drops_symbol_events(self):
        """빈 목록 = 아무 종목도 안 본다. 되돌리기는 파일 한 줄이고 즉시 듣는다."""
        ok, reason = core.watchlist_matches({}, core.parse_tag_line(mon()))
        self.assertEqual((ok, reason), (False, "unwatched"))


class ReadsFileOnEveryEvent(unittest.TestCase):
    """세션이 자기 눈을 런타임에 고칠 수 있어야 한다."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "watchlist.json")

    def write(self, obj, mtime=None):
        with open(self.p, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        if mtime is not None:                       # mtime 캐시를 시험하려면 값을 직접 정한다
            os.utime(self.p, (mtime, mtime))

    def test_change_on_disk_takes_effect_without_restart(self):
        """프로세스를 다시 띄우지 않고 목록이 바뀐다."""
        self.write({"006800": {}}, mtime=1_000_000)
        wl = core.WatchList(self.p)
        self.assertEqual(sorted(wl.symbols()), ["006800"])
        self.write({"006800": {}, "042700": {}}, mtime=1_000_100)
        self.assertEqual(sorted(wl.symbols()), ["006800", "042700"])

    def test_unchanged_file_is_not_reread(self):
        """mtime 이 같으면 파일을 다시 열지 않는다.

        **내용을 바꾸되 mtime 은 그대로 둔다.** 같은 내용으로 다시 쓰면 캐시를 타든 디스크를
        다시 읽든 결과가 같아, 이 테스트는 캐시가 있는지 없는지를 아무것도 재지 못한다.
        """
        self.write({"006800": {}}, mtime=1_000_000)
        wl = core.WatchList(self.p)
        self.assertEqual(sorted(wl.symbols()), ["006800"])
        self.write({"042700": {}}, mtime=1_000_000)      # 내용만 다르고 mtime 은 동일
        self.assertEqual(sorted(wl.symbols()), ["006800"], "mtime 이 같은데 파일을 다시 읽었다")

    def test_missing_file_watches_nothing_and_says_so(self):
        """파일이 없으면 종목 이벤트가 하나도 안 온다 — 그 상태가 조용하면 안 된다.

        빈 목록({})은 "아무것도 안 보겠다"는 **의도**일 수 있지만, 파일 부재는 의도가 아니다.
        둘을 같은 침묵으로 처리하면 실수로 지운 목록이 조용한 장으로 읽힌다.
        """
        wl = core.WatchList(os.path.join(self.d, "없다.json"))
        self.assertEqual(wl.symbols(), {})
        self.assertTrue(wl.problem, "파일이 없는 사실이 어디에도 남지 않았다")
        self.assertIn("없", wl.problem)

    def test_empty_list_is_a_legitimate_silence(self):
        """빈 목록은 의도다 — 여기까지 시끄러우면 경고가 소음이 되어 무시된다."""
        self.write({}, mtime=1_000_000)
        wl = core.WatchList(self.p)
        self.assertEqual(wl.symbols(), {})
        self.assertFalse(wl.problem, "의도된 빈 목록에 경고가 붙었다")

    def test_broken_file_keeps_the_last_good_list(self):
        """깨진 파일에 빈 목록을 돌려주면 그 순간 조용히 눈이 먼다 — 마지막 정상값을 지킨다."""
        self.write({"006800": {}}, mtime=1_000_000)
        wl = core.WatchList(self.p)
        wl.symbols()
        with open(self.p, "w", encoding="utf-8") as f:
            f.write("{ 깨짐")
        os.utime(self.p, (1_000_200, 1_000_200))
        self.assertEqual(sorted(wl.symbols()), ["006800"], "깨진 파일이 감시 목록을 지웠다")
        self.assertTrue(wl.problem, "깨진 사실이 어디에도 남지 않았다")

    def test_broken_file_at_first_read_is_not_a_one_time_notice(self):
        """기동 시점에 이미 깨져 있으면 지킬 정상값이 없다 — 그 상태가 계속 문제로 남아야 한다.

        첫 읽기에서 한 번 알리고 끝내면, mtime 이 그대로인 한 이후 모든 종목 이벤트가
        추가 신호 없이 버려진다.
        """
        with open(self.p, "w", encoding="utf-8") as f:
            f.write("{ 깨짐")
        os.utime(self.p, (1_000_000, 1_000_000))
        wl = core.WatchList(self.p)
        for _ in range(3):
            self.assertEqual(wl.symbols(), {})
            self.assertTrue(wl.problem, "깨진 채로 도는데 문제 표시가 사라졌다")

    def test_symbol_codes_are_validated_on_read(self):
        """쓰는 쪽만 검증하면, 손으로 적은 `006800_AL` 이 조용히 실려 영원히 매칭되지 않는다.

        이벤트의 sym 은 순수 6자리라 `_AL` 접미사는 어떤 이벤트와도 맞지 않는다. 목록에
        남겨 두면 "적어 뒀는데 왜 안 오지" 가 되고, 그 원인은 어디에도 적히지 않는다.
        """
        self.write({"006800": {}, "006800_AL": {}, "abc": {}}, mtime=1_000_000)
        wl = core.WatchList(self.p)
        self.assertEqual(sorted(wl.symbols()), ["006800"], "종목 코드가 아닌 항목이 실렸다")
        self.assertTrue(wl.problem, "버린 항목이 어디에도 남지 않았다")
        self.assertIn("006800_AL", wl.problem)

    def test_note_is_kept_for_the_reader(self):
        """왜 이 종목을 보는지를 파일에 적을 수 있어야 사람이 읽는다."""
        self.write({"006800": {"note": "보유 120주"}}, mtime=1_000_000)
        self.assertEqual(core.WatchList(self.p).symbols()["006800"]["note"], "보유 120주")

    def test_list_form_is_accepted(self):
        """사람이 손으로 고치는 파일이다 — 메모 없이 종목만 적은 형태도 받는다."""
        self.write(["006800", "042700"], mtime=1_000_000)
        self.assertEqual(sorted(core.WatchList(self.p).symbols()), ["006800", "042700"])


class ProblemReachesTheSession(unittest.TestCase):
    """목록이 정상이 아니면 세션이 알아야 한다.

    이 상태에서는 종목 이벤트가 전부 버려지는데, 어댑터는 살아 있고 배달 로그만 쌓인다 —
    세션에게는 조용한 장과 똑같이 보인다. 배달 로그는 아무도 매번 읽지 않는다.
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.wp = os.path.join(self.d, "watchlist.json")
        self.logp = os.path.join(self.d, "delivery.jsonl")
        self.out = []
        self.clock = {"t": datetime.fromisoformat("2026-06-18T10:00:00")}

    def proc(self):
        return core.Processor({"account_id": "acct", "inbound": {"quiet_sec": 30}},
                              core.WatchList(self.wp), core.DeliveryLog(self.logp),
                              deliver=self.out.append, now_fn=lambda: self.clock["t"])

    def feed(self, p, line=None, ref="1-0"):
        return p.handle_event(core.parse_tag_line(line or mon()), source="rrr_stream", ref=ref)

    def notices(self):
        return [t for t in self.out if t.startswith("[watchlist-problem")]

    def rows(self, kind):
        if not os.path.exists(self.logp):
            return []
        with open(self.logp, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip() and json.loads(x).get("kind") == kind]

    def test_missing_file_is_announced(self):
        p = self.proc()
        self.assertEqual(self.feed(p), "skip:unwatched")
        self.assertEqual(len(self.notices()), 1, f"파일이 없는데 조용하다: {self.out}")
        self.assertIn("없다", self.notices()[0])

    def test_announced_once_per_day(self):
        """장 하나에 이벤트는 수십 건 온다 — 매번 깨우면 그게 소음이 되어 무시된다."""
        p = self.proc()
        for i in range(5):
            self.feed(p, ref=f"{i}-0")
        self.assertEqual(len(self.notices()), 1)

    def test_a_new_day_announces_again(self):
        """어댑터는 날을 넘겨 산다 — 어제 알렸다고 오늘의 실명을 넘기면 안 된다."""
        p = self.proc()
        self.feed(p, ref="1-0")
        self.clock["t"] = datetime.fromisoformat("2026-06-19T10:00:00")
        self.feed(p, ref="2-0")
        self.assertEqual(len(self.notices()), 2)

    def test_a_different_problem_announces_again(self):
        with open(self.wp, "w", encoding="utf-8") as f:
            f.write("{ 깨짐")
        p = self.proc()
        self.feed(p, ref="1-0")
        with open(self.wp, "w", encoding="utf-8") as f:
            json.dump({"006800_AL": {}}, f)
        self.feed(p, ref="2-0")
        self.assertEqual(len(self.notices()), 2, f"다른 문제인데 한 번만 알렸다: {self.notices()}")

    def test_healthy_list_is_silent(self):
        with open(self.wp, "w", encoding="utf-8") as f:
            json.dump({"336260": {}}, f)
        p = self.proc()
        self.assertEqual(self.feed(p), "buffered")
        self.assertEqual(self.notices(), [])

    def test_empty_list_is_silent(self):
        """빈 목록은 의도다 — 여기까지 깨우면 경고가 소음이 된다."""
        with open(self.wp, "w", encoding="utf-8") as f:
            json.dump({}, f)
        p = self.proc()
        self.assertEqual(self.feed(p), "skip:unwatched")
        self.assertEqual(self.notices(), [])

    def test_notice_is_recorded_for_audit(self):
        """다이제스트와 같은 이유로 남긴다 — pane 에만 들어가면 세션이 끝나는 순간 사라진다."""
        p = self.proc()
        self.feed(p)
        self.assertEqual(len(self.rows("watchlist_problem")), 1)

    def test_delivery_failure_does_not_stop_the_adapter(self):
        """통지 배달 실패가 어댑터를 멈추면, 알리려던 것보다 큰 것을 잃는다."""
        def boom(_t):
            raise RuntimeError("pane gone")
        p = core.Processor({"account_id": "acct", "inbound": {}}, core.WatchList(self.wp),
                           core.DeliveryLog(self.logp), deliver=boom, now_fn=lambda: self.clock["t"])
        self.assertEqual(self.feed(p), "skip:unwatched", "통지 실패가 이벤트 처리 결과를 바꿨다")
        self.assertIn("watchlist_problem_deliver_failed", [r.get("reason") for r in self.rows("skip")])


class OldSubscriptionVocabularyIsGone(unittest.TestCase):
    """구독 어휘가 되살아나면 배달 기준이 둘로 갈린다 — 이름으로 막는다."""

    def test_removed_symbols_from_core(self):
        for gone in ("subscription_matches", "SESSION_TOKENS", "SUBSCRIBABLE_EVENT_TYPES", "load_subscriptions"):
            self.assertFalse(hasattr(core, gone), f"{gone} 가 아직 있다")

    def test_removed_reasons_from_source(self):
        with open(os.path.join(ROOT, "bin", "inbound_core.py"), encoding="utf-8") as f:
            src = f.read()
        for gone in ("session_mismatch", "session_unknown", "subscription_expired", "expires_at"):
            self.assertNotIn(gone, src, f"폐기된 어휘 '{gone}' 가 남았다")


if __name__ == "__main__":
    unittest.main()
