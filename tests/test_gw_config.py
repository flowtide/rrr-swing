"""게이트웨이(gw) 설정·주소 해석·X-API-Key 인증 헤더 및 고정 경로 검증 (W5 PR-1)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import inbound_core as gc  # noqa: E402
import inbound_rrr as rr  # noqa: E402

START = os.path.join(ROOT, "bin", "start.sh")


class GwConfigTest(unittest.TestCase):
    def setUp(self):
        self.orig_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)

    def test_env_var_overrides_config_base_url(self):
        os.environ["RS_GW_BASE_URL"] = "http://override-gw:15410"
        cfg = {"gw": {"base_url": "http://config-gw:15410", "api_key": "test-key"}}
        gw = gc.resolve_gw_config(cfg)
        self.assertEqual(gw.base_url, "http://override-gw:15410")

    def test_env_var_trailing_slash_stripped(self):
        os.environ["RS_GW_BASE_URL"] = "http://override-gw:15410/"
        cfg = {"gw": {"base_url": "http://config-gw:15410", "api_key": "test-key"}}
        gw = gc.resolve_gw_config(cfg)
        self.assertEqual(gw.base_url, "http://override-gw:15410")

    def test_config_base_url_used_when_no_env_override(self):
        os.environ.pop("RS_GW_BASE_URL", None)
        cfg = {"gw": {"base_url": "http://config-gw:15410/", "api_key": "test-key"}}
        gw = gc.resolve_gw_config(cfg)
        self.assertEqual(gw.base_url, "http://config-gw:15410")

    def test_missing_both_refuses_with_value_error(self):
        os.environ.pop("RS_GW_BASE_URL", None)
        bad_configs = [
            {},
            {"gw": {}},
            {"gw": {"base_url": ""}},
            {"gw": {"base_url": "   "}},
            {"gw": {"base_url": "<YOUR_GW_URL>"}},
        ]
        for cfg in bad_configs:
            with self.subTest(cfg=cfg):
                with self.assertRaises(ValueError):
                    gc.resolve_gw_config(cfg)

    def test_missing_api_key_refuses_with_value_error(self):
        """키는 config.gw.api_key 에서 온다. 환경변수에 있어도 config 가 비면 거부한다."""
        os.environ.pop("RS_GW_BASE_URL", None)
        os.environ["RRR_GW_API_KEY"] = "env-key-must-not-be-used"
        cfg = {"gw": {"base_url": "http://gw:15410"}}
        with self.assertRaises(ValueError):
            gc.resolve_gw_config(cfg)

    def test_key_comes_from_config_not_env(self):
        """키는 config.gw.api_key 하나에서 온다 — 환경변수로 옮겨 담지 않는다."""
        os.environ.pop("RS_GW_BASE_URL", None)
        os.environ["RRR_GW_API_KEY"] = "env-key-must-not-be-used"
        cfg = {"gw": {"base_url": "http://gw:15410", "api_key": "config-key"}}
        gw = gc.resolve_gw_config(cfg)
        self.assertEqual(gw.api_key, "config-key")
        self.assertFalse(hasattr(gw, "api_key_env"), "env 이름 중간 단계는 없어졌다")

    def test_assembled_urls_have_fixed_api_prefix_without_duplicates(self):
        os.environ.pop("RS_GW_BASE_URL", None)
        os.environ["RRR_GW_API_KEY"] = "k"
        cfg = {"gw": {"base_url": "http://gw:15410/", "api_key": "test-key"}}
        gw = gc.resolve_gw_config(cfg)
        self.assertEqual(gw.events_stream_url, "http://gw:15410/api/events/stream")
        self.assertEqual(gw.events_replay_url, "http://gw:15410/api/events")
        self.assertEqual(gw.health_url, "http://gw:15410/api/health")
        self.assertEqual(gw.api_url("/stocks/005930/context"), "http://gw:15410/api/stocks/005930/context")
        self.assertEqual(gw.api_url("stocks/005930/context"), "http://gw:15410/api/stocks/005930/context")
        self.assertEqual(gw.api_url("/api/stocks/005930/context"), "http://gw:15410/api/stocks/005930/context")

    def test_auth_header_is_x_api_key(self):
        os.environ.pop("RS_GW_BASE_URL", None)
        os.environ["RRR_GW_API_KEY"] = "env-key-must-not-be-used"
        cfg = {"gw": {"base_url": "http://gw:15410", "api_key": "secret-token-123"}}
        gw = gc.resolve_gw_config(cfg)
        hdrs = gw.headers()
        self.assertEqual(hdrs.get("X-API-Key"), "secret-token-123")
        self.assertNotIn("Authorization", hdrs)

    def test_inbound_rrr_build_stream_url_defaults_to_fixed_path(self):
        sub = {"symbols": ["005930"], "event_types": ["support_return"]}
        url = rr.build_stream_url("http://gw:15410", sub, since="$")
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(url).query)
        self.assertEqual(q.get("since"), ["$"])
        self.assertEqual(q.get("symbols"), ["005930"])

    def _launch(self, gw_cfg, env_extra=None):
        """실기동 경로로 start.sh 를 돌린다.

        gw 점검은 `--check-gw-health` 한 곳뿐이고 그것은 실기동 경로에 있다(--check-only 는
        네트워크 0 을 유지한다). 설정 오류는 해석 단계에서 걸리므로 네트워크를 타지 않는다.
        """
        with tempfile.TemporaryDirectory() as d:
            cfgp = os.path.join(d, "config.json")
            with open(cfgp, "w", encoding="utf-8") as f:
                json.dump({"mode": "dry_run", "account_id": "acct", "gw": gw_cfg}, f)
            # 스텁 런타임을 PATH 앞에 둔다. 여기까지 오면 테스트는 이미 실패지만, 진짜 claude 가
            # 뜨면 그 실패가 hang 으로 나타난다(gw 점검을 지우는 뮤턴트에서 실제로 그랬다).
            stubs = os.path.join(d, "stubs")
            os.makedirs(stubs)
            with open(os.path.join(stubs, "claude"), "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env bash\necho STUB_RUNTIME_LAUNCHED\n")
            os.chmod(os.path.join(stubs, "claude"), 0o755)
            env = dict(os.environ, FORCE_OUTSIDE_HERDR="1", RS_CONFIG=cfgp,
                       RS_LOCAL=os.path.join(d, "local"),
                       PATH=stubs + os.pathsep + os.environ.get("PATH", ""))
            env.pop("RS_GW_BASE_URL", None)
            env.update(env_extra or {})
            r = subprocess.run(["bash", START, "--role", "lead"], capture_output=True,
                               text=True, env=env, cwd=ROOT, timeout=60)
            assert "STUB_RUNTIME_LAUNCHED" not in r.stdout, "gw 점검을 통과해 런타임까지 갔다"
            return r

    def test_start_sh_rejects_missing_gw_base_url(self):
        r = self._launch({"base_url": ""}, {"RRR_GW_API_KEY": "dummy"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("base_url", r.stdout + r.stderr)

    def test_start_sh_rejects_missing_gw_api_key(self):
        """키는 config 에서만 온다 — 환경변수에 있어도 통과하면 안 된다."""
        r = self._launch({"base_url": "http://127.0.0.1:1"},
                         {"RRR_GW_API_KEY": "env-key-must-not-be-used"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("api_key", r.stdout + r.stderr)

    def test_check_only_stays_offline(self):
        """--check-only 는 네트워크를 타지 않는다 — 도달 불가 주소로도 통과해야 한다."""
        with tempfile.TemporaryDirectory() as d:
            cfgp = os.path.join(d, "config.json")
            with open(cfgp, "w", encoding="utf-8") as f:
                json.dump({"mode": "dry_run", "account_id": "acct", "operator_channel": "console",
                           "gw": {"base_url": "http://127.0.0.1:1", "api_key": "k"}}, f)
            env = dict(os.environ, FORCE_OUTSIDE_HERDR="1", RS_CONFIG=cfgp)
            env.pop("RS_GW_BASE_URL", None)
            r = subprocess.run(["bash", START, "--check-only"], capture_output=True,
                               text=True, env=env, cwd=ROOT, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("check-only ok", r.stdout)


if __name__ == "__main__":
    unittest.main()
