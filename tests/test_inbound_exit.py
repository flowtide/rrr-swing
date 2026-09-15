"""인바운드 어댑터의 종료 조건과 정리 계약.

어댑터는 상주 프로세스다. 조용히 사라지면 rs-lead 는 "이벤트가 없는 장"과 구분할 수 없고,
heartbeat 마저 함께 끊기므로 안전망도 사라진다. 그래서 **어떤 경로로 끝나든 마지막 줄에
종료 사유를 남긴다**. 라이브에서 herdr 배달 1회 실패가 트레이스백만 남기고 프로세스를
끝내 이 계약이 생겼다.

정리 쪽도 함께 못박는다: 어댑터는 하나만 살아야 하는데(중복이면 같은 다이제스트가 두 번
간다) 기존 코드는 kill 뒤 종료를 확인하지 않고 바로 새로 띄웠다.
"""
import io
import json
import os
import re
import subprocess
import sys
import time
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import inbound_core as core  # noqa: E402
import inbound_rrr as rr  # noqa: E402

START = os.path.join(ROOT, "bin", "start.sh")


class ExitCodeContractTest(unittest.TestCase):
    def test_codes_are_distinct_and_documented(self):
        """상수와 모듈 docstring 의 표가 갈리면 안 된다."""
        codes = {
            "ok": rr.EXIT_OK,
            "config": rr.EXIT_CONFIG,
            "undelivered": rr.EXIT_UNDELIVERED,
            "crashed": rr.EXIT_CRASHED,
        }
        self.assertEqual(len(set(codes.values())), len(codes), codes)
        doc_lines = [l.strip() for l in (rr.__doc__ or "").splitlines()]
        for name, code in codes.items():
            self.assertTrue(any(re.match(rf"{code}\s+{name}\b", l) for l in doc_lines),
                            f"docstring 표에 '{code}  {name}' 줄이 없다")

    def test_log_exit_writes_one_line_with_reason_and_returns_code(self):
        buf = io.StringIO()
        rc = rr.log_exit(rr.EXIT_UNDELIVERED, "undelivered", "herdr … → exit 1", out=buf)
        self.assertEqual(rc, rr.EXIT_UNDELIVERED)
        line = buf.getvalue().strip()
        self.assertEqual(len(line.splitlines()), 1, line)
        self.assertIn("EXIT code=3", line)
        self.assertIn("reason=undelivered", line)
        self.assertIn("detail=", line)

    def test_log_exit_detail_is_single_line_and_bounded(self):
        """detail 에 트레이스백이 통째로 들어가면 마지막 줄을 읽는 의미가 없다."""
        buf = io.StringIO()
        rr.log_exit(rr.EXIT_CRASHED, "crashed", "a\nb\n" + "x" * 500, out=buf)
        line = buf.getvalue().strip()
        self.assertEqual(len(line.splitlines()), 1)
        self.assertLess(len(line), 320, line)

    def test_log_exit_omits_detail_when_absent(self):
        buf = io.StringIO()
        rr.log_exit(rr.EXIT_OK, "catch_up_done", out=buf)
        self.assertNotIn("detail=", buf.getvalue())


class ExitPathsTest(unittest.TestCase):
    """실제 프로세스를 돌려 종료 코드와 마지막 줄을 본다."""

    def _run(self, *args, cfg=None):
        d = tempfile.mkdtemp()
        cfgp = os.path.join(d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump(cfg if cfg is not None else {"gw": {"base_url": "", "api_key": ""}}, f)
        env = dict(os.environ)
        env.pop("RS_GW_BASE_URL", None)
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "inbound_rrr.py"), "--config", cfgp, *args],
            capture_output=True, text=True, env=env, cwd=ROOT, timeout=60,
        )
        return r

    def _exit_line(self, r):
        lines = [l for l in (r.stdout + r.stderr).splitlines() if "EXIT code=" in l]
        self.assertTrue(lines, f"EXIT 줄 없음:\n{r.stdout}\n{r.stderr}")
        return lines[-1]

    def test_missing_gw_config_exits_config_with_reason(self):
        r = self._run("--deliver", "stdout")
        self.assertEqual(r.returncode, rr.EXIT_CONFIG)
        self.assertIn("reason=config", self._exit_line(r))

    def test_target_always_defaults_so_there_is_no_missing_target_path(self):
        """`--target ''` 는 기본값 rs-lead 로 떨어진다 — "target 없음" 종료 경로는 존재하지 않는다.

        전에는 그 분기가 코드에 있었으나 기본값 때문에 도달할 수 없었다. 종료 조건 표에
        도달 불가 경로를 적어 두면 표를 믿을 수 없게 된다.
        """
        self.assertNotIn("--target 이 필요", open(os.path.join(ROOT, "bin", "inbound_rrr.py"), encoding="utf-8").read())

    def test_selftest_does_not_emit_exit_line(self):
        r = self._run("--selftest")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("EXIT code=", r.stdout + r.stderr)


