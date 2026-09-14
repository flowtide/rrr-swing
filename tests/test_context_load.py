"""context_load — 세 원본(playbook·state·journal)의 최신을 머지해 기동 컨텍스트를 만든다.

담는 순서가 곧 우선순위다: 운영자 지시 → 규칙 → 상태 → 최근 일지. 예산은 앞 절부터
소진되므로 뒤 절(일지)이 먼저 굶는다. 통째로 빠진 절은 끝에 [예산 소진 …] 으로 알린다.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import context_load as cl  # noqa: E402

SCRIPT = os.path.join(ROOT, "bin", "context_load.py")


class ContextLoadTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.local = os.path.join(self.d, "local")
        # 원본 3층은 local/memory/ 아래다. 기동 산출물(system-prompts/)은 그 밖 — 고치는 곳과
        # 만들어지는 곳을 가른다. 경로는 테스트가 직접 적는다(코드 상수를 따라가지 않는다).
        self.memory = os.path.join(self.local, "memory")
        for layer in ("playbook", "state", "journal"):
            os.makedirs(os.path.join(self.memory, layer))

    def write(self, rel, text):
        """rel 은 memory/ 기준(예: state/2026-09-15.md)."""
        path = os.path.join(self.memory, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    # --- 층에서 최신 고르기 -------------------------------------------------------

    def test_latest_wins_in_each_layer(self):
        """'현재' = 그 층의 최신 날짜 파일. 안 고친 날은 파일이 없고, 그래도 최신이 유효하다."""
        self.write("playbook/2026-09-01.md", "옛 규칙")
        self.write("playbook/2026-09-14.md", "새 규칙")
        self.write("state/2026-09-15.md", "## 태도\n방어\n")
        out = cl.build(self.local)
        self.assertIn("새 규칙", out)
        self.assertNotIn("옛 규칙", out)

    def test_non_dated_files_are_ignored(self):
        self.write("playbook/2026-09-01.md", "규칙")
        self.write("playbook/notes.txt", "무시")
        self.write("playbook/README.md", "무시")
        self.assertEqual([os.path.basename(p) for p in [cl.latest(os.path.join(self.memory, "playbook"))]],
                         ["2026-09-01.md"])

    def test_missing_layer_gives_empty_section_not_crash(self):
        self.write("state/2026-09-15.md", "## 태도\n방어\n")
        out = cl.build(self.local)
        self.assertIn("## 규칙", out)
        self.assertIn("(없음)", out)

    # --- 운영자 지시 끌어올림 -----------------------------------------------------

    def test_directive_is_lifted_to_the_front_and_removed_from_state(self):
        """지시는 규칙보다 먼저 읽혀야 한다. 끌어올림은 절 이름으로 고르는 것이지 요약이 아니다."""
        self.write("playbook/2026-09-14.md", "# 규칙\n한도 20%\n")
        self.write("state/2026-09-15.md",
                   "# 상태\n\n## 운영자 지시\n오늘은 매매 보류.\n\n## 장세\n위험회피\n\n## 태도\n방어\n")
        out = cl.build(self.local)
        self.assertLess(out.index("## 운영자 지시"), out.index("## 규칙"))
        self.assertIn("오늘은 매매 보류.", out.split("## 규칙")[0])
        state_section = out.split("## 상태")[1].split("## 최근 일지")[0]
        self.assertNotIn("매매 보류", state_section, "지시가 상태 절에 남았다")
        self.assertIn("위험회피", state_section, "나머지 상태가 사라졌다")

    def test_state_without_directive_section_is_kept_whole(self):
        self.write("state/2026-09-15.md", "# 상태\n\n## 장세\n위험회피\n")
        body, rest = cl.split_directive("# 상태\n\n## 장세\n위험회피\n")
        self.assertIsNone(body)
        self.assertIn("위험회피", rest)
        self.assertIn("(없음)", cl.build(self.local).split("## 규칙")[0])

    def test_empty_directive_section_reads_as_absent(self):
        """절만 있고 내용이 없으면 지시가 없는 것이다."""
        body, _ = cl.split_directive("## 운영자 지시\n\n## 태도\n방어\n")
        self.assertIsNone(body)

    # --- 순서와 예산 --------------------------------------------------------------

    def test_order_is_directive_playbook_state_journal(self):
        self.write("playbook/2026-09-14.md", "규칙")
        self.write("state/2026-09-15.md", "## 운영자 지시\n지시\n\n## 태도\n방어\n")
        self.write("journal/2026-09-14.md", "일지")
        out = cl.build(self.local)
        idx = [out.index(head) for _, head in cl.SECTION_HEADS]
        self.assertEqual(idx, sorted(idx), "절 순서가 우선순위와 다르다")

    def test_journal_is_newest_first(self):
        for day in ("2026-09-12", "2026-09-13", "2026-09-14"):
            self.write(f"journal/{day}.md", f"일지 {day}")
        out = cl.build(self.local, journal_days=3)
        self.assertLess(out.index("### 2026-09-14.md"), out.index("### 2026-09-12.md"))
        self.assertIn("최근 일지 (3개, 최신부터", out)

    def test_journal_window_takes_newest_n(self):
        for day in ("2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13"):
            self.write(f"journal/{day}.md", f"일지 {day}")
        out = cl.build(self.local, journal_days=2)
        self.assertIn("### 2026-09-13.md", out)
        self.assertNotIn("### 2026-09-10.md", out)

    def test_playbook_survives_three_full_journals(self):
        """일지가 사흘치 꽉 차도 규칙은 닿아야 한다 — 이 역전이 이 순서를 만든 이유다."""
        self.write("playbook/2026-09-14.md", "# 규칙\n## 한도\n계좌 20% 상한\n")
        self.write("state/2026-09-15.md", "## 태도\n" + "방" * 5000)
        for day in ("2026-09-12", "2026-09-13", "2026-09-14"):
            self.write(f"journal/{day}.md", "일" * 5000)
        out = cl.build(self.local, budget_chars=12000, file_cap_chars=4000)
        self.assertIn("계좌 20% 상한", out, "규칙이 굶었다")
        self.assertLessEqual(len(out), 12000)

    def test_file_cap_truncates_with_marker(self):
        self.write("playbook/2026-09-14.md", "P" * 500)
        out = cl.build(self.local, file_cap_chars=100, budget_chars=12000)
        self.assertIn(cl.TRUNC, out)
        self.assertLessEqual(out.split("## 상태")[0].count("P"), 100)

    def test_starved_section_is_announced_not_silent(self):
        """통째로 빠진 절은 끝에 알린다 — '비어 있다' 와 '못 읽었다' 가 같아 보이면 안 된다."""
        self.write("playbook/2026-09-14.md", "P" * 300)
        self.write("state/2026-09-15.md", "S" * 300)
        self.write("journal/2026-09-14.md", "J" * 300)
        out = cl.build(self.local, budget_chars=400, file_cap_chars=4000)
        self.assertIn("JOURNAL", out.split("[예산 소진")[1])
        self.assertNotIn("## 최근 일지", out)
        self.assertLessEqual(len(out), 400)

    def test_starved_note_lists_exactly_what_is_missing(self):
        self.write("playbook/2026-09-14.md", "P" * 300)
        self.write("state/2026-09-15.md", "S" * 300)
        self.write("journal/2026-09-14.md", "J" * 300)
        for budget in (400, 200, 120, 60):
            with self.subTest(budget=budget):
                out = cl.build(self.local, budget_chars=budget, file_cap_chars=4000)
                self.assertLessEqual(len(out), budget)
                claimed = out.split("[예산 소진 — 읽지 못한 절: ")[1].split("]")[0].split(", ")
                actual = [lab for lab, head in cl.SECTION_HEADS if head not in out]
                self.assertEqual(sorted(claimed), sorted(actual))

    def test_defaults_pinned(self):
        self.assertEqual((cl.DEFAULT_JOURNAL_DAYS, cl.DEFAULT_BUDGET_CHARS, cl.DEFAULT_FILE_CAP_CHARS),
                         (3, 12000, 4000))

    # --- CLI --------------------------------------------------------------------

    def _run(self, *args):
        return subprocess.run([sys.executable, SCRIPT, "--local", self.local, "--config", "/nonexistent", *args],
                              capture_output=True, text=True, cwd=ROOT)

    def test_cli_refuses_when_no_source_exists(self):
        """원본이 하나도 없으면 최초 설치 상태다 — 조용히 빈 컨텍스트를 내지 않는다."""
        r = self._run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("init_local", r.stderr)

    def test_cli_writes_artifact_and_prints_path(self):
        self.write("state/2026-09-15.md", "## 태도\n방어\n")
        out = os.path.join(self.local, "system-prompts", "2026-09-15.md")
        r = self._run("--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), out)
        with open(out, encoding="utf-8") as f:
            self.assertIn("## 태도", f.read())

    def test_cli_reads_budget_from_config(self):
        self.write("state/2026-09-15.md", "S" * 5000)
        cfgp = os.path.join(self.d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"memory": {"journal_days": 1, "load_budget_chars": 600, "file_cap_chars": 300}}, f)
        r = subprocess.run([sys.executable, SCRIPT, "--local", self.local, "--config", cfgp],
                           capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLessEqual(len(r.stdout), 600)


if __name__ == "__main__":
    unittest.main()
