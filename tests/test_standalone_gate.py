"""단독 실행 게이트 — 배포된 clone 은 형제 저장소 없이 선다

1. 저장소 전체에 형제 저장소 경로(trading-sys/<다른repo>, ../rrr, rrr/docs/)가 없어야 한다.
2. kiwoom_sdk 임포트가 없어야 한다 — 단, bin/broker_kiwoom.py 만 W2 이전 한시적 allowlist 예외.
3. 소스·MCP 등록에 호스트 문자열이 박혀 있지 않아야 한다(주소는 설정/환경변수에서만).
4. 테스트 실행 방법이 README 및 docs/02-runbook.md 에 명시되어 있어야 한다.
"""
from __future__ import annotations

import json
import os
import sys
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# W2: broker_kiwoom.py 삭제 완료로 예외 목록은 완전히 비어 있다
KIWOOM_SDK_ALLOWLIST = set()

FORBIDDEN_PATH_PATTERNS = [
    re.compile(r"trading-sys/(?!rrr-swing\b)[A-Za-z0-9_\-\.]+"),
    re.compile(r"\.\./rrr\b"),
    re.compile(r"rrr/docs/"),
]

SCANNED_EXTENSIONS = {
    ".py", ".json", ".md", ".sh", ".toml", ".yaml", ".yml", ".txt",
}

EXCLUDED_DIRS = {
    ".git", "local", "tests", "__pycache__", ".pytest_cache", ".venv",
}


import subprocess


def _iter_repo_files(root: str):
    """저장소 파일 순회 (tests 및 gitignore 대상 제외)."""
    res = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        cwd=root,
    )
    if res.returncode == 0:
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("tests/"):
                continue
            path = os.path.join(root, line)
            if os.path.isfile(path):
                yield path
    else:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in {"local", "tests"}]
            for f in filenames:
                ext = os.path.splitext(f)[1]
                if ext in SCANNED_EXTENSIONS or f in {"CLAUDE.md", "AGENTS.md", "README.md"}:
                    yield os.path.join(dirpath, f)


def _repo_text_files(dirs, exts):
    out = []
    for d in dirs:
        dirpath = os.path.join(ROOT, d)
        if not os.path.isdir(dirpath):
            continue
        for fname in sorted(os.listdir(dirpath)):
            if fname.endswith(exts):
                out.append(os.path.join(dirpath, fname))
    return out

