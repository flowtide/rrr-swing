"""RS_ROLE=analyst(분석 역할)면 bin/order.py 는 exit 30."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RoleGateTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfgp = os.path.join(self.d, "config.json")
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump({"mode": "dry_run", "account_id": "acct", "rules": {}}, f)

    def run_order(self, *, role):
        env = dict(os.environ)
        env.pop("RS_ROLE", None)
        if role is not None:
            env["RS_ROLE"] = role
        return subprocess.run([sys.executable, os.path.join(ROOT, "bin", "order.py"), "--order-ref", "x", "--config", self.cfgp, "--ledger", os.path.join(self.d, "l.jsonl"), "--broker", "none"],
                              capture_output=True, text=True, env=env)

    def test_analyst_cannot_order(self):
        r = self.run_order(role="analyst")
        self.assertEqual(r.returncode, 30, r.stdout + r.stderr)
        self.assertIn("analyst", r.stdout + r.stderr)
        self.assertNotIn("order not found", r.stdout)  # 역할 거부가 원장 조회보다 먼저

    def test_session_role_passes_role_check(self):
        for role in ("session", None):
            r = self.run_order(role=role)
            self.assertEqual(r.returncode, 30, r.stderr)  # order 없음 — 역할은 통과했고 원장 조회에서 멈춘다
            self.assertNotIn("analyst", r.stdout + r.stderr)
            self.assertIn("order not found", r.stdout)


if __name__ == "__main__":
    unittest.main()
