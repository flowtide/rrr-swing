"""turn_context — 매 턴 주입되는 짧은 컨텍스트.

시스템 프롬프트는 그날의 기준이라 장중에 바뀌지 않는다. 변화는 이쪽이 나른다.
매 턴 실리므로 짧아야 하고, 무엇이 중요한지 고르는 판단은 하지 않는다 — 절 이름으로
고르고 상한에서 자를 뿐이다.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import turn_context as tc  # noqa: E402

SCRIPT = os.path.join(ROOT, "bin", "turn_context.py")

STATE = """# 상태

## 운영자 지시
오늘은 매매 보류. 시험 운행만.

## 장세
위험회피. KOSPI -3.2%.

## 태도
방어 유지. 신규 진입 불가.

## 보유
006800 240주 평단 51,720

## 열린 협의
c-006800 트리거 33,000, 기한 14:15

## 메모
코드 이슈 2건
"""


class TurnContextTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.local = os.path.join(self.d, "local")
        # 상태 원본은 local/memory/state/ 다(경로는 테스트가 직접 적는다).
        os.makedirs(os.path.join(self.local, "memory", "state"))
        self.state = os.path.join(self.local, "memory", "state", "2026-09-15.md")
        with open(self.state, "w", encoding="utf-8") as f:
            f.write(STATE)

    def test_carries_mode_directive_stance_and_consults(self):
        out = tc.build(self.local, mode="confirm")
        self.assertIn("mode=confirm", out)
        self.assertIn("오늘은 매매 보류", out)
        self.assertIn("방어 유지", out)
        self.assertIn("트리거 33,000", out)

    def test_leaves_out_what_the_session_fetches_itself(self):
        """보유·장세·메모는 빼는 값이다 — 매 턴 실으면 비싸고 세션이 직접 본다."""
        out = tc.build(self.local, mode="confirm")
        for gone in ("위험회피", "006800 240주", "코드 이슈"):
            self.assertNotIn(gone, out, f"턴 컨텍스트에 실리면 안 되는 값: {gone}")

    def test_order_puts_directive_first(self):
        """지시가 태도보다 먼저다 — 상한에 걸리면 뒤가 잘린다."""
        out = tc.build(self.local, mode="confirm")
        self.assertLess(out.index("## 운영자 지시"), out.index("## 태도"))
        self.assertLess(out.index("## 태도"), out.index("## 열린 협의"))

    def test_uses_the_latest_state_file(self):
        with open(os.path.join(self.local, "memory", "state", "2026-09-16.md"), "w", encoding="utf-8") as f:
            f.write("## 태도\n공격\n")
        out = tc.build(self.local, mode="dry_run")
        self.assertIn("공격", out)
        self.assertNotIn("방어 유지", out)
        self.assertIn("2026-09-16.md", out)

    def test_reports_the_state_file_and_its_mtime(self):
        """낡음 판단은 세션이 한다 — 코드는 언제 고쳐졌는지만 알린다."""
        out = tc.build(self.local, mode="confirm")
        self.assertIn("state/2026-09-15.md", out)
        self.assertIn("수정 ", out)

    def test_missing_section_reads_as_absent_not_missing_output(self):
        with open(self.state, "w", encoding="utf-8") as f:
            f.write("# 상태\n\n## 태도\n방어\n")
        out = tc.build(self.local, mode="confirm")
        self.assertIn("## 운영자 지시\n(없음)", out)
        self.assertIn("## 열린 협의\n(없음)", out)

    def test_no_state_file_says_so(self):
        out = tc.build(os.path.join(self.d, "empty"), mode="dry_run")
        self.assertIn("상태 파일이 없다", out)

    def test_cap_is_enforced(self):
        with open(self.state, "w", encoding="utf-8") as f:
            f.write("## 태도\n" + "방" * 5000 + "\n")
        out = tc.build(self.local, mode="confirm", cap_chars=300)
        self.assertLessEqual(len(out), 300)

    def test_default_cap_is_one_thousand(self):
        self.assertEqual(tc.DEFAULT_CAP_CHARS, 1000)

    def test_sections_are_pinned(self):
        """싣는 절은 코드에 고정한다 — 늘리면 매 턴 비용이 그만큼 는다."""
        self.assertEqual(tc.SECTIONS, ("## 운영자 지시", "## 태도", "## 열린 협의"))

    def test_cli_reads_mode_from_config(self):
        cfgp = os.path.join(self.d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"mode": "confirm"}, f)
        r = subprocess.run([sys.executable, SCRIPT, "--local", self.local, "--config", cfgp],
                           capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("mode=confirm", r.stdout)

    def test_cli_succeeds_without_config(self):
        """훅에서 돌므로 실패하면 안 된다 — 컨텍스트가 없다고 턴을 막지 않는다."""
        r = subprocess.run([sys.executable, SCRIPT, "--local", self.local, "--config", "/nonexistent"],
                           capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("mode=미상", r.stdout)


if __name__ == "__main__":
    unittest.main()
