"""rrr 스트림 엔트리 → 태그 줄 변환 계약. Telegram 소스는 폐기됐으므로 두 소스 파리티는 더 이상 없다."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import inbound_core as core  # noqa: E402

CFG = {"operator_chat_id": "111", "signal_chat_id": "-500", "signal_sender_id": "777", "account_id": "acct1",
       "inbound": {"quiet_sec": 30, "inject_max_chars": 1800}}
SUB = {"account_id": "acct1", "symbols": ["336260", "003230"], "event_types": ["support_return", "resistance_break", "tick", "macro"],
       "sessions": ["REG_KRX_NXT"], "expires_at": "2026-06-18T20:00:00"}
NOW = datetime(2026, 6, 18, 10, 50)

# rrr EventEntry.to_wire 계약 — 포인터 필드만
ZONE_ENTRY = {"id": "1750210507000-0", "kind": "zone", "schema": "rrr_mon_alert_v1", "trade_date": "2026-06-18", "as_of": "2026-06-18T10:35:07",
              "sess": "REG_KRX_NXT", "sym": "336260", "bar_ts": "2026-06-18T10:35:00", "evt": "support_return", "lvl": "s1", "lvl_px": "22150", "px": "22300"}
TICK_ENTRY = {"id": "1750211102000-0", "kind": "tick", "schema": "rrr_mon_tick_v1", "trade_date": "2026-06-18", "as_of": "2026-06-18T10:45:02",
              "sym": "003230", "bar_ts": "2026-06-18T10:40:00", "o": "1240000", "h": "1250000", "l": "1238000", "c": "1247.5", "v": "12345", "watch_id": "7"}
MACRO_ENTRY = {"id": "1750211040000-0", "kind": "macro", "schema": "rrr_mon_macro_v1", "trade_date": "2026-06-18", "as_of": "2026-06-18T10:44:00",
               "sess": "REG_KRX_NXT", "slot": "h1044"}
# 같은 이벤트의 Telegram 태그 줄(포인터 키만, 계약 순서)
ZONE_TAG = "📈 x\n#mon schema=rrr_mon_alert_v1 evt=support_return sym=336260 lvl=s1 lvl_px=22150 px=22300 bar_ts=2026-06-18T10:35:00 as_of=2026-06-18T10:35:07 sess=REG_KRX_NXT"
TICK_TAG = "🔁 x\n#mon-tick schema=rrr_mon_tick_v1 sym=003230 bar_ts=2026-06-18T10:40:00 o=1240000 h=1250000 l=1238000 c=1247.5 v=12345 watch_id=7"
MACRO_TAG = "🌐 x\n#macro schema=rrr_mon_macro_v1 sess=REG_KRX_NXT as_of=2026-06-18T10:44:00 slot=h1044"



class EntryToTagged(unittest.TestCase):
    def test_zone_entry_rebuilds_the_contract_tag_line(self):
        t = core.entry_to_tagged(ZONE_ENTRY)
        self.assertEqual(t["tag"], "#mon")
        self.assertEqual(t["line"], ZONE_TAG.split("\n")[1])
        self.assertEqual(t["kv"]["evt"], "support_return")
        self.assertEqual(t["kv"]["trade_date"], "2026-06-18")  # kv 는 stream 전용 필드도 보존(로그용), 태그 줄에는 없음

    def test_tick_entry_keeps_fixed_key_order(self):
        self.assertEqual(core.entry_to_tagged(TICK_ENTRY)["line"], TICK_TAG.split("\n")[1])

    def test_macro_entry(self):
        self.assertEqual(core.entry_to_tagged(MACRO_ENTRY)["line"], MACRO_TAG.split("\n")[1])

    def test_unknown_kind_or_missing_required_is_none(self):
        self.assertIsNone(core.entry_to_tagged(dict(ZONE_ENTRY, kind="other")))
        self.assertIsNone(core.entry_to_tagged({k: v for k, v in ZONE_ENTRY.items() if k != "bar_ts"}))


if __name__ == "__main__":
    unittest.main()