class DeliveryFailureExitTest(unittest.TestCase):
    """배달 실패로 끝나는 경로를 끝까지 돌린다 — 라이브에서 실제로 난 사고다."""

    def setUp(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading

        entry = {
            "id": "1-0", "kind": "zone", "schema": "rrr_mon_alert_v1",
            "trade_date": "2026-09-14", "as_of": "2026-09-14T10:05:00", "sess": "REG_KRX_NXT",
            "sym": "005930", "bar_ts": "2026-09-14T10:00:00", "evt": "support_return",
            "lvl": "s1", "lvl_px": "70000", "px": "70500",
        }
        # 어댑터는 SSE 하나만 쓴다(--once 로 페이지를 긁는 catch-up 은 커서와 함께 폐기됐다).
        sse = (f"id: 1-0\nevent: zone\ndata: {json.dumps(entry, ensure_ascii=False)}\n\n").encode()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/api/events/stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(sse)))
                    self.end_headers()
                    self.wfile.write(sse)
                    return
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

        self.d = tempfile.mkdtemp()
        self.cfgp = os.path.join(self.d, "config.json")
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump({"account_id": "acct", "gw": {"base_url": self.base, "api_key": "k"}}, f)
        self.watchp = os.path.join(self.d, "watchlist.json")
        with open(self.watchp, "w", encoding="utf-8") as f:
            json.dump({"005930": {}}, f)
        # PATH 앞에 실패하는 herdr 를 둔다.
        self.stubs = os.path.join(self.d, "stubs")
        os.makedirs(self.stubs)
        hp = os.path.join(self.stubs, "herdr")
        with open(hp, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\necho 'boom: no such agent' >&2\nexit 1\n")
        os.chmod(hp, 0o755)

    def _run(self):
        # 라이브 재시도 창은 5분이다 — 테스트에서는 0 으로 줄여 곧장 포기 경로를 본다.
        env = dict(os.environ, PATH=self.stubs + os.pathsep + os.environ.get("PATH", ""),
                   RS_DELIVER_RETRY_SEC="0")
        env.pop("RS_GW_BASE_URL", None)
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "inbound_rrr.py"),
             "--config", self.cfgp, "--watchlist", self.watchp,
             "--delivery-log", os.path.join(self.d, "dl.jsonl"),
             "--deliver", "herdr", "--target", "rs-lead", "--max-reconnects", "0"],
            capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)

    def test_delivery_failure_exits_undelivered_with_reason_on_last_line(self):
        r = self._run()
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, rr.EXIT_UNDELIVERED, out)
        last = [l for l in out.splitlines() if l.strip()][-1]
        self.assertIn("EXIT code=3", last, f"마지막 줄이 종료 사유가 아니다: {last}")
        self.assertIn("reason=undelivered", last)

    def test_exit_line_carries_the_failing_command_and_what_herdr_said(self):
        """한 줄로 진단이 끝나야 한다 — 트레이스백을 거슬러 읽게 하지 않는다."""
        out = self._run().stderr
        last = [l for l in out.splitlines() if l.strip()][-1]
        self.assertIn("herdr agent prompt", last, last)
        self.assertIn("exit 1", last, last)
        self.assertIn("boom", last, "herdr 가 남긴 말이 빠졌다")
        self.assertFalse(out.splitlines()[-1].startswith(("Traceback", "  File", "subprocess.")),
                         "마지막 줄이 트레이스백이다")


