"""init_local.sh — local/ 골격 + 첫 원본(state·playbook) 준비. 멱등. 템플릿은 제목·절 이름만(값 0).

기억은 날짜 층으로 쌓인다(docs/05-context.md). 첫 원본만 오늘 날짜로 깔고, 이후 날짜 파일은
고치는 쪽이 최신을 복사해 만든다 — 그래서 seed 는 그 층이 **비어 있을 때만** 한다.
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "bin", "init_local.sh")
TEMPLATES = os.path.join(ROOT, "docs", "templates")
# 원본 3층은 memory/ 아래, 기동 산출물 system-prompts/ 는 그 밖.
DIRS = ("memory/playbook", "memory/state", "memory/journal", "system-prompts",
        "stories", "meetings", "lessons", "consults", "packets", "reports")
TODAY = "2026-09-15"


class InitLocalTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.local = os.path.join(self.d, "local")

    def run_init(self):
        env = dict(os.environ, RS_TODAY=TODAY)
        return subprocess.run(["bash", SCRIPT, self.local], capture_output=True, text=True, cwd=ROOT, env=env)

    def test_creates_skeleton_and_templates(self):
        r = self.run_init()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for d in DIRS:
            self.assertTrue(os.path.isdir(os.path.join(self.local, d)), d)
        for layer, tpl in (("memory/state", "state.md"), ("memory/playbook", "playbook.md")):
            seeded = os.path.join(self.local, layer, f"{TODAY}.md")
            with open(seeded, encoding="utf-8") as f, open(os.path.join(TEMPLATES, tpl), encoding="utf-8") as g:
                self.assertEqual(f.read(), g.read(), layer)
        self.assertIn(f"created={len(DIRS) + 2} kept=0", r.stdout)

    def test_idempotent_keeps_existing_files(self):
        self.run_init()
        state = os.path.join(self.local, "memory", "state", f"{TODAY}.md")
        with open(state, "w", encoding="utf-8") as f:
            f.write("## 장세\n관망\n")
        with open(os.path.join(self.local, "memory", "journal", "2026-06-25.md"), "w", encoding="utf-8") as f:
            f.write("x")
        r = self.run_init()
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(state, encoding="utf-8") as f:
            self.assertEqual(f.read(), "## 장세\n관망\n")
        self.assertTrue(os.path.exists(os.path.join(self.local, "memory", "journal", "2026-06-25.md")))
        self.assertIn(f"created=0 kept={len(DIRS) + 2}", r.stdout)

    def test_seed_skips_when_layer_already_has_a_dated_file(self):
        """이미 다른 날짜 파일이 있으면 오늘 것을 깔지 않는다 — 날짜 파일은 고치는 쪽이 만든다."""
        self.run_init()
        os.remove(os.path.join(self.local, "memory", "state", f"{TODAY}.md"))
        old = os.path.join(self.local, "memory", "state", "2026-09-01.md")
        with open(old, "w", encoding="utf-8") as f:
            f.write("옛 상태\n")
        self.run_init()
        self.assertFalse(os.path.exists(os.path.join(self.local, "memory", "state", f"{TODAY}.md")))
        self.assertTrue(os.path.exists(old))

    def test_templates_have_headings_and_no_values(self):
        names = sorted(os.listdir(TEMPLATES))
        self.assertEqual(names, ["journal.md", "meeting.md", "playbook.md", "state.md", "story.md"])
        with open(os.path.join(TEMPLATES, "state.md"), encoding="utf-8") as f:
            self.assertIn("## 운영자 지시", f.read(), "상태 템플릿에 운영자 지시 절이 없다")
        for n in names:
            with open(os.path.join(TEMPLATES, n), encoding="utf-8") as f:
                text = f.read()
            self.assertTrue(text.startswith("#") or text.startswith("---"), n)
            for tok in ("336260", "005930", "10%", "20%", "1,000", "0000"):
                self.assertNotIn(tok, text, (n, tok))
        with open(os.path.join(TEMPLATES, "playbook.md"), encoding="utf-8") as f:
            pb = f.read()
        for sec in ("한도", "손실 정책", "진입 기준", "청산 기준", "시장 상황별 태도", "바꾼 이력"):
            self.assertIn(f"## {sec}", pb, sec)
        with open(os.path.join(TEMPLATES, "story.md"), encoding="utf-8") as f:
            st = f.read()
        fm = st.split("---")[1]
        for key in ("symbol", "plan_id", "thesis", "entry_zone", "target", "invalidation_conditions", "horizon_d", "recheck_by", "status", "activated_at"):
            self.assertIn(f"{key}:", fm, key)


if __name__ == "__main__":
    unittest.main()
