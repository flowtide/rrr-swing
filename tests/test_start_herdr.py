"""start.sh: herdr pane 게이트 (D15) 와 pane 판별 (bin/herdr_pane.py).

두 가지를 못박는다.

1. 이름 존재를 요구하면 순환이다 — 그 이름이 붙을 에이전트를 만드는 것이 start.sh 자신이고,
   에이전트가 없는 pane 은 `herdr agent list` 에 나오지 않는다. 그래서 게이트는 이름을
   요구하지 않고, 이름은 런타임이 뜬 뒤 start.sh 가 붙인다.
2. pane 은 **포커스가 아니라 실행 위치**로 정한다. `herdr pane current` 는 포커스된 pane 을
   돌려줄 뿐이라, 기동 직후 포커스를 옮기면 역할 이름이 엉뚱한 pane 에 붙는다. 라이브에서
   rs-lead 가 exec 쓸 pane 에 붙어 다이제스트 배달이 한 건 실패했다.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import herdr_pane as hp  # noqa: E402

START = os.path.join(ROOT, "bin", "start.sh")

# pane list 는 파일 내용을, process-info 는 고정 shell_pid 를 돌려준다.
# agent rename 은 호출 인자를 기록한다.
#
# `pane current` 는 **일부러 미끼 pane 을 돌려준다**. 실제 herdr 은 이 질의에 늘 답하므로,
# 스텁이 답하지 않으면 "포커스 폴백" 뮤턴트가 어차피 빈 값을 받아 테스트를 통과해 버린다
# (실제로 그 뮤턴트가 살아남았다). 미끼를 두면 폴백이 성공해 버리고, 거부를 요구하는
# 테스트가 그때 죽는다.
HERDR_STUB = """#!/usr/bin/env bash
if [ "$1" = "pane" ] && [ "$2" = "current" ]; then
    printf '{"result":{"pane":{"pane_id":"%s"}}}' "${RS_TEST_FOCUSED_PANE}"
    exit 0
fi
if [ "$1" = "pane" ] && [ "$2" = "list" ]; then
    cat "${RS_TEST_HERDR_PANE_FILE}"
    exit 0
fi
if [ "$1" = "pane" ] && [ "$2" = "process-info" ]; then
    printf '{"result":{"process_info":{"shell_pid":%s,"foreground_processes":[]}}}' "${RS_TEST_PANE_SHELL_PID}"
    exit 0
fi
if [ "$1" = "agent" ] && [ "$2" = "rename" ]; then
    echo "$3 $4" >> "${RS_TEST_HERDR_RENAME_LOG}"
    exit 0