class DeliveryRetryTest(unittest.TestCase):
    """배달 실패는 창(기본 5분) 안에서 견딘다 — 일시적 실패로 상주 프로세스가 끝나면 안 된다."""

    class _Run:
        """returncode 시퀀스를 돌려주는 subprocess.run 대역."""

        def __init__(self, codes, err=""):
            self.codes, self.err, self.calls = list(codes), err, 0

        def __call__(self, cmd, capture_output=None, text=None):
            self.calls += 1
            code = self.codes.pop(0) if self.codes else 0
            return subprocess.CompletedProcess(cmd, code, stdout="", stderr=self.err if code else "")

    def _clock(self, step=1.0):
        t = {"v": 0.0}

        def now():
            return t["v"]

        def sleep(d):
            t["v"] += d
        return now, sleep

    def test_transient_failure_is_retried_then_succeeds(self):
        run = self._Run([1, 1, 1, 0], err="boom")
        now, sleep = self._clock()
        logs = []
        core.deliver_herdr("x", "rs-lead", run=run, sleep=sleep, clock=now, log=logs.append)
        self.assertEqual(run.calls, 4)
        self.assertTrue(any("recovered" in l for l in logs), logs)

    def test_gives_up_after_the_window_and_raises(self):
        run = self._Run([1] * 1000, err="boom")
        now, sleep = self._clock()
        logs = []
        with self.assertRaises(subprocess.CalledProcessError):
            core.deliver_herdr("x", "rs-lead", run=run, sleep=sleep, clock=now, log=logs.append)
        self.assertGreater(run.calls, 3, "창 안에서 재시도하지 않았다")
        self.assertGreaterEqual(now(), core.DELIVER_RETRY_WINDOW_SEC, "창을 다 쓰지 않고 포기했다")
        self.assertTrue(any("giving_up" in l for l in logs), logs)

    def test_backoff_is_capped(self):
        run = self._Run([1] * 1000, err="boom")
        now, sleep = self._clock()
        delays = []
        core_sleep = sleep

        def spy(d):
            delays.append(d)
            core_sleep(d)
        with self.assertRaises(subprocess.CalledProcessError):
            core.deliver_herdr("x", "rs-lead", run=run, sleep=spy, clock=now, log=lambda m: None)
        self.assertLessEqual(max(delays), core.DELIVER_RETRY_MAX_SLEEP)

    def test_agent_blocked_is_not_retried(self):
        """차단은 재시도 대상이 아니다 — 호출부의 큐(상한 3, FIFO)가 받는다."""
        run = self._Run([1], err='{"error":{"code":"agent_blocked"}}')
        now, sleep = self._clock()
        with self.assertRaises(core.HerdrBlockedError):
            core.deliver_herdr("x", "rs-lead", run=run, sleep=sleep, clock=now, log=lambda m: None)
        self.assertEqual(run.calls, 1, "차단을 재시도했다")
        self.assertEqual(now(), 0.0, "차단인데 대기했다")

    def test_window_default_is_five_minutes(self):
        self.assertEqual(core.DELIVER_RETRY_WINDOW_SEC, 300.0)


