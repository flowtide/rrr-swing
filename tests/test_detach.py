"""bin/detach.py — 명령을 **자기 세션·자기 프로세스 그룹**으로 띄운다.

start.sh 는 어댑터를 `&` 로 띄운 뒤 `exec` 로 claude 가 된다. 스크립트에서는 job control 이
꺼져 있어 `&` 가 새 그룹을 만들지 않고, exec 는 PID 를 지키므로 어댑터의 그룹이 곧 claude 의
그룹이 된다 — 그 그룹에 가는 시그널을 어댑터도 함께 맞고, 그 죽음은 EXIT 줄 없이 조용하다
(zombie, ppid=claude, 같은 pgid). 어댑터가 유일한 wake 소스라 세션은 그것을 알아차릴 수 없다.

macOS 에는 setsid(1) 이 없어 setsid(2) 뒤 exec 하는 작은 스크립트를 둔다. exec 라 pid 가
그대로이므로 `$!` 가 곧 어댑터다.
"""
import os
import subprocess
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DETACH = os.path.join(ROOT, "bin", "detach.py")
START = os.path.join(ROOT, "bin", "start.sh")


def _wait(pred, sec=3.0):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


class DetachTest(unittest.TestCase):
    def _spawn(self, *cmd):
        p = subprocess.Popen([sys.executable, DETACH, *cmd],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        return p

    def test_child_leads_its_own_session_and_group(self):
        """pgid == sid == pid. 띄운 쪽의 그룹에 남지 않는다."""
        p = self._spawn(sys.executable, "-c", "import time; time.sleep(10)")
        self.assertTrue(_wait(lambda: os.getpgid(p.pid) == p.pid),
                        f"자기 그룹의 리더가 아니다: pgid={os.getpgid(p.pid)} pid={p.pid} (띄운 셸 pgid={os.getpgid(0)})")
        self.assertEqual(os.getsid(p.pid), p.pid, "자기 세션의 리더가 아니다")
        self.assertNotEqual(os.getpgid(p.pid), os.getpgid(0), "띄운 프로세스와 같은 그룹이다")

    def test_pid_survives_exec(self):
        """`$!` 가 곧 어댑터여야 한다 — 중간 프로세스가 남으면 pid 파일이 래퍼를 가리킨다."""
        p = self._spawn(sys.executable, "-c", "import os,time; print(os.getpid(), flush=True); time.sleep(10)")
        # 자식이 exec 뒤 자기 pid 를 찍는다 — Popen 이 아는 pid 와 같아야 한다.
        p2 = subprocess.Popen([sys.executable, DETACH, sys.executable, "-c",
                               "import os; print(os.getpid())"], stdout=subprocess.PIPE, text=True)
        out, _ = p2.communicate(timeout=10)
        self.assertEqual(int(out.strip()), p2.pid, "exec 뒤 pid 가 바뀌었다 — 래퍼가 중간에 남는다")

    def test_no_controlling_terminal(self):
        """제어 터미널이 없어야 터미널 쪽 시그널(HUP·INT·QUIT)이 닿지 않는다."""
        p = self._spawn(sys.executable, "-c", "import time; time.sleep(10)")
        _wait(lambda: os.getpgid(p.pid) == p.pid)
        tty = subprocess.run(["ps", "-o", "tty=", "-p", str(p.pid)], capture_output=True, text=True).stdout.strip()
        self.assertIn(tty, ("??", "?", ""), f"제어 터미널이 붙어 있다: {tty!r}")

    def test_exits_2_when_command_is_missing(self):
        r = subprocess.run([sys.executable, DETACH, "definitely-not-a-command-xyz"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("definitely-not-a-command-xyz", r.stderr)

    def test_start_sh_launches_the_adapter_through_detach(self):
        """기동 줄 두 개(herdr·file) 모두 detach 를 거친다 — 하나만 고치면 --adapter-only 가 옛 길로 뜬다."""
        with open(START, encoding="utf-8") as f:
            src = f.read()
        launch = src.split("인바운드 어댑터 백그라운드 기동")[1].split("RS_ADAPTER_PID=$!")[0]
        lines = [l for l in launch.splitlines() if "bin/inbound_rrr.py" in l or "$ADAPTER" in l]
        lines = [l for l in lines if l.strip().endswith("&")]
        self.assertEqual(len(lines), 2, lines)
        for l in lines:
            self.assertIn("bin/detach.py", l, f"detach 를 거치지 않는 기동 줄: {l.strip()}")
            self.assertIn("< /dev/null", l, f"stdin 이 터미널에 남는 기동 줄: {l.strip()}")


if __name__ == "__main__":
    unittest.main()