fi
exit 1
"""


class StartHerdrGateTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.herdr_bin = os.path.join(self.d, "herdr")
        with open(self.herdr_bin, "w", encoding="utf-8") as f:
            f.write(HERDR_STUB)
        os.chmod(self.herdr_bin, 0o755)
        self.pane_file = os.path.join(self.d, "panes.json")
        self.rename_log = os.path.join(self.d, "rename.log")
        self.cfgp = os.path.join(self.d, "config.json")
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump({
                "mode": "dry_run",
                "account_id": "acct",
                "operator_channel": "console",
                "gw": {"base_url": "http://127.0.0.1:1", "api_key": "test-key"},
                "inbound": {"source": "rrr_stream", "target": "rs-lead"},
            }, f)

    def run_start(self, *args, pane_id="w9:p1", shell_pid=None, env_extra=None):
        panes = {"result": {"panes": [{"pane_id": pane_id}] if pane_id else []}}
        with open(self.pane_file, "w", encoding="utf-8") as f:
            json.dump(panes, f)
        env = dict(
            os.environ,
            RS_CONFIG=self.cfgp,
            HERDR_BIN=self.herdr_bin,
            RS_TEST_HERDR_PANE_FILE=self.pane_file,
            RS_TEST_HERDR_RENAME_LOG=self.rename_log,
            # 이 프로세스(pytest)는 start.sh 셸의 조상이다 — 그래서 "그 pane 안"으로 풀린다.
            RS_TEST_PANE_SHELL_PID=str(os.getpid() if shell_pid is None else shell_pid),
            RS_TEST_FOCUSED_PANE="w9:pDECOY",   # 포커스 폴백이 되살아나면 이 값이 잡힌다
        )
        env.pop("FORCE_OUTSIDE_HERDR", None)
        env.pop("RS_PANE_ID", None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(["bash", START, "--check-only", *args], capture_output=True, text=True, env=env, cwd=ROOT)

    def test_passes_when_this_shell_is_inside_a_pane(self):
        r = self.run_start()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("check-only ok", r.stdout)

    def test_refuses_when_this_shell_is_in_no_pane(self):
        """어느 pane 의 프로세스 트리에도 없으면 기동하지 않는다.

        포커스 pane 으로 대신 붙이면 엉뚱한 pane 이 역할 이름을 갖는다 — 라이브에서 난 일이다.
        스텁의 `pane current` 는 미끼 pane 을 돌려준다 — 포커스 폴백이 되살아나면 기동이
        성공해 버리므로 여기서 죽는다.
        """
        r = self.run_start(shell_pid=1)  # 조상이 아닌 pid
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("herdr pane 을 찾지 못했다", out)
        self.assertNotIn("w9:pDECOY", out)
        self.assertFalse(os.path.exists(self.rename_log), "미끼 pane 에 이름이 붙었다")

    def test_refuses_when_no_panes_exist(self):
        r = self.run_start(pane_id=None)
        self.assertEqual(r.returncode, 1)
        self.assertIn("herdr pane 을 찾지 못했다", r.stdout + r.stderr)

    def test_does_not_require_pre_existing_agent_name(self):
        """부트스트랩 순환 방지: 이름이 아직 없어도 기동할 수 있어야 한다.

        스텁은 `agent list` 를 지원하지 않는다(exit 1). 옛 게이트였다면 여기서 거부됐다.
        """
        r = self.run_start()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_explicit_pane_id_overrides_detection(self):
        """운영자가 pane 을 직접 지정하면 판별을 건너뛴다(판별이 불가능한 환경 대비)."""
        r = self.run_start(shell_pid=1, env_extra={"RS_PANE_ID": "w9:p7"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_force_outside_herdr_bypasses_check(self):
        r = self.run_start(pane_id=None, env_extra={"FORCE_OUTSIDE_HERDR": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class HerdrPaneResolveTest(unittest.TestCase):
    """pane 판별 자체 — 조상 교차."""

    def test_ancestors_includes_self_and_parent(self):
        anc = hp.ancestors(os.getpid())
        self.assertIn(os.getpid(), anc)
        self.assertIn(os.getppid(), anc)

    def test_ancestors_terminates_on_pid_one(self):
        self.assertEqual(hp.ancestors(1), set())

    def _stub(self, panes, pid_by_pane):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "herdr")
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\n")
            f.write('if [ "$1" = "pane" ] && [ "$2" = "list" ]; then\n')
            f.write("  cat <<'J'\n%s\nJ\n  exit 0\nfi\n" % json.dumps({"result": {"panes": [{"pane_id": p} for p in panes]}}))
            f.write('if [ "$1" = "pane" ] && [ "$2" = "process-info" ]; then\n  case "$4" in\n')
            for pane, pid in pid_by_pane.items():
                f.write('    %s) printf \'{"result":{"process_info":{"shell_pid":%d,"foreground_processes":[]}}}\' ;;\n' % (pane, pid))
            f.write("    *) exit 1 ;;\n  esac\n  exit 0\nfi\nexit 1\n")
        os.chmod(path, 0o755)
        return path

    def test_picks_the_pane_containing_this_process_not_the_first_one(self):
        """첫 pane 이 아니라 **내가 들어 있는** pane 을 고른다.

        포커스/순서로 고르면 오늘처럼 exec 쓸 pane 에 lead 이름이 붙는다.
        """
        me = os.getpid()
        stub = self._stub(["w3:p1", "w3:p2", "w3:p3"], {"w3:p1": 1, "w3:p2": me, "w3:p3": 1})
        self.assertEqual(hp.resolve_self_pane(me, stub), "w3:p2")

    def test_returns_none_when_no_pane_contains_this_process(self):
        stub = self._stub(["w3:p1", "w3:p2"], {"w3:p1": 1, "w3:p2": 1})
        self.assertIsNone(hp.resolve_self_pane(os.getpid(), stub))

    def test_matches_through_the_parent_chain(self):
        """pane 의 셸은 내 부모다 — 내 pid 가 아니라 조상으로 맞춘다."""
        stub = self._stub(["w3:p1"], {"w3:p1": os.getppid()})
        self.assertEqual(hp.resolve_self_pane(os.getpid(), stub), "w3:p1")


if __name__ == "__main__":
    unittest.main()
