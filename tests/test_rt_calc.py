"""rt_calc — 검증된 산술(호가단위·floor·수량·평단·R). 판단 0."""
import os
import subprocess
import sys
import unittest
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import rt_calc as rc  # noqa: E402


class Tick(unittest.TestCase):
    def test_tick_size_table(self):
        self.assertEqual(rc.tick_size(Decimal("1500")), Decimal("1"))
        self.assertEqual(rc.tick_size(Decimal("3000")), Decimal("5"))
        self.assertEqual(rc.tick_size(Decimal("15000")), Decimal("10"))
        self.assertEqual(rc.tick_size(Decimal("40000")), Decimal("50"))
        self.assertEqual(rc.tick_size(Decimal("150000")), Decimal("100"))
        self.assertEqual(rc.tick_size(Decimal("400000")), Decimal("500"))
        self.assertEqual(rc.tick_size(Decimal("600000")), Decimal("1000"))

    def test_normalize_buy_rounds_down_sell_rounds_up(self):
        self.assertEqual(rc.tick_normalize(Decimal("22333"), "buy"), Decimal("22300"))
        self.assertEqual(rc.tick_normalize(Decimal("22333"), "sell"), Decimal("22350"))


class Arithmetic(unittest.TestCase):
    def test_floor_px_is_avg_times_margin_rounded_up_to_tick(self):
        self.assertEqual(rc.floor_px(Decimal("100000"), Decimal("1.003")), Decimal("100300"))
        self.assertEqual(rc.floor_px(Decimal("22150"), Decimal("1.003")), Decimal("22250"))  # 22216.45 → 다음 호가(50)

    def test_qty_for_allocation_caps_by_alloc_and_cash(self):
        self.assertEqual(rc.qty_for_allocation(nav=Decimal("100000000"), alloc_pct=Decimal("10"), px=Decimal("51000"), cash=Decimal("50000000")), 196)
        self.assertEqual(rc.qty_for_allocation(nav=Decimal("100000000"), alloc_pct=Decimal("10"), px=Decimal("51000"), cash=Decimal("1000000")), 19)
        self.assertEqual(rc.qty_for_allocation(nav=Decimal("100000000"), alloc_pct=Decimal("10"), px=Decimal("51000"), cash=Decimal("0")), 0)

    def test_avg_after_fill(self):
        self.assertEqual(rc.avg_after_fill(10, Decimal("100"), 10, Decimal("120")), Decimal("110"))
        self.assertEqual(rc.avg_after_fill(0, Decimal("0"), 5, Decimal("77")), Decimal("77"))

    def test_r_multiple_and_pnl(self):
        self.assertEqual(rc.pnl_amount(qty=10, entry_px=Decimal("100"), exit_px=Decimal("130"), fees=Decimal("30")), Decimal("270"))
        self.assertEqual(rc.r_multiple(pnl=Decimal("270"), risk_amount=Decimal("100")), Decimal("2.70"))
        self.assertIsNone(rc.r_multiple(pnl=Decimal("270"), risk_amount=Decimal("0")))

    def test_px_abs_strips_sign_prefix(self):
        self.assertEqual(rc.px_abs("+22300"), Decimal("22300"))
        self.assertEqual(rc.px_abs("-22300"), Decimal("22300"))
        self.assertIsNone(rc.px_abs(""))


class Cli(unittest.TestCase):
    def test_selftest_and_json_cli(self):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "rt_calc.py"), "selftest"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "rt_calc.py"), "qty", "--nav", "100000000", "--alloc-pct", "10", "--px", "51000", "--cash", "50000000"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn('"qty": 196', out.stdout)
