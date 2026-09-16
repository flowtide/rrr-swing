"""bin/watchlist.py — 감시 목록 편집 CLI. 판단 0, 저장과 원장 기록만.

필드는 종목 하나뿐이고, 부분 갱신(add·drop)이 1급이다. 전면 교체만 있으면 종목 하나를
바꾸려는 명령이 적지 않은 값을 함께 지우며, 그 손실은 조용하다.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "bin", "watchlist.py")
sys.path.insert(0, os.path.join(ROOT, "bin"))

import watchlist as wl  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "watchlist.json")
        self.ledger = os.path.join(self.d, "ledger.jsonl")

    def read(self):
        with open(self.p, encoding="utf-8") as f:
            return json.load(f)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, CLI, "--path", self.p, "--ledger", self.ledger, *args],
                              capture_output=True, text=True, cwd=ROOT)


class SetReplaces(Base):
    def test_set_writes_symbols(self):
        wl.set_symbols(self.p, ["006800", "042700"], now="2026-09-15T14:00:00")
        self.assertEqual(sorted(self.read()), ["006800", "042700"])

    def test_set_replaces_wholesale(self):
        """set 은 전면 교체다 — 그 뜻이 명령 이름에 드러나 있으므로 놀랄 일이 아니다."""
        wl.set_symbols(self.p, ["006800", "042700"], now="2026-09-15T14:00:00")
        wl.set_symbols(self.p, ["005930"], now="2026-09-15T14:01:00")
        self.assertEqual(sorted(self.read()), ["005930"])

    def test_rejects_bad_symbol(self):
        for bad in ("06800", "006800_AL", "abc", ""):
            with self.subTest(sym=bad):
                with self.assertRaises(ValueError):
                    wl.set_symbols(self.p, [bad], now="2026-09-15T14:00:00")

    def test_al_suffix_is_rejected(self):
        """`_AL` 은 조회 전용 심볼이다 — 감시 목록의 종목 코드는 순수 6자리다."""
        with self.assertRaises(ValueError):
            wl.set_symbols(self.p, ["006800_AL"], now="2026-09-15T14:00:00")


class PartialEdits(Base):
    """전면 교체만 있으면 종목 하나를 바꾸려는 명령이 나머지를 함께 지운다."""

    def test_add_keeps_the_others(self):
        wl.set_symbols(self.p, ["006800"], now="2026-09-15T14:00:00")
        wl.add(self.p, "042700", note="신규 관심", now="2026-09-15T14:01:00")
        got = self.read()
        self.assertEqual(sorted(got), ["006800", "042700"])
        self.assertEqual(got["042700"]["note"], "신규 관심")

    def test_drop_keeps_the_others(self):
        wl.set_symbols(self.p, ["006800", "042700"], now="2026-09-15T14:00:00")
        wl.drop(self.p, "042700", now="2026-09-15T14:01:00")
        self.assertEqual(sorted(self.read()), ["006800"])

    def test_add_preserves_existing_note(self):
        """메모 없이 다시 add 했다고 남이 적어 둔 이유가 지워지면 안 된다."""
        wl.set_symbols(self.p, ["006800"], now="2026-09-15T14:00:00")
        wl.add(self.p, "006800", note="보유 120주", now="2026-09-15T14:01:00")
        wl.add(self.p, "006800", note=None, now="2026-09-15T14:02:00")
        self.assertEqual(self.read()["006800"]["note"], "보유 120주")

    def test_drop_of_absent_symbol_is_not_an_error(self):
        """이미 빠진 종목을 또 빼는 것은 원하는 상태에 이미 있다는 뜻이다(멱등)."""
        wl.set_symbols(self.p, ["006800"], now="2026-09-15T14:00:00")
        wl.drop(self.p, "042700", now="2026-09-15T14:01:00")
        self.assertEqual(sorted(self.read()), ["006800"])


class LedgerTrail(Base):
    def test_append_ledger_records_the_declaration(self):
        wl.set_symbols(self.p, ["006800"], now="2026-09-15T14:00:00",
                       account_id="acct", ledger_path=self.ledger)
        with open(self.ledger, encoding="utf-8") as f:
            rec = json.loads(f.readline())
        self.assertEqual(rec["evt"], "subscription")
        self.assertEqual(rec["symbols"], ["006800"])
        self.assertEqual(rec["account_id"], "acct")
        for gone in ("event_types", "sessions", "expires_at"):
            self.assertNotIn(gone, rec, f"폐기된 필드 '{gone}' 를 원장에 쓴다")

    def test_cli_append_ledger_works_after_the_subcommand(self):
        """사람이 실제로 치는 순서로 먹어야 한다 — `add <종목> --note … --append-ledger`.

        부모 파서에 두면 서브명령 **앞**에서만 먹고, 뒤에 치면 `unrecognized arguments` 로
        죽는다. 문서의 예시가 전부 뒤에 두는 형태였고, 그 형태가 동작하지 않았다.
        """
        r = self.run_cli("add", "006800", "--note", "보유 120주", "--append-ledger")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(self.ledger), "원장에 쓰지 않았다")
        with open(self.ledger, encoding="utf-8") as f:
            rec = json.loads(f.readline())
        self.assertEqual(rec["evt"], "subscription")
        self.assertEqual(rec["symbols"], ["006800"])

    def test_cli_append_ledger_on_every_writing_subcommand(self):
        """쓰는 명령은 전부 같은 자리에서 받는다 — 하나만 되면 나머지에서 또 물린다."""
        for args in (["set", "006800"], ["add", "042700"], ["drop", "042700"], ["clear"]):
            with self.subTest(cmd=args[0]):
                if os.path.exists(self.ledger):
                    os.remove(self.ledger)
                r = self.run_cli(*args, "--append-ledger")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertTrue(os.path.exists(self.ledger), f"{args[0]} 가 원장에 쓰지 않았다")

    def test_no_ledger_write_without_the_flag(self):
        wl.set_symbols(self.p, ["006800"], now="2026-09-15T14:00:00")
        self.assertFalse(os.path.exists(self.ledger))


class Cli(Base):
    def test_set_show_add_drop_roundtrip(self):
        self.assertEqual(self.run_cli("set", "006800", "042700").returncode, 0)
        r = self.run_cli("show")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(json.loads(r.stdout)), ["006800", "042700"])
        self.assertEqual(self.run_cli("drop", "042700").returncode, 0)
        self.assertEqual(sorted(json.loads(self.run_cli("show").stdout)), ["006800"])
        self.assertEqual(self.run_cli("add", "005930", "--note", "관심").returncode, 0)
        self.assertEqual(sorted(json.loads(self.run_cli("show").stdout)), ["005930", "006800"])

    def test_show_on_missing_file_is_empty_not_an_error(self):
        """기동 selftest 가 이 명령을 부른다 — 최초 설치에서 죽으면 세션이 안 뜬다."""
        r = self.run_cli("show")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout), {})

    def test_clear_empties_the_list(self):
        self.run_cli("set", "006800")
        self.assertEqual(self.run_cli("clear").returncode, 0)
        self.assertEqual(json.loads(self.run_cli("show").stdout), {})

    def test_bad_symbol_exits_nonzero(self):
        r = self.run_cli("set", "abc")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("abc", r.stdout + r.stderr)


class OldToolIsGone(unittest.TestCase):
    def test_subscribe_py_removed(self):
        """남겨 두면 둘 중 어느 것이 진짜인지 갈린다 — 갈린 정본은 조용히 낡는다."""
        self.assertFalse(os.path.exists(os.path.join(ROOT, "bin", "subscribe.py")))


if __name__ == "__main__":
    unittest.main()