class StandaloneGateTests(unittest.TestCase):
    def test_allowlist_is_strictly_empty(self):
        """W2: broker_kiwoom.py 삭제 후 KIWOOM_SDK_ALLOWLIST 는 완전히 비어 있어야 한다 (메타 테스트: 임의 확장 금지)."""
        self.assertEqual(
            KIWOOM_SDK_ALLOWLIST,
            set(),
            "KIWOOM_SDK_ALLOWLIST must be strictly empty in W2",
        )

    def test_no_sibling_repo_paths_in_repo(self):
        """저장소 파일에 형제 저장소 절대/상대 경로가 없어야 한다."""
        violations = []
        for path in _iter_repo_files(ROOT):
            rel = os.path.relpath(path, ROOT)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line_no, line in enumerate(f, 1):
                        for pat in FORBIDDEN_PATH_PATTERNS:
                            m = pat.search(line)
                            if m:
                                violations.append(f"{rel}:{line_no}: {m.group(0)} (line: {line.strip()})")
            except OSError:
                continue

        self.assertEqual(violations, [], f"Sibling repo paths found:\n" + "\n".join(violations))

    def test_no_design_decision_numbers(self):
        """`D18` 같은 결정 번호는 이 저장소에서 풀 수 없다 — 쓰지 않는다.

        번호의 정의는 형제 저장소의 설계 문서에 있고, 그 경로는 이 저장소가 적을 수도 없다
        (위 테스트가 금지한다). 배포된 clone 에는 그 문서 자체가 없으므로, 읽는 사람은
        `D18` 을 보고도 어디로 가야 할지 알 수 없다 — 있는데 도달할 수 없는 참조다.

        뜻을 문장으로 적는다. 번호는 설계 문서를 읽는 사람에게만 값이 있고, 런북을 읽는
        운영자에게는 풀어 쓴 문장이 바로 쓸모 있다.
        """
        pat = re.compile(r"\bD\d{1,2}\b")
        violations = []
        for path in _iter_repo_files(ROOT):
            if not path.endswith((".md", ".py", ".sh")):
                continue
            rel = os.path.relpath(path, ROOT)
            if os.path.samefile(path, __file__):
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line_no, line in enumerate(f, 1):
                        m = pat.search(line)
                        if m:
                            violations.append(f"{rel}:{line_no}: {m.group(0)} — {line.strip()[:80]}")
            except OSError:
                continue
        self.assertEqual(violations, [], "풀 수 없는 결정 번호:\n" + "\n".join(violations))

    def test_no_kiwoom_sdk_imports_except_allowlist(self):
        """kiwoom_sdk 임포트는 allowlist 에 명시된 파일(bin/broker_kiwoom.py)에만 존재해야 한다."""
        import_pattern = re.compile(r"^\s*(?:import\s+kiwoom_sdk|from\s+kiwoom_sdk\s+import)")
        files_with_import = set()

        for path in _iter_repo_files(ROOT):
            if not path.endswith(".py"):
                continue
            rel = os.path.relpath(path, ROOT)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if import_pattern.search(line):
                            files_with_import.add(rel)
                            break
            except OSError:
                continue

        # 1. allowlist 에 없는 파일에서 임포트 발견 시 거부
        unauthorized = files_with_import - KIWOOM_SDK_ALLOWLIST
        self.assertEqual(unauthorized, set(), f"Unauthorized kiwoom_sdk imports: {unauthorized}")

        # 2. allowlist 엔트리가 실제 존재하고 임포트하고 있는지 확인 (켜진 척 방지)
        for allowed in KIWOOM_SDK_ALLOWLIST:
            self.assertIn(
                allowed,
                files_with_import,
                f"Allowlisted file {allowed} does not import kiwoom_sdk or does not exist",
            )

    def test_no_hardcoded_hosts_in_config(self):
        """example config 에 하드코딩된 호스트 주소가 없어야 한다."""
        example_path = os.path.join(ROOT, "config", "config.example.json")
        self.assertTrue(os.path.exists(example_path))
        with open(example_path, encoding="utf-8") as f:
            ex_cfg = json.load(f)

        gw_base = ex_cfg.get("gw", {}).get("base_url", "")
        self.assertIn("<GW_HOST>", gw_base, f"config.example.json gw.base_url must use <GW_HOST>, got: {gw_base}")

    def test_test_command_documented(self):
        """README.md 와 docs/02-runbook.md 에 pytest 실행 명령이 명시되어 있어야 한다."""
        cmd = "uv run --no-project --with pytest pytest -q"
        readme_path = os.path.join(ROOT, "README.md")
        runbook_path = os.path.join(ROOT, "docs", "02-runbook.md")

        self.assertTrue(os.path.exists(readme_path))
        self.assertTrue(os.path.exists(runbook_path))

        with open(readme_path, encoding="utf-8") as f:
            self.assertIn(cmd, f.read(), f"README.md does not document test command: {cmd}")

        with open(runbook_path, encoding="utf-8") as f:
            self.assertIn(cmd, f.read(), f"docs/02-runbook.md does not document test command: {cmd}")

    def test_eod_cancel_removed_and_no_gateway_state_in_code(self):
        """주문은 SOR 고정이며 장 종료 시 자동 소멸하므로 eod_cancel.py 및 gateway-state.json 잔재가 없어야 한다."""
        self.assertFalse(os.path.exists(os.path.join(ROOT, "bin", "eod_cancel.py")), "bin/eod_cancel.py must be removed")
        self.assertFalse(os.path.exists(os.path.join(ROOT, "tests", "test_eod_cancel.py")), "tests/test_eod_cancel.py must be removed")

        for d in ("bin", "scripts"):
            dirpath = os.path.join(ROOT, d)
            for fname in os.listdir(dirpath):
                if fname.endswith(".py"):
                    fpath = os.path.join(dirpath, fname)
                    with open(fpath, encoding="utf-8") as f:
                        content = f.read()
                    self.assertNotIn("gateway-state.json", content, f"{fpath} still references gateway-state.json")

    def test_inbound_telegram_removed_and_no_shared_channel_env(self):
        """PR-2: inbound_telegram.py 는 폐기되며, 공유 .env 경로에 쓰지 않는다."""
        self.assertFalse(os.path.exists(os.path.join(ROOT, "bin", "inbound_telegram.py")), "bin/inbound_telegram.py must be removed")
        self.assertFalse(os.path.exists(os.path.join(ROOT, "tests", "test_inbound_parity.py")), "tests/test_inbound_parity.py must be removed")

        # 코드뿐 아니라 문서까지 본다. 이 코호트의 드리프트는 전부 문서·문자열에서 났다 —
        # 코드는 따라오는데 그 코드를 설명하는 문장이 남는다.
        for fpath in _repo_text_files(("bin", "scripts", "docs", "tests/fixtures"), (".py", ".sh", ".md", ".json", ".jsonl")) + [
            os.path.join(ROOT, n) for n in ("README.md", "AGENTS.md", "CLAUDE.md")
        ]:
            if not os.path.exists(fpath) or os.path.samefile(fpath, __file__):
                continue
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("inbound_telegram", content, f"{fpath} still references inbound_telegram")
            self.assertNotIn("telegram_updates.jsonl", content, f"{fpath} still references the removed fixture")

    def test_repo_does_not_manage_mcp_configuration(self):
        """이 저장소는 MCP 설정을 만들지도 좁히지도 않는다(운영자 결정).

        MCP 는 운영자의 user scope 등록(`claude mcp add`)을 그대로 쓴다. 주문 도구 차단도
        하지 않는다 — 사용자 확인 뒤에만 주문한다는 계약은 AGENTS.md·부트 프롬프트가 지키며
        코드는 관여하지 않는다. 되살리면 텔레그램 채널이 다시 조용히 죽는다(채널은 MCP
        서버라 --strict-mcp-config 가 배제한다. 라이브에서 그렇게 막혔다).
        """
        for gone in (".mcp.json", os.path.join("bin", "mcp_guard.py")):
            self.assertFalse(os.path.exists(os.path.join(ROOT, gone)), f"{gone} 는 삭제됐다")
        # .claude/settings.json 은 훅만 담는다 — 있어도 되지만 MCP 설정이 들어가면 안 된다.
        settings = os.path.join(ROOT, ".claude", "settings.json")
        if os.path.exists(settings):
            with open(settings, encoding="utf-8") as f:
                cfg = json.load(f)
            for key in ("permissions", "enabledMcpjsonServers", "mcpServers"):
                self.assertNotIn(key, cfg, f".claude/settings.json 에 MCP 설정 '{key}' 가 들어갔다")
            self.assertEqual(set(cfg) - {"_note"}, {"hooks"}, f"훅 외의 키: {sorted(set(cfg) - {'_note', 'hooks'})}")
        with open(os.path.join(ROOT, "bin", "start.sh"), encoding="utf-8") as f:
            content = f.read()
        for token in ("--strict-mcp-config", "--mcp-config", "mcp_guard", "KIWOOM_TRADING_ENABLED"):
            self.assertNotIn(token, content, f"bin/start.sh: MCP 기계장치가 되살아났다 — '{token}'")

    def test_order_exchange_is_sor_everywhere(self):
        """주문 거래소는 SOR 고정이다 — 다른 거래소를 지시하는 문장이 남으면 안 된다.

        docs/04-price_source.md 가 SOR 고정 이전 서술(`dmst_stex_tp=NXT`)을 들고 있었다.
        세션이 그 문서를 읽으면 잘못된 거래소로 주문을 낸다. `_AL` 이 조회 전용이라는 규칙은
        호가 선택의 문제이고 주문 거래소와 무관하다 — 둘을 섞지 않는다.
        """
        import re as _re
        files = (_repo_text_files(("docs", "bin", "scripts"), (".md", ".sh", ".py")) +
                 [os.path.join(ROOT, n) for n in ("README.md", "AGENTS.md", "CLAUDE.md")])
        pat = _re.compile(r'dmst_stex_tp\s*=\s*"?([A-Za-z]+)"?')
        for fpath in files:
            if not os.path.exists(fpath) or os.path.samefile(fpath, __file__):
                continue
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            for value in set(pat.findall(content)):
                self.assertEqual(value, "SOR",
                                 f"{os.path.relpath(fpath, ROOT)}: 주문 거래소가 SOR 이 아니다 — {value}")

    def test_docs_do_not_describe_order_restrictions(self):
        """문서는 주문 제약을 설명하지 않는다(운영자 결정).

        도구가 지원하는 범위로 쓴다 — 확인 절차나 차단 여부를 문서가 규정하지 않는다.
        없는 기계장치를 설명하면 읽는 쪽이 그것이 있다고 믿는다.
        """
        dead = ("사용자 확인 뒤", "확인 중계", "코드가 막지 않는다", "주문 도구를 차단",
                "permissions.deny", "확인 응답이 없어도")
        files = (_repo_text_files(("docs", "bin"), (".md", ".sh")) +
                 [os.path.join(ROOT, n) for n in ("README.md", "AGENTS.md", "CLAUDE.md",
                                                  os.path.join("config", "config.example.json"))])
        for fpath in files:
            if not os.path.exists(fpath):
                continue
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            for token in dead:
                self.assertNotIn(token, content, f"{os.path.relpath(fpath, ROOT)}: 주문 제약 서술 {token}")

    def test_agents_md_keeps_the_execution_direction(self):
        """제약은 아니지만 역할 방향은 남는다 — 집행 지시는 rs-lead → rs-exec 한 방향이다."""
        with open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("rs-lead → rs-exec", content)
        self.assertIn("사람 채널이 없다", content)


    def test_watchlist_vocabulary_has_one_source(self):
        """감시 목록 판정의 정본은 inbound_core 하나여야 한다.

        같은 규칙을 두 곳에 적으면 갈리고, 갈려도 로그에는 unwatched 만 남아 정상 필터링과
        구분되지 않는다. 옛 구독에서 실제로 그랬다 — subscribe.py 에 폐기된 tick 이 남고
        heartbeat 가 빠져 `--events heartbeat` 선언이 거부됐다.

        CLI(watchlist.py)는 파일을 쓰기만 하고, 무엇을 배달할지는 코어만 정한다.
        """
        sys.path.insert(0, os.path.join(ROOT, "bin"))
        import inbound_core as core
        import watchlist

        self.assertTrue(hasattr(core, "watchlist_matches"), "판정은 코어에 있다")
        self.assertFalse(hasattr(watchlist, "watchlist_matches"), "CLI 가 판정을 따로 들고 있다")
        for gone in ("SESSION_TOKENS", "SUBSCRIBABLE_EVENT_TYPES"):
            self.assertFalse(hasattr(core, gone), f"폐기된 어휘 {gone} 가 남았다")

    def test_no_module_redeclares_the_zone_event_list(self):
        """존 이벤트 목록을 다시 적는 모듈이 있으면 안 된다 — 정본은 inbound_core 뿐이다.

        subscribe.py 만 정본에 묶고 끝냈더니 ledger.py·review.py 가 각자 사본을 들고 갈렸다.
        ledger 는 heartbeat 를 도메인 밖으로 판정해 구독 기록을 거부했고, review 는 heartbeat
        로 내린 decision 을 전환율 행렬에서 조용히 버렸다. 목록을 적는 자리 자체를 막는다.
        """
        canonical = os.path.join(ROOT, "bin", "inbound_core.py")
        offenders = []
        for sub in ("bin", "scripts"):
            d = os.path.join(ROOT, sub)
            for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                fpath = os.path.join(d, name)
                if not name.endswith(".py") or os.path.samefile(fpath, canonical):
                    continue
                with open(fpath, encoding="utf-8") as f:
                    content = f.read()
                # 두 개 이상 나란히 적혀 있으면 목록이다(단일 언급은 주석·예시일 수 있다).
                hits = sum(f'"{e}"' in content for e in
                           ("support_enter", "support_break", "resistance_enter", "resistance_break"))
                if hits >= 2:
                    offenders.append(os.path.relpath(fpath, ROOT))
        self.assertEqual(offenders, [], f"존 이벤트 목록 사본: {offenders} — inbound_core 에서 가져다 쓴다")

    def test_config_has_no_dead_keys(self):
        """코드가 읽지 않는 설정을 두지 않는다. 설정은 동작을 바꿀 때만 존재한다."""
        import json as _json
        with open(os.path.join(ROOT, "config", "config.example.json"), encoding="utf-8") as f:
            cfg = _json.load(f)
        for dead in ("subscription", "escalation_ack_by", "recheck_days_default", "model", "effort"):
            self.assertNotIn(dead, cfg, f"config 에 죽은 키: {dead}")
        self.assertNotIn("marks_time", cfg.get("accounting", {}))

    def test_no_links_to_missing_docs(self):
        """문서·코드가 가리키는 저장소 내 문서가 실제로 있어야 한다.

        문서를 지우면 그것을 가리키던 곳이 죽은 링크로 남는다. 읽는 사람은 없는 파일을 찾다가
        계약이 어디 있는지 잃는다.
        """
        import re as _re
        targets = _repo_text_files(("docs", "bin"), (".md", ".py", ".sh")) + [
            os.path.join(ROOT, n) for n in ("README.md", "AGENTS.md", "CLAUDE.md")
        ]
        pat = _re.compile(r"((?:docs/)[\w./-]+\.md)")
        dead = []
        for fpath in targets:
            if not os.path.exists(fpath) or os.path.samefile(fpath, __file__):
                continue
            with open(fpath, encoding="utf-8") as f:
                for ref in set(pat.findall(f.read())):
                    if not os.path.exists(os.path.join(ROOT, ref)):
                        dead.append(f"{os.path.relpath(fpath, ROOT)} → {ref}")
        self.assertEqual(sorted(dead), [], f"없는 문서를 가리킨다: {sorted(dead)}")

    def test_universe_vocabulary_is_not_forked(self):
        """종목 집합은 세 이름으로만 부른다 — rrr 유니버스 · 활성 유니버스 · 구독 선언.

        같은 것을 "보유 + 관심 종목" 처럼 풀어 쓰면 이름이 갈리고, 갈린 이름은 정의가 따로
        자란다. 활성 유니버스라는 이름이 생긴 뒤로는 그 이름을 쓴다.
        """
        forked = ("보유 + 관심 종목", "보유·관심 종목", "보유·관심 목록",
                  "api_key_env", "export RRR_GW_API_KEY")
        for fpath in _repo_text_files(("docs",), (".md",)) + [
            os.path.join(ROOT, n) for n in ("README.md", "AGENTS.md", "CLAUDE.md")
        ]:
            if not os.path.exists(fpath) or os.path.samefile(fpath, __file__):
                continue
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            for t in forked:
                self.assertNotIn(t, content, f"{fpath}: '{t}' 대신 '활성 유니버스' 를 쓴다")

    def test_unsupported_runtimes_stay_removed(self):
        """지원 런타임은 claude|agy 뿐이며, 나머지 잔해는 남으면 안 된다.

        한때 `--runtime codex` 와 Stage 7~9 의 mcp_guard(check_agy·CODEX_OVERRIDE …)가 있었다.
        쓰이지 않는 경로는 검증되지 않은 채 낡고, 남아 있다는 사실만으로 선택지가 있는 것처럼
        읽힌다 — 프로덕션이 부르지 않는 점검은 점검이 아니다. codex 는 주문 경로가 갈리므로
        (kiwoom-gw 미등록 + 동명 도구가 kiwoom-sdk-mcp 로 해석) 되살리지 않는다.

        GEMINI.md 도 만들지 않는다. agy 는 AGENTS.md 계층을 읽으므로 계약이 이미 닿아 있고,
        런타임마다 계약 사본을 두면 사본이 따로 자란다.
        """
        self.assertFalse(os.path.exists(os.path.join(ROOT, "GEMINI.md")),
                         "GEMINI.md 는 만들지 않는다 — agy 도 AGENTS.md 를 읽는다")
        self.assertFalse(os.path.isdir(os.path.join(ROOT, ".claude", "skills", "trade-pair")),
                         "trade-pair 스킬은 삭제됐다 — tmux 는 쓰지 않는다")
        dead = ("--runtime codex", "check_agy", "codex_override_args",
                "CODEX_OVERRIDE", "--agy-config", "GEMINI.md")
        files = (_repo_text_files(("bin", "scripts", "docs"), (".py", ".sh", ".md"))
                 + [os.path.join(ROOT, n) for n in ("README.md", "AGENTS.md", "CLAUDE.md")])
        for fpath in files:
            if not os.path.exists(fpath):
                continue
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            for token in dead:
                self.assertNotIn(token, content, f"{os.path.relpath(fpath, ROOT)}: 폐기된 런타임 잔해 '{token}'")

    def test_agy_runtime_preconditions_are_documented(self):
        """agy 로 rs-exec 을 띄우기 전에 agy 쪽에 있어야 하는 두 가지를 런북이 적는다.

        ① kiwoom-gw MCP 등록 — 없으면 주문 도구가 없다.
        ② kiwoom-gw 스킬 — exec 부트 프롬프트가 "kiwoom-gw skill 절차 준수" 를 지시하는데,
           없으면 3소스 대사 절차 없이 주문한다.

        둘 다 빠져도 세션은 **정상 기동한 얼굴로** 뜬다. 에러가 나지 않는 침묵 실패라
        운영자가 알아차릴 신호가 런북의 전제조건 문장뿐이고, 기동 명령만 적고 전제조건을
        빠뜨리면 그 문장이 사라진 것을 아무도 못 본다.
        """
        with open(os.path.join(ROOT, "docs", "02-runbook.md"), encoding="utf-8") as f:
            runbook = f.read()
        self.assertIn("--runtime agy", runbook, "런북에 agy 기동 명령이 없다")
        self.assertIn("mcp_config.json", runbook, "런북에 agy 의 MCP 등록 위치가 없다")
        self.assertIn("skills/kiwoom-gw", runbook, "런북에 agy 의 kiwoom-gw 스킬 전제조건이 없다")

    def test_dead_tokens_removed_from_documentation(self):
        """W6: 폐기된 레거시 토큰들이 문서에 재발하지 않아야 한다.

        - max_attempts_per_digest (config 키 삭제됨)
        - safety_check (order.py 주문 제출/대사 안전 외피 폐기)
        - kill 파일 / kill_file (외피 폐기, 급정지는 gw 키 비활성화)
        - ops_retry_limit (rules.py 삭제됨)
        - direct 모드 (폐기 — 운전 모드는 dry_run·confirm 둘뿐)
        - RS_ROLE=session (--role lead|exec 로 변경됨)
        """
        doc_files = _repo_text_files(("docs",), (".md",)) + [
            os.path.join(ROOT, n) for n in ("README.md", "AGENTS.md", "CLAUDE.md")
        ]
        forbidden_in_docs = [
            "max_attempts_per_digest",
            "safety_check",
            "kill 파일",
            "kill_file",
            "ops_retry_limit",
            "RS_ROLE=session",
            "dry_run·direct",
            "dry_run · direct",
            "`direct`",
        ]

        for fpath in doc_files:
            if not os.path.exists(fpath) or os.path.samefile(fpath, __file__):
                continue
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            for token in forbidden_in_docs:
                self.assertNotIn(token, content, f"{fpath} still references legacy token '{token}'")

    def _local_doc(self):
        path = os.path.join(ROOT, "docs", "06-local.md")
        self.assertTrue(os.path.exists(path), "docs/06-local.md 가 없다")
        with open(path, encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _section(doc: str, heading: str) -> str:
        """`## 제목` 절의 본문만. 절 밖의 언급이 게이트를 통과시키지 못하게 한다."""
        lines = doc.splitlines()
        i = next((n for n, l in enumerate(lines) if l.strip() == heading), None)
        if i is None:
            return ""
        j = next((n for n in range(i + 1, len(lines)) if lines[n].startswith("## ")), len(lines))
        return "\n".join(lines[i + 1:j])

    def test_local_layout_doc_matches_the_skeleton(self):
        """docs/06-local.md 는 `local/` 의 지도다 — init_local.sh 의 골격과 갈라지면 안 된다.

        층을 하나 더 두면서 문서를 잊으면, 세션은 그 자리가 '고쳐도 되는 곳'인지 모른다.
        """
        with open(os.path.join(ROOT, "bin", "init_local.sh"), encoding="utf-8") as f:
            init = f.read()
        m = re.search(r"^for d in (.+?); do$", init, re.M)
        self.assertIsNotNone(m, "init_local.sh 의 골격 목록을 찾지 못했다")
        doc = self._local_doc()
        for d in m.group(1).split():
            leaf = d.rsplit("/", 1)[-1]
            self.assertIn(f"{leaf}/", doc,
                          f"init_local.sh 가 만드는 {d}/ 가 docs/06-local.md 에 없다")

    def test_local_doc_maps_every_runtime_path(self):
        """코드가 쓰는 `local/` 경로는 '무엇이 어디에' 표에 행이 있어야 한다.

        절 안을 본다 — 다른 절에 이름만 스쳐도 통과하면 권위 있는 행을 지워도 잡히지 않는다.
        """
        table = self._section(self._local_doc(), "## 무엇이 어디에")
        self.assertTrue(table.strip(), "'## 무엇이 어디에' 절이 비었다")
        for name in ("ledger.jsonl", "positions.jsonl", "frozen_book.json",
                     "watchlist.json", "delivery.jsonl",
                     "inbound.log", "inbound.pid"):
            self.assertIn(name, table, f"{name} 이 '무엇이 어디에' 표에 없다")

    def test_local_doc_states_the_cost_of_deleting(self):
        """지우면 무슨 일이 생기는지가 '지울 때' 절에 있어야 한다 — 오늘 배운 값이다."""
        section = self._section(self._local_doc(), "## 지울 때")
        self.assertTrue(section.strip(), "'## 지울 때' 절이 비었다")
        for name in ("ledger.jsonl", "packets/", "consults/", "stories/",
                     "watchlist.json"):
            self.assertIn(name, section, f"{name} 을 지웠을 때의 결과가 '지울 때' 절에 없다")

    def test_local_doc_does_not_restate_the_memory_edit_rule(self):
        """편집 규칙과 날짜 층은 docs/05-context.md 가 소유한다 — 두 곳에 적으면 갈라진다."""
        doc = self._local_doc()
        self.assertIn("docs/05-context.md", doc, "06-local.md 가 05-context.md 를 가리켜야 한다")
        self.assertNotIn("최신을 복사해", doc,
                         "편집 규칙 본문이 06-local.md 에 복제됐다 — 05-context.md 를 가리키기만 한다")


if __name__ == "__main__":
    unittest.main()
