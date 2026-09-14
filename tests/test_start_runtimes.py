"""start.sh: 기동 명령·부트 프롬프트·운영자 채널 주입·--check-only·--dry-launch(스텁 런타임). 실기동 0.

MCP 는 운영자의 user scope 등록을 그대로 쓴다 — 이 저장소는 MCP 설정을 만들지도 좁히지도 않는다.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

START = os.path.join(ROOT, "bin", "start.sh")
STUB = """#!/usr/bin/env bash
python3 - "$@" <<'PY'
import json, os, sys
json.dump({"argv": sys.argv[1:], "env": {k: os.environ[k] for k in ("RS_ROLE", "RS_RUNTIME", "TELEGRAM_BOT_TOKEN") if k in os.environ}}, open(os.environ["RS_TEST_LAUNCH_LOG"], "w"), ensure_ascii=False)
PY
"""


class StartRuntimesTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.stubs = os.path.join(self.d, "stubs")
        os.makedirs(self.stubs)
        for name in ("claude", "agy"):
            p = os.path.join(self.stubs, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(STUB)
            os.chmod(p, 0o755)
        with open(os.path.join(ROOT, "config", "config.example.json"), encoding="utf-8") as f:
            example = json.load(f)
        self.cfgp = os.path.join(self.d, "config.json")
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump({"mode": "dry_run", "account_id": "acct", "operator_channel": "console", "gw": {"base_url": "http://127.0.0.1:1", "api_key": "test-key"},
                       "inbound": {"source": "rrr_stream"}}, f)
        self.log = os.path.join(self.d, "launch.json")
        # lead 기동은 기억 원본을 머지해 시스템 프롬프트로 넣는다 — 최소 골격을 깔아 둔다.
        self.local = os.path.join(self.d, "local")
        for layer in ("memory/playbook", "memory/state", "memory/journal", "system-prompts"):
            os.makedirs(os.path.join(self.local, layer), exist_ok=True)
        with open(os.path.join(self.local, "memory", "state", "2026-09-15.md"), "w", encoding="utf-8") as f:
            f.write("# 상태\n\n## 운영자 지시\n지시 없음\n\n## 태도\n관망\n")
        with open(os.path.join(self.local, "memory", "playbook", "2026-09-15.md"), "w", encoding="utf-8") as f:
            f.write("# 규칙\n## 한도\n\n")

    def tearDown(self):
        pid_file = os.path.join(self.d, "local", "inbound.pid")
        if os.path.exists(pid_file):
            try:
                pid = int(open(pid_file).read().strip())
                os.kill(pid, 9)
            except Exception:
                pass

    def run_start(self, *args, env_extra=None):
        # 이 파일은 런타임 기동 인자를 본다. herdr pane 게이트는 test_start_herdr.py 가 검증하므로
        # 여기서는 명시적으로 우회한다 — 그러지 않으면 실제 herdr 상태에 결과가 좌우된다.
        env = dict(os.environ, FORCE_OUTSIDE_HERDR="1", RS_CONFIG=self.cfgp, RS_LOCAL=self.local,
                   RS_GW_BASE_URL="http://127.0.0.1:15410",
                   PATH=self.stubs + os.pathsep + os.environ.get("PATH", ""), RS_TEST_LAUNCH_LOG=self.log)
        env.update(env_extra or {})
        return subprocess.run(["bash", START, *args], capture_output=True, text=True, env=env, cwd=ROOT)

    def launched(self):
        with open(self.log, encoding="utf-8") as f:
            return json.load(f)

    def test_lead_dry_launch_injects_env_and_boot(self):
        for args in (["--role", "lead", "--dry-launch"], ["--dry-launch"]):
            r = self.run_start(*args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            j = self.launched()
            self.assertEqual(j["env"], {"RS_ROLE": "lead", "RS_RUNTIME": "claude"})
            argv = j["argv"]
            boot = argv[-1]
            self.assertIn("세션 기동", boot)
            self.assertIn("role=lead", boot)
            self.assertIn("kiwoom_list_tools", boot)
            self.assertIn("브로커 도구 확인", boot)
            self.assertNotIn("사용자 확인 뒤", boot)
            self.assertNotIn("도구가 없음을 확인", boot)
            self.assertNotIn("주문 경로는 bin/order.py 뿐", boot)
            self.assertIn("집행 결과 기록·보고", boot)
            for tool in ("bin/order.py", "bin/consult.py", "scripts/ledger.py", "scripts/rt_calc.py", "bin/decision_packet.py", "bin/subscribe.py", "bin/report.py"):
                self.assertIn(tool, boot, tool)
            self.assertNotIn("eod_cancel", boot)
            self.assertIn("SOR 고정", boot)
            # 따옴표까지 본다. bash 이중따옴표 문자열 안에서는 이스케이프하지 않으면 조용히
            # 벗겨져, 소스엔 dmst_stex_tp="SOR" 로 적혀 있는데 프롬프트엔 SOR 로 간다(실제로 그랬다).
            self.assertIn('dmst_stex_tp="SOR"', boot)
            self.assertIn("5요소 중 거래소를 매번 고르지 않는다", boot)
            self.assertIn("마감 전 취소 작업을 두지 않는다", boot)
            self.assertIn("[market-check ts=HH:MM]", boot)
            self.assertIn("AGENTS.md §10", boot)
            self.assertNotIn("체크리스트", boot)
            # 기억은 부트 프롬프트가 아니라 시스템 프롬프트로 간다(docs/05-context.md).
            for gone in ("## 규칙", "## 상태", "## 최근 일지"):
                self.assertNotIn(gone, boot, f"부트 프롬프트에 기억이 실렸다: {gone}")
            self.assertIn("고치려는 날짜 파일이 없으면", boot)
            self.assertIn("'## 운영자 지시' 절은 운영자·에이전트의 것이다", boot)

    def test_exec_dry_launch_injects_env_and_boot(self):
        r = self.run_start("--role", "exec", "--dry-launch")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        j = self.launched()
        self.assertEqual(j["env"], {"RS_ROLE": "exec", "RS_RUNTIME": "claude"})
        argv = j["argv"]
        boot = argv[-1]
        self.assertIn("role=exec", boot)
        self.assertIn("기술적 대응", boot)
        self.assertIn("하위 분석", boot)
        self.assertIn("주문 집행", boot)
        self.assertIn("rs-lead 의 집행 지시", boot)
        self.assertNotIn("사용자 확인 뒤", boot)
        self.assertIn("사람 채널을 갖지 않으며", boot)
        self.assertIn("되묻는다", boot)
        self.assertIn("3소스 대사", boot)
        self.assertIn("bin/order.py", boot)
        self.assertNotIn("eod_cancel", boot)
        self.assertIn("SOR 고정", boot)
        self.assertIn('dmst_stex_tp="SOR"', boot)
        self.assertIn("5요소 중 거래소를 매번 고르지 않는다", boot)
        self.assertIn("마감 전 취소 작업을 두지 않는다", boot)
        self.assertNotIn("market-check", boot)
        self.assertNotIn("AGENTS.md §10", boot)
        self.assertNotIn("## STATE", boot)

    def test_lead_injects_memory_as_system_prompt(self):
        """기억은 시스템 프롬프트로 간다 — 사용자 메시지는 대화가 길어지면 밀려난다.

        이 주입이 이번 구조의 핵심이라 인자·파일·내용을 함께 못박는다.
        """
        r = self.run_start("--role", "lead", "--dry-launch", env_extra={"RS_TODAY": "2026-09-15"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        argv = self.launched()["argv"]
        self.assertIn("--append-system-prompt-file", argv, "기억이 시스템 프롬프트로 들어가지 않는다")
        path = argv[argv.index("--append-system-prompt-file") + 1]
        self.assertEqual(path, os.path.join(self.local, "system-prompts", "2026-09-15.md"))
        self.assertTrue(os.path.exists(path), "산출물이 남지 않았다")
        with open(path, encoding="utf-8") as f:
            art = f.read()
        for head in ("## 운영자 지시", "## 규칙", "## 상태", "## 최근 일지"):
            self.assertIn(head, art, head)
        self.assertLess(art.index("## 운영자 지시"), art.index("## 규칙"))

    def test_exec_gets_no_system_prompt(self):
        """exec 는 rs-lead 의 지시로 움직인다 — 기억을 받지 않는다."""
        r = self.run_start("--role", "exec", "--dry-launch")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("--append-system-prompt-file", self.launched()["argv"])

    def test_lead_refuses_when_no_memory_source_exists(self):
        """원본이 하나도 없으면 최초 설치 상태다 — 빈 기억으로 띄우지 않는다."""
        import shutil
        shutil.rmtree(os.path.join(self.local, "memory"))
        r = self.run_start("--role", "lead", "--dry-launch")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("init_local", r.stdout + r.stderr)

    def test_boot_prompt_is_separated_from_variadic_options(self):
        """부트 프롬프트 앞에 `--` 가 있어야 한다(claude 한정).

        agy 는 프롬프트를 `-i` 의 값으로 받으므로 이 구분자가 없다 — 그쪽은
        test_exec_runs_on_agy_runtime 이 따로 못박는다.

        `claude --mcp-config <configs...>` 는 공백으로 이어지는 가변 인자다. `--` 없이
        프롬프트를 뒤에 붙이면 claude 가 그것까지 MCP 파일 경로로 읽고 기동 전에 죽는다:
            Error: Invalid MCP configuration: MCP config file not found: <cwd>/세션 기동(...
        `--` 가 옵션 파싱을 끝내 프롬프트를 위치 인자로 고정한다. argv[:3]/argv[-1] 만 보는
        검사는 이 결함을 통과시킨다 — 구분자의 존재와 위치를 직접 고정한다.
        """
        for role in ("lead", "exec"):
            with self.subTest(role=role):
                r = self.run_start("--role", role, "--dry-launch")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                argv = self.launched()["argv"]
                self.assertIn("--", argv, f"{role}: 부트 프롬프트 앞 `--` 구분자 없음")
                self.assertEqual(argv[-2], "--", f"{role}: `--` 가 프롬프트 바로 앞이 아님: {argv[-3:]}")
                # 구분자 앞쪽에는 프롬프트가 섞이지 않는다 — 가변 인자가 삼킬 수 있는 자리.
                self.assertNotIn("세션 기동", " ".join(argv[:-1]))

    def test_exec_role_does_not_start_adapter_and_lead_starts_adapter(self):
        pid_file = os.path.join(self.d, "local", "inbound.pid")
        if os.path.exists(pid_file):
            os.remove(pid_file)

        # 1. exec 역할: --dry-launch 없이 기동해도 어댑터를 띄우지 않고 pid 파일을 생성하지 않는다
        r_exec = self.run_start("--role", "exec")
        self.assertEqual(r_exec.returncode, 0, r_exec.stdout + r_exec.stderr)
        self.assertFalse(os.path.exists(pid_file), "exec must not create inbound.pid")
        self.assertNotIn("adapter=bin/inbound_rrr.py", r_exec.stdout)

        # 기존 pid 파일이 있어도 exec 는 건드리지 않는다
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)
        with open(pid_file, "w") as f:
            f.write("999999")
        r_exec2 = self.run_start("--role", "exec")
        self.assertEqual(r_exec2.returncode, 0, r_exec2.stdout + r_exec2.stderr)
        with open(pid_file) as f:
            self.assertEqual(f.read().strip(), "999999")
        os.remove(pid_file)

        # 2. lead 역할 대조: --dry-launch 없이 기동 시 어댑터를 띄우고 inbound.pid 를 생성한다
        r_lead = self.run_start("--role", "lead")
        self.assertEqual(r_lead.returncode, 0, r_lead.stdout + r_lead.stderr)
        self.assertTrue(os.path.exists(pid_file), "lead must create inbound.pid")
        self.assertIn("adapter=bin/inbound_rrr.py", r_lead.stdout)
        try:
            with open(pid_file) as f:
                lead_pid = int(f.read().strip())
            os.kill(lead_pid, 9)
        except Exception:
            pass

    def test_confirm_mode_boot_prompt_describes_execution_not_restriction(self):
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump({"mode": "confirm", "account_id": "acct", "operator_channel": "console", "gw": {"base_url": "http://127.0.0.1:1", "api_key": "test-key"},
                       "inbound": {"source": "rrr_stream"}}, f)
        r = self.run_start("--dry-launch")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        j = self.launched()
        boot = j["argv"][-1]
        self.assertIn("mode=confirm", boot)
        self.assertIn("gw MCP 주문 도구로 집행", boot)
        for gone in ("사용자 확인 뒤", "확인 중계", "응답이 없으면 중단"):
            self.assertNotIn(gone, boot, "부트 프롬프트에 주문 제약 서술: " + gone)

    def test_check_only_reports_role_plan(self):
        r_lead = self.run_start("--role", "lead", "--check-only")
        self.assertEqual(r_lead.returncode, 0, r_lead.stdout + r_lead.stderr)
        self.assertIn("role=lead", r_lead.stdout)

        r_exec = self.run_start("--role", "exec", "--check-only")
        self.assertEqual(r_exec.returncode, 0, r_exec.stdout + r_exec.stderr)
        self.assertIn("role=exec", r_exec.stdout)

    def test_unknown_role_rejected(self):
        r = self.run_start("--role", "gemini", "--dry-launch")
        self.assertEqual(r.returncode, 2)
        self.assertIn("lead|exec", r.stdout + r.stderr)

    def test_runtime_claude_is_accepted_explicitly(self):
        """`--runtime claude` 는 기본값을 적어 둔 것이며 기동 명령이 달라지지 않는다."""
        r = self.run_start("--role", "exec", "--runtime", "claude", "--dry-launch")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        j = self.launched()
        self.assertEqual(j["env"], {"RS_ROLE": "exec", "RS_RUNTIME": "claude"})
        self.assertEqual(j["argv"][-2], "--", "claude 는 `--` 뒤 위치 인자로 프롬프트를 받는다")

    def test_exec_runs_on_agy_runtime(self):
        """rs-exec 은 agy 로도 뜬다 — 부트 프롬프트는 `-i` 의 **값**으로 간다.

        agy 의 `-i`(--prompt-interactive) 가 claude 의 위치 인자와 동치다: 초기 프롬프트를
        실행한 뒤 세션을 유지한다. 프롬프트가 플래그 값이므로 claude 에서 한 번 물렸던
        `--` 구분자 문제(가변 인자가 프롬프트를 삼킨다)가 agy 에는 없다 — 대신 프롬프트가
        정말 `-i` 에 붙어 있는지를 못박는다. 빠지면 agy 는 빈 대화로 떠서 지시를 기다린다.
        """
        r = self.run_start("--role", "exec", "--runtime", "agy", "--dry-launch")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        j = self.launched()
        self.assertEqual(j["env"], {"RS_ROLE": "exec", "RS_RUNTIME": "agy"})
        argv = j["argv"]
        self.assertIn("-i", argv, "agy 는 -i 로 부트 프롬프트를 받는다")
        self.assertIn("세션 기동", argv[argv.index("-i") + 1], "부트 프롬프트가 -i 의 값이 아니다")
        self.assertNotIn("--", argv, "agy 는 `--` 구분자를 쓰지 않는다")

    def test_agy_runtime_skips_permission_prompts(self):
        """rs-exec 은 사람이 없는 세션이다 — 승인 프롬프트가 뜨면 조용히 멈춘다.

        agy 의 settings.json 이 이미 mcp(*)·command(*) 와일드카드 allow 라 이 플래그가
        넓히는 권한은 없다. 막는 것은 정지다.
        """
        r = self.run_start("--role", "exec", "--runtime", "agy", "--dry-launch")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--dangerously-skip-permissions", self.launched()["argv"])

    def test_agy_boot_prompt_names_its_runtime(self):
        """부트 프롬프트의 runtime 표기는 사실 기록이다 — 틀리면 세션이 자기를 오인한다."""
        r = self.run_start("--role", "exec", "--runtime", "agy", "--dry-launch")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        boot = self.launched()["argv"][-1]
        self.assertIn("runtime=agy", boot)
        self.assertNotIn("runtime=claude", boot)

    def test_agy_is_refused_for_lead_role(self):
        """lead 는 claude 고정이다.

        lead 의 기억은 `--append-system-prompt-file` 로 들어가는데(docs/05-context.md)
        agy 에 대응 플래그가 없다. 막지 않으면 기억 없는 lead 가 **정상 기동한 얼굴로**
        뜬다 — 어댑터가 죽었을 때 런타임을 띄우지 않는 것과 같은 부류라 닫아 둔다.
        """
        r = self.run_start("--role", "lead", "--runtime", "agy", "--dry-launch")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("agy", out)
        self.assertIn("lead", out)
        self.assertFalse(os.path.exists(self.log), "거부했는데 런타임이 떴다")

        # 게이트는 인자 파싱 자리다 — 점검 경로로 들어와도 같은 곳에서 막힌다.
        r2 = self.run_start("--role", "lead", "--runtime", "agy", "--check-only")
        self.assertEqual(r2.returncode, 2, r2.stdout + r2.stderr)

    def test_unsupported_runtime_rejected(self):
        """지원 목록은 claude|agy 뿐이다.

        codex 는 넣지 않는다 — 이 머신의 codex 에는 kiwoom-gw 가 등록돼 있지 않고,
        동명 `kiwoom_order_*` 가 kiwoom-sdk-mcp 로 해석되어 단일 주문 경로(D1)와
        gw 키 비활성화 급정지(D18)를 함께 우회한다.
        """
        for rt in ("codex", "gemini"):
            with self.subTest(runtime=rt):
                r = self.run_start("--role", "exec", "--runtime", rt, "--dry-launch")
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("claude|agy", r.stdout + r.stderr)

    def test_check_only_reports_runtime(self):
        r = self.run_start("--role", "exec", "--runtime", "agy", "--check-only")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("runtime=agy", r.stdout)

    def test_telegram_channel_passes_token_to_session_env_for_lead(self):
        dummy_token = "dummy_telegram_token_xyz"
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump({
                "mode": "dry_run",
                "account_id": "acct",
                "operator_channel": "telegram",
                "operator_chat_id": "99999",
                "telegram_bot_token": dummy_token,
                "gw": {"base_url": "http://127.0.0.1:1", "api_key": "test-key"},
                "inbound": {"source": "rrr_stream"},
            }, f)

        fake_home = os.path.join(self.d, "fake_home")
        os.makedirs(fake_home, exist_ok=True)
        r = self.run_start("--role", "lead", "--dry-launch", env_extra={"HOME": fake_home})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        self.assertTrue(os.path.exists(self.log))
        with open(self.log, encoding="utf-8") as f:
            j = json.load(f)

        # 토큰이 세션 env 로 전달됨 (값 단언 0, 존재 여부만)
        self.assertIn("TELEGRAM_BOT_TOKEN", j["env"])
        self.assertTrue(bool(j["env"].get("TELEGRAM_BOT_TOKEN")))

        # --channels 인자가 claude 실행에 전달됨
        argv = j["argv"]
        self.assertIn("--channels", argv)
        self.assertIn("plugin:telegram@claude-plugins-official", argv)

        # 공유 ~/.claude/channels/telegram/.env 에 쓰지 않음
        shared_env = os.path.join(fake_home, ".claude", "channels", "telegram", ".env")
        self.assertFalse(os.path.exists(shared_env), f"Must not write to shared {shared_env}")

    def test_check_only_does_not_leak_telegram_token(self):
        secret_token = "super_secret_tg_bot_token_abc123"
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump({
                "mode": "dry_run",
                "account_id": "acct",
                "operator_channel": "telegram",
                "operator_chat_id": "99999",
                "telegram_bot_token": secret_token,
                "gw": {"base_url": "http://127.0.0.1:1", "api_key": "test-key"},
                "inbound": {"source": "rrr_stream"},
            }, f)

        r = self.run_start("--role", "lead", "--check-only")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("check-only ok", r.stdout)
        self.assertNotIn(secret_token, r.stdout)
        self.assertNotIn(secret_token, r.stderr)

    def test_console_channel_does_not_inject_telegram_channel_or_token(self):
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump({
                "mode": "dry_run",
                "account_id": "acct",
                "operator_channel": "console",
                "gw": {"base_url": "http://127.0.0.1:1", "api_key": "test-key"},
                "inbound": {"source": "rrr_stream"},
            }, f)

        r = self.run_start("--role", "lead", "--dry-launch")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(self.log, encoding="utf-8") as f:
            j = json.load(f)

        self.assertFalse(bool(j["env"].get("TELEGRAM_BOT_TOKEN")))
        self.assertNotIn("--channels", j["argv"])

    def test_exec_role_does_not_get_telegram_channel_even_if_configured(self):
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump({
                "mode": "dry_run",
                "account_id": "acct",
                "operator_channel": "telegram",
                "operator_chat_id": "99999",
                "telegram_bot_token": "dummy_tok",
                "gw": {"base_url": "http://127.0.0.1:1", "api_key": "test-key"},
                "inbound": {"source": "rrr_stream"},
            }, f)

        r = self.run_start("--role", "exec", "--dry-launch")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(self.log, encoding="utf-8") as f:
            j = json.load(f)

        self.assertNotIn("--channels", j["argv"])

if __name__ == "__main__":
    unittest.main()
