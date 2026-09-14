"""start.sh 운전 모드: config.mode ∈ dry_run|confirm 만.
direct 는 폐기되었으며, 주문은 MCP 도구로 내되 사용자 확인 뒤에만 집행한다.
판정 파일·운영자 ack 같은 전환 조건은 없다(전환 = 운영자의 config.mode 변경 + 재기동). --check-only 로 hermetic."""
import json
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = os.path.join(ROOT, "bin", "start.sh")


class StartModesTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(ROOT, "config", "config.example.json"), encoding="utf-8") as f:
            self.example = json.load(f)
        self.cfg = {
            "mode": "confirm",
            "account_id": "acct",
            "operator_channel": "console",
            "gw": {"base_url": "http://127.0.0.1:1", "api_key": "test-key"},
            "inbound": {"source": "rrr_stream"},
        }
        self.cfgp = os.path.join(self.d, "config.json")

    def write_cfg(self, **over):
        c = dict(self.cfg, **over)
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump(c, f)

    def run_start(self, *args):
        env = dict(os.environ, FORCE_OUTSIDE_HERDR="1", RS_CONFIG=self.cfgp, RRR_GW_API_KEY="dummy")
        for k in ("KIWOOM_ACCOUNT", "KIWOOM_ACCESS_TOKEN"):
            env.pop(k, None)
        return subprocess.run(["bash", START, "--check-only", *args], capture_output=True, text=True, env=env, cwd=ROOT)

    def test_example_config_has_only_two_modes_documented(self):
        self.assertEqual(self.example["mode"], "dry_run")
        self.assertNotIn("gate", self.example)
        self.assertEqual([k for k in self.example if "ttl_min" in k], [])  # 건별 사인오프 TTL 키 없음
        self.assertIn("confirm", self.example["_mode"])
        self.assertNotIn("direct", self.example["_mode"])
        # rules, kill_file, max_attempts_per_digest, broker, eod_cancel 블록 삭제 확인
        self.assertNotIn("rules", self.example)
        self.assertNotIn("_rules", self.example)
        self.assertNotIn("kill_file", self.example)
        self.assertNotIn("max_attempts_per_digest", self.example)
        self.assertNotIn("broker", self.example)
        self.assertNotIn("_broker", self.example)
        self.assertNotIn("eod_cancel", self.example)
        self.assertNotIn("_eod_cancel", self.example)

    def test_dry_run_check_only_ok(self):
        self.write_cfg(mode="dry_run")
        r = self.run_start()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("check-only ok mode=dry_run", r.stdout)
        self.assertNotIn("gate", r.stdout)

    def test_confirm_check_only_ok(self):
        self.write_cfg(mode="confirm")
        r = self.run_start()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("check-only ok mode=confirm", r.stdout)
        self.assertEqual(os.listdir(self.d), ["config.json"])

    def test_confirm_does_not_require_broker_credentials(self):
        """자격증명은 gw 가 가지므로 confirm 모드 기동 시 브로커 자격증명을 요구하지 않는다."""
        self.write_cfg(mode="confirm")
        r = self.run_start()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("KIWOOM_ACCOUNT", r.stdout + r.stderr)
        self.assertNotIn("KIWOOM_ACCESS_TOKEN", r.stdout + r.stderr)

    def test_direct_mode_refused(self):
        """direct 모드는 폐기되었으므로 기동 거부되어야 한다."""
        self.write_cfg(mode="direct")
        r = self.run_start()
        self.assertEqual(r.returncode, 1)
        self.assertIn("dry_run|confirm", r.stdout + r.stderr)

    def test_other_mode_values_refused(self):
        for m in ("paper", "semi_auto", ""):
            self.write_cfg(mode=m)
            r = self.run_start()
            self.assertEqual(r.returncode, 1, m)
            self.assertIn("dry_run|confirm", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