class RuntimeNotStartedWithoutAdapterTest(unittest.TestCase):
    """어댑터가 기동에 실패하면 런타임을 띄우지 않는다.

    눈이 없는 세션은 "이벤트가 없는 장"과 발신원 사망을 구분하지 못한다. exec 를 지나면
    셸이 런타임으로 바뀌어 되돌릴 수 없으므로, 그 앞에서 막는다.
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.stubs = os.path.join(self.d, "stubs")
        os.makedirs(self.stubs)
        # 런타임이 떠 버리면 즉시 드러나도록 기록을 남기는 스텁을 둔다.
        self.launched = os.path.join(self.d, "launched")
        cp = os.path.join(self.stubs, "claude")
        with open(cp, "w", encoding="utf-8") as f:
            f.write(f"#!/usr/bin/env bash\ntouch {self.launched}\n")
        os.chmod(cp, 0o755)
        self.cfgp = os.path.join(self.d, "config.json")
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump({"mode": "dry_run", "account_id": "acct", "operator_channel": "console",
                       # 어댑터가 즉시 죽도록 키를 비운다(EXIT code=2 config).
                       "gw": {"base_url": "PLACEHOLDER", "api_key": "k"},
                       "inbound": {"source": "rrr_stream", "target": "rs-lead"}}, f)

    def _run(self, health_ok=True):
        """health 는 통과시키되 어댑터는 죽게 만든다 — 순서상 health 검사가 먼저다."""
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/api/health"):
                    body = b'{"ok":true}'
                    self.send_response(200)
                else:
                    body = b'nope'
                    self.send_response(401)   # 어댑터는 401 을 보고 즉시 종료한다(exit 2)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        env = dict(os.environ, RS_CONFIG=self.cfgp, FORCE_OUTSIDE_HERDR="1",
                   RS_GW_BASE_URL=base, RS_LOCAL=os.path.join(self.d, "local"),
                   RS_ADAPTER_GRACE_SEC="3", RS_DELIVER_RETRY_SEC="0",
                   PATH=self.stubs + os.pathsep + os.environ.get("PATH", ""))
        return subprocess.run(["bash", START, "--role", "lead"], capture_output=True, text=True,
                              env=env, cwd=ROOT, timeout=60)

    def test_runtime_is_not_launched_when_adapter_dies_at_startup(self):
        r = self._run()
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("눈이 없는 세션은 띄우지 않는다", out)
        self.assertFalse(os.path.exists(self.launched), "어댑터가 죽었는데 런타임을 띄웠다")

    def test_failure_shows_the_adapter_log_tail(self):
        """왜 죽었는지 그 자리에서 보여야 한다 — 로그를 따로 찾아가게 하지 않는다."""
        out = self._run().stdout + self._run().stderr
        self.assertIn("inbound.log", out)

    def test_check_runs_both_after_launch_and_immediately_before_exec(self):
        """두 자리 모두에서 확인해야 한다.

        기동 직후 확인만으로는 그 뒤 MCP 확정본 생성 등을 거치는 동안 죽는 경우를 놓친다.
        그 창은 타이밍을 강제할 수 없어(프로세스 사망 시점을 재현할 수 없다) 구조로 못박는다 —
        exec 직전 호출을 지우는 뮤턴트가 행동 테스트만으로는 살아남았다.

        셸을 런타임으로 넘기는 자리도 **한 곳**이어야 한다. 런타임별로 exec 를 따로 두면
        확인을 거치지 않는 두 번째 출구가 생긴다(agy 를 더할 때 실제로 그렇게 짰다가
        이 테스트가 잡았다). 명령은 확인 전에 확정하고 exec 는 하나로 모은다.
        """
        with open(START, encoding="utf-8") as f:
            src = f.read()
        calls = [i for i, line in enumerate(src.splitlines())
                 if line.strip().startswith("abort_if_adapter_dead ")]
        self.assertEqual(len(calls), 2, f"호출이 2곳이어야 한다(현재 {len(calls)}곳)")
        execs = [i for i, line in enumerate(src.splitlines()) if line.startswith("exec ")]
        self.assertEqual(len(execs), 1,
                         f"셸을 런타임으로 바꾸는 자리는 한 곳이어야 한다(현재 {len(execs)}곳) — "
                         "런타임이 늘어도 명령을 먼저 확정하고 확인 뒤 한 번만 exec 한다")
        exec_line = execs[0]
        self.assertLess(calls[-1], exec_line, "exec 직전 확인이 없다")
        between = src.splitlines()[calls[-1] + 1:exec_line]
        self.assertFalse([l for l in between if l.strip() and not l.strip().startswith("#")],
                         f"확인과 exec 사이에 코드가 끼었다: {between}")

    def test_stale_pid_file_is_removed_on_failure(self):
        self._run()
        self.assertFalse(os.path.exists(os.path.join(self.d, "local", "inbound.pid")),
                         "죽은 pid 가 파일에 남았다")


class DeliveryLogKeepsContentTest(unittest.TestCase):
    """배달 로그는 본문을 남긴다.

    다이제스트 본문은 pane 에만 들어간다. 메타(digest_id·bar_ts·건수)만 남기면 세션이
    끝난 뒤 "무슨 이벤트로 그 판단을 했나" 를 어디서도 복원할 수 없다.
    """

    def _proc(self, tmp):
        cfg = {"account_id": "acct", "inbound": {}}
        watchp = os.path.join(tmp, "watchlist.json")
        with open(watchp, "w", encoding="utf-8") as f:
            json.dump({"005930": {}}, f)
        log = core.DeliveryLog(os.path.join(tmp, "delivery.jsonl"))
        out = []
        return core.Processor(cfg, core.WatchList(watchp), log, deliver=out.append), out, os.path.join(tmp, "delivery.jsonl")

    def _rows(self, path, kind):
        with open(path, encoding="utf-8") as f:
            return [json.loads(l) for l in f if json.loads(l).get("kind") == kind]

    def test_digest_record_carries_the_delivered_lines(self):
        tmp = tempfile.mkdtemp()
        proc, out, path = self._proc(tmp)
        entry = {"id": "1-0", "kind": "zone", "schema": "rrr_mon_alert_v1",
                 "trade_date": "2026-09-14", "as_of": "2026-09-14T10:05:00", "sess": "REG_KRX_NXT",
                 "sym": "005930", "bar_ts": "2026-09-14T10:00:00", "evt": "support_return",
                 "lvl": "s1", "lvl_px": "70000", "px": "70500"}
        proc.handle_event(core.entry_to_tagged(entry), source="rrr_stream", ref="1-0")
        proc.finalize()
        rows = self._rows(path, "digest")
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get("lines"), out, "배달한 본문과 기록이 다르다")
        self.assertIn("005930", " ".join(rows[0]["lines"]))
        self.assertIn("support_return", " ".join(rows[0]["lines"]))


class LocalPathsAreAuthoritativeTest(unittest.TestCase):
    """RS_LOCAL 이 커서·배달 로그의 권위여야 한다.

    어댑터의 기본값은 저장소의 local/ 로 하드코딩돼 있다. start.sh 가 경로를 넘기지 않으면
    RS_LOCAL 을 옮겨도 그 둘만 진짜 파일에 쓴다 — 테스트가 운영 로그를 오염시킨다(실제로 그랬다).
    """

    def test_start_sh_passes_local_paths_to_the_adapter(self):
        with open(START, encoding="utf-8") as f:
            src = f.read()
        launch = src.split("인바운드 어댑터 백그라운드 기동")[1].split("RS_ADAPTER_PID=$!")[0]
        # 변수를 정의만 하고 쓰지 않으면 아무 소용이 없다 — **실제 기동 줄**을 본다.
        # (정의만 검사했더니 사용을 지운 뮤턴트가 살아남았다.)
        nohup_lines = [l for l in launch.splitlines() if "nohup python3" in l]
        self.assertEqual(len(nohup_lines), 2, nohup_lines)
        for line in nohup_lines:
            self.assertIn("$PATHS", line, f"경로를 넘기지 않는 기동 줄: {line.strip()}")
        defn = [l for l in launch.splitlines() if l.strip().startswith("PATHS=")]
        self.assertEqual(len(defn), 1, defn)
        for flag, path in (("--watchlist", "$RS_LOCAL/watchlist.json"),
                           ("--delivery-log", "$RS_LOCAL/delivery.jsonl"),
                           ("--inbox", "$RS_LOCAL/inbox.jsonl")):
            self.assertIn(f"{flag} {path}", defn[0], f"{flag} 가 RS_LOCAL 을 따르지 않는다")

    def test_adapter_writes_only_under_the_given_local_dir(self):
        """실제로 띄워서 저장소 파일이 건드려지지 않는지 본다."""
        repo_log = os.path.join(ROOT, "local", "delivery.jsonl")
        before = os.path.getsize(repo_log) if os.path.exists(repo_log) else None
        d = tempfile.mkdtemp()
        loc = os.path.join(d, "local")
        cfgp = os.path.join(d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"account_id": "acct", "gw": {"base_url": "http://127.0.0.1:1", "api_key": "k"}}, f)
        watchp = os.path.join(d, "watchlist.json")
        with open(watchp, "w", encoding="utf-8") as f:
            json.dump({"005930": {}}, f)
        os.makedirs(loc, exist_ok=True)
        env = dict(os.environ)
        env.pop("RS_GW_BASE_URL", None)
        subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "inbound_rrr.py"), "--config", cfgp,
             "--watchlist", watchp,
             "--delivery-log", os.path.join(loc, "delivery.jsonl"),
             "--deliver", "stdout", "--max-reconnects", "0"],
            capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)
        after = os.path.getsize(repo_log) if os.path.exists(repo_log) else None
        self.assertEqual(before, after, "저장소의 local/delivery.jsonl 이 변했다")


class CleanupWaitsForDeathTest(unittest.TestCase):
    """정리 단계는 이전 어댑터가 실제로 죽은 뒤에 새로 띄운다."""

    def setUp(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.health_url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.addCleanup(self.srv.shutdown)
        self.d = tempfile.mkdtemp()
        self.cfgp = os.path.join(self.d, "config.json")
        with open(self.cfgp, "w", encoding="utf-8") as f:
            json.dump({"mode": "dry_run", "account_id": "acct", "operator_channel": "console",
                       "gw": {"base_url": "http://127.0.0.1:1", "api_key": "k"},
                       "inbound": {"source": "rrr_stream", "target": "rs-lead"}}, f)

    def _fake_adapter(self, *, immortal: bool, decoy: bool = False):
        """어댑터로 보이는(또는 보이기만 하는) 프로세스를 띄운다. cwd 는 저장소여야 잡힌다."""
        body = "import time\n"
        if immortal:
            body = "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        body += "while True: time.sleep(0.2)\n"
        if decoy:
            # 런타임처럼 **인자 본문 안**에만 경로가 들어간 프로세스.
            path = os.path.join(self.d, "runtime_like.py")
            # 프롬프트 본문에 경로와 커서 인자가 **둘 다** 들어간 최악의 경우 — RS_LOCAL 범위
            # 제한만으로는 걸러지지 않으므로, 위치 판별(argv[0]|argv[1])만이 이것을 살린다.
            prompt = ("감시 목록은 python3 bin/watchlist.py show 로 본다. "
                      f"어댑터는 --watchlist {os.path.join(self.d, 'local')}/watchlist.json 을 읽는다.")
            argv = [sys.executable, path, "--prompt", prompt]
        else:
            d = os.path.join(self.d, "bin")
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, "inbound_rrr.py")
            # 정리는 같은 $RS_LOCAL 을 보는 어댑터만 죽인다 — 그 표식을 붙인다.
            argv = [sys.executable, path, "--watchlist", f"{os.path.join(self.d, 'local')}/watchlist.json"]
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        return proc

    def test_cleanup_does_not_kill_a_process_that_only_mentions_the_path(self):
        """런타임 세션을 죽이면 안 된다.

        `pgrep -f` 는 명령줄 어디에든 문자열이 있으면 잡는다. 부트 프롬프트에
        어댑터 명령이 적혀 있어 살아 있는 rs-lead claude 가
        정리 대상이 됐다 — 재기동이 다른 pane 의 세션을 죽인다(라이브에서 확인).
        """
        decoy = self._fake_adapter(immortal=False, decoy=True)
        env = dict(os.environ, RS_CONFIG=self.cfgp, FORCE_OUTSIDE_HERDR="1",
                   RS_GW_BASE_URL=self.health_url, RS_LOCAL=os.path.join(self.d, "local"),
                   RS_ADAPTER_GRACE_SEC="0", RS_CLEANUP_TRIES="3")
        r = subprocess.run(["bash", START, "--role", "lead", "--adapter-only"], capture_output=True,
                           text=True, env=env, cwd=ROOT, timeout=60)
        self.assertNotIn(f"pid={decoy.pid}", r.stdout + r.stderr, "경로를 언급만 한 프로세스를 정리 대상으로 삼았다")
        self.assertIsNone(decoy.poll(), "런타임처럼 생긴 프로세스를 죽였다")
        # 이 실행이 띄운 어댑터는 남기지 않는다.
        pidf = os.path.join(self.d, "local", "inbound.pid")
        if os.path.exists(pidf):
            try:
                os.kill(int(open(pidf).read().strip()), 9)
            except (OSError, ValueError):
                pass

    def test_cleanup_finds_the_adapter_behind_interpreter_flags(self):
        """`python3 -u bin/inbound_rrr.py` 도 찾아야 한다.

        스크립트를 argv[0]|argv[1] 위치로만 찾으면 인터프리터 플래그(`-u`·`-X`·`-O`) 하나에
        한 칸씩 밀려 대상에서 빠진다. 로그를 즉시 flush 하려고 `-u` 로 띄운 어댑터가 정리되지
        않아, 다음 기동이 둘째 어댑터를 올렸다(라이브에서 났다).

        스크립트는 **인터프리터 뒤 첫 비(非)플래그 인자**다. 그 자리를 본다.
        """
        os.makedirs(os.path.join(self.d, "bin"), exist_ok=True)
        path = os.path.join(self.d, "bin", "inbound_rrr.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write("import time\nwhile True: time.sleep(0.2)\n")
        proc = subprocess.Popen([sys.executable, "-u", path, "--watchlist", "x/watchlist.json"],
                                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        env = dict(os.environ, RS_CONFIG=self.cfgp, FORCE_OUTSIDE_HERDR="1",
                   RS_GW_BASE_URL=self.health_url, RS_LOCAL=os.path.join(self.d, "local"),
                   RS_ADAPTER_GRACE_SEC="0", RS_CLEANUP_TRIES="3")
        r = subprocess.run(["bash", START, "--role", "lead", "--adapter-only"], capture_output=True,
                           text=True, env=env, cwd=ROOT, timeout=60)
        self.assertIn(f"pid={proc.pid}", r.stdout + r.stderr, "`-u` 로 뜬 어댑터를 지나쳤다")
        for _ in range(50):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        self.assertIsNotNone(proc.poll(), "`-u` 로 뜬 어댑터가 살아남았다")
        pidf = os.path.join(self.d, "local", "inbound.pid")
        if os.path.exists(pidf):
            try:
                os.kill(int(open(pidf).read().strip()), 9)
            except (OSError, ValueError):
                pass

    def test_cleanup_takes_every_adapter_of_my_uid(self):
        """내 uid 의 어댑터는 $RS_LOCAL 이 달라도 정리한다 — 하나만 살아야 하기 때문이다.

        인자로 범위를 좁히면 플래그 이름을 바꾸는 순간 조용히 깨진다. 옛 이름으로 뜬
        어댑터가 정리되지 않고 남아, 두 어댑터가 같은 세션에 배달했다(라이브에서 났다).
        식별은 **스크립트 이름 하나**로만 하고 범위는 **내 uid** 로 좁힌다.

        대가: 이 스위트를 돌리면 운영 어댑터도 같이 죽는다. 운영 중에는 돌리지 않는다.
        """
        other = os.path.join(self.d, "other")
        os.makedirs(os.path.join(self.d, "bin"), exist_ok=True)
        path = os.path.join(self.d, "bin", "inbound_rrr.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write("import time\nwhile True: time.sleep(0.2)\n")
        proc = subprocess.Popen([sys.executable, path, "--watchlist", f"{other}/watchlist.json"],
                                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        env = dict(os.environ, RS_CONFIG=self.cfgp, FORCE_OUTSIDE_HERDR="1",
                   RS_GW_BASE_URL=self.health_url, RS_LOCAL=os.path.join(self.d, "local"),
                   RS_ADAPTER_GRACE_SEC="0", RS_CLEANUP_TRIES="3")
        r = subprocess.run(["bash", START, "--role", "lead", "--adapter-only"], capture_output=True,
                           text=True, env=env, cwd=ROOT, timeout=60)
        self.assertIn(f"pid={proc.pid}", r.stdout + r.stderr, "다른 $RS_LOCAL 의 어댑터를 지나쳤다")
        for _ in range(50):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        self.assertIsNotNone(proc.poll(), "정리 대상인데 살아남았다")
        pidf = os.path.join(self.d, "local", "inbound.pid")
        if os.path.exists(pidf):
            try:
                os.kill(int(open(pidf).read().strip()), 9)
            except (OSError, ValueError):
                pass

    def test_cleanup_is_scoped_to_my_uid(self):
        """다른 사용자의 프로세스는 내 것이 아니다 — ps 가 uid 로 좁혀져 있어야 한다."""
        with open(START, encoding="utf-8") as f:
            cleanup = f.read().split("--- ④ 인바운드 어댑터")[1].split("mkdir -p")[0]
        self.assertIn("id -u", cleanup, "내 uid 를 구하지 않는다")
        self.assertIn('-v u="$MY_UID"', cleanup, "내 uid 를 awk 에 넘기지 않는다")
        self.assertRegex(cleanup, r"\$2 ?[!=]= ?u", "ps 결과를 uid 로 거르지 않는다")

    def test_start_sh_refuses_when_previous_adapter_will_not_die(self):
        """SIGTERM 을 무시하는 어댑터가 남아 있으면 기동하지 않는다.

        기다리지 않고 띄우면 둘이 겹쳐 같은 다이제스트가 두 번 배달된다.

        정상 코드는 1초 안에 거부한다. 종료 확인이 사라지면 start.sh 가 그대로 런타임 기동까지
        진행하므로 여기서 타임아웃으로 잡힌다 — 그 대기를 짧게 두어 회귀를 빨리 드러낸다.
        """
        # 정리는 스크립트가 **실행 파일 자리**에 있을 때만 어댑터로 본다 — 같은 이름으로 위장한다.
        proc = self._fake_adapter(immortal=True)
        try:
            # 정리는 gw /api/health 검사 뒤에 온다 — 로컬 서버로 그 앞단을 통과시킨다(네트워크 0).
            env = dict(os.environ, RS_CONFIG=self.cfgp, FORCE_OUTSIDE_HERDR="1",
                       RS_GW_BASE_URL=self.health_url,
                       RS_LOCAL=os.path.join(self.d, "local"), RS_CLEANUP_TRIES="3")
            r = subprocess.run(["bash", START, "--role", "lead"], capture_output=True, text=True,
                               env=env, cwd=ROOT, timeout=25)
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 1, out)
            self.assertIn("종료되지 않았다", out)
            self.assertNotIn("adapter=bin/inbound_rrr.py pid=", out, "죽지 않았는데 새로 띄웠다")
        finally:
            pass

    def test_cleanup_kill_is_verified_in_source(self):
        """kill 뒤 생존 확인이 소스에 남아 있어야 한다(리팩터로 사라지기 쉬운 줄)."""
        with open(START, encoding="utf-8") as f:
            src = f.read()
        cleanup = src.split("--- ④ 인바운드 어댑터")[1].split("mkdir -p")[0]
        self.assertIn("kill -0", cleanup, "종료 확인 없이 바로 새로 띄운다")
        self.assertRegex(cleanup, r"exit 1", "죽지 않았을 때 중단하지 않는다")
        # 인자로 식별하면 플래그 이름을 바꾸는 순간 조용히 깨진다(라이브에서 났다).
        for arg in ("--watchlist", "--cursor", "--delivery-log", "RS_LOCAL"):
            self.assertNotIn(arg, cleanup, f"정리가 인자 '{arg}' 로 식별한다")


if __name__ == "__main__":
    unittest.main()
