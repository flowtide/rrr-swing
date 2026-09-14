#!/usr/bin/env python3
"""rrr-swing 인바운드 코어 — 소스(Telegram 태그 / rrr 스트림 엔트리)에 무관한 공통 부분.

결정 3: 이 코드는 매매를 결정하지 않는다. 배달만 한다.
공개 API: parse_tag_line, entry_to_tagged, subscription_matches, DigestBuffer, format_digest,
deliver_stdout|file|herdr, DeliveryLog, Processor(handle_event, tick, finalize), market_check_slot, load_config, load_subscriptions.
- tick 은 30분 시장 체크 wake 도 낸다: 창(inbound.market_check, 기본 09:00~15:30 매 30분) 안에서 슬롯마다 "[market-check ts=HH:MM]" 1줄. 내용은 시각뿐.
- 유효한 구독 선언(local/subscriptions.json)에 해당하는 이벤트만 계좌당 봉당 1건 다이제스트(digest_id=(account_id, bar_ts))로 넘긴다.
- 암묵 필터 없음: 미매칭·검증 실패는 배달 로그(local/delivery.jsonl)에 reason 으로만 남긴다.
- 계약 원본: rrr-gw-skills `rrr-mon-alert-consumer-reference.md` §4, `frontend_api_reference.md` §2.16·§2.17
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
import sys
import urllib.error
import urllib.request

# 태그별 필수 key (계약 §4.2 / §4.4 / §4.6.1)
TAG_SCHEMAS = {
    "#mon": ("rrr_mon_alert_v1", ("schema", "evt", "sym", "lvl", "lvl_px", "px", "bar_ts", "as_of")),
    "#mon-tick": ("rrr_mon_tick_v1", ("schema", "sym", "bar_ts", "o", "h", "l", "c", "v", "watch_id")),
    "#macro": ("rrr_mon_macro_v1", ("schema", "sess", "as_of")),
}
ZONE_EVENTS = ("support_enter", "support_return", "support_break",
               "resistance_enter", "resistance_return", "resistance_break",
               "heartbeat")
# 공급자가 내는 세션 토큰 전량. 구독의 sessions 는 이 안에서 고른다.
SESSION_TOKENS = ("PRE_NXT", "PRE_TO_REG_BREAK", "REG_KRX_NXT", "REG_TO_POST_BREAK", "POST_NXT")
# 구독 event_types 가 덮어야 하는 전체 유형 = zone 6종 + heartbeat + macro.
SUBSCRIBABLE_EVENT_TYPES = ZONE_EVENTS + ("macro",)
# 30분 시장 체크 wake 의 기본값(창·주기). config inbound.market_check 가 덮는다. every_min 0 = 끔.
MARKET_CHECK_DEFAULTS = {"every_min": 30, "from": "09:00", "to": "15:30"}


def _hm_to_min(s) -> int:
    h, m = str(s).split(":")[:2]
    return int(h) * 60 + int(m)


def market_check_slot(now: datetime, mc: dict) -> str | None:
    """창 [from, to](분 단위, 양끝 포함) 안이면 현재 슬롯(HH:MM = from + k·every_min), 밖이면 None. every_min ≤ 0 이면 None(끔)."""
    every = int(mc.get("every_min") or 0)
    if every <= 0:
        return None
    start, end = _hm_to_min(mc.get("from", "09:00")), _hm_to_min(mc.get("to", "15:30"))
    cur = now.hour * 60 + now.minute
    if cur < start or cur > end:
        return None
    slot = start + ((cur - start) // every) * every
    return f"{slot // 60:02d}:{slot % 60:02d}"


def parse_tag_line(text: str) -> dict | None:
    """메시지 마지막 줄의 태그를 파싱한다. 알 수 없는 태그·스키마 불일치·필수 key 부재 → None.

    반환: {"tag": "#mon"|"#mon-tick"|"#macro", "kv": {key: value(원문 그대로)}, "line": 마지막 줄 원문}
    """
    if not text:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    last = lines[-1].strip()
    tokens = last.split()
    tag = tokens[0]
    if tag not in TAG_SCHEMAS:  # '#mon' 접두 매칭 금지 — 정확히 일치해야 한다
        return None
    schema, required = TAG_SCHEMAS[tag]
    kv: dict[str, str] = {}
    for tok in tokens[1:]:
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        kv[k] = v  # 값은 변형하지 않는다(알 수 없는 key 도 보존)
    if tag == "#mon" and kv.get("evt") == "heartbeat":
        required = ("schema", "evt", "sym", "px", "bar_ts", "as_of")
    if kv.get("schema") != schema:
        return None
    if any(k not in kv for k in required):
        return None
    if tag == "#mon-tick":  # 계약 §4.6.1: 키 순서 고정(알 수 없는 key 는 무시하고 순서만 본다)
        seen = [k for tok in tokens[1:] if "=" in tok for k in [tok.split("=", 1)[0]] if k in required]
        if tuple(seen) != required:
            return None
    return {"tag": tag, "kv": kv, "line": last}


def _event_type(tagged: dict) -> str:
    if tagged["tag"] == "#mon":
        return tagged["kv"].get("evt", "")
    if tagged["tag"] == "#mon-tick":
        return "tick"
    return "macro"


def subscription_matches(sub: dict, tagged: dict, now: datetime) -> tuple[bool, str]:
    """구독 선언과 태그의 매칭. (True, "") 또는 (False, reason).

    reason ∈ subscription_expired | unsubscribed | session_unknown | session_mismatch
    - 종목 매칭: #mon / #mon-tick 은 sym ∈ symbols. #macro 는 종목 없음.
    - 유형 매칭: #mon 은 evt, #mon-tick 은 'tick', #macro 는 'macro' 가 event_types 에 명시돼야 한다.
    - 세션 매칭: sess 가 있는 태그(#mon, #macro)만. sess 부재 = 세션 미상 → session_unknown.
    """
    exp = sub.get("expires_at")
    if exp:
        try:
            if now > datetime.fromisoformat(exp):
                return False, "subscription_expired"
        except ValueError:
            return False, "subscription_expired"
    kv = tagged["kv"]
    if tagged["tag"] in ("#mon", "#mon-tick") and kv.get("sym") not in set(sub.get("symbols") or []):
        return False, "unsubscribed"
    if _event_type(tagged) not in set(sub.get("event_types") or []):
        return False, "unsubscribed"
    if tagged["tag"] in ("#mon", "#macro"):
        sess = kv.get("sess")
        if not sess:
            return False, "session_unknown"
        if sess not in set(sub.get("sessions") or []):
            return False, "session_mismatch"
    return True, ""



# ---------------------------------------------------------------- rrr 스트림 엔트리 → tagged (파리티)

# 태그 줄에 실리는 키(계약 순서). stream 전용 필드(trade_date)는 kv 에만 보존한다.
_TAG_LINE_KEYS = {
    "#mon": ("schema", "evt", "sym", "lvl", "lvl_px", "px", "bar_ts", "as_of", "str", "sess", "istr_prev", "istr_rising", "ma20", "ma60"),
    "#mon-tick": ("schema", "sym", "bar_ts", "o", "h", "l", "c", "v", "watch_id"),
    "#macro": ("schema", "sess", "as_of", "slot"),
}
_KIND_TO_TAG = {"zone": "#mon", "tick": "#mon-tick", "macro": "#macro"}


def entry_to_tagged(entry: dict) -> dict | None:
    """rrr `EventEntry.to_wire` JSON(문자열 필드) → parse_tag_line 과 같은 {"tag","kv","line"}.

    kind 가 3종 밖이거나 필수 key 가 없으면 None(bad_tag). `line` 은 계약 키 순서로 되살린 태그 줄이라
    같은 이벤트의 Telegram 태그 줄과 문자 단위로 같다(부가값 없는 포인터 기준).
    """
    if not isinstance(entry, dict):
        return None
    tag = _KIND_TO_TAG.get(str(entry.get("kind", "")))
    if tag is None:
        return None
    schema, required = TAG_SCHEMAS[tag]
    kv = {str(k): str(v) for k, v in entry.items() if k not in ("id", "kind") and v is not None}
    if tag == "#mon" and kv.get("evt") == "heartbeat":
        required = ("schema", "evt", "sym", "px", "bar_ts", "as_of")
    if kv.get("schema") != schema or any(k not in kv for k in required):
        return None
    parts = [tag] + [f"{k}={kv[k]}" for k in _TAG_LINE_KEYS[tag] if k in kv]
    return {"tag": tag, "kv": kv, "line": " ".join(parts)}

# ---------------------------------------------------------------- 다이제스트

def _dedupe_key(account_id: str, tagged: dict) -> tuple:
    kv = tagged["kv"]
    if tagged["tag"] == "#macro":
        return (account_id, "macro", "macro", kv.get("as_of", ""))
    return (account_id, kv.get("sym", ""), _event_type(tagged), kv.get("bar_ts", ""))


class DigestBuffer:
    """계좌당 봉당 1건 다이제스트. 키 = (account_id, bar_ts). 중복 제거 키 = (account_id, sym, evt, bar_ts).

    flush 규칙: 새(더 큰) bar_ts 도착 시 이전 봉 flush, 또는 quiet_sec 경과(flush_due). #macro 는 bar 가 없으므로
    as_of 를 bar_ts 로 삼아 즉시 단독 다이제스트로 flush 한다.
    """

    def __init__(self, account_id: str, quiet_sec: int = 30):
        self.account_id = account_id
        self.quiet_sec = int(quiet_sec)
        self._open: dict[str, dict] = {}     # bar_ts -> digest
        self._last_add: dict[str, datetime] = {}
        self._seen: set[tuple] = set()

    def _new(self, bar_ts: str) -> dict:
        return {"digest_id": f"{self.account_id}:{bar_ts}", "account_id": self.account_id,
                "bar_ts": bar_ts, "lines": [], "keys": [], "raw_event_count": 0, "refs": []}

    def add(self, tagged: dict, now: datetime, *, source: str = "rrr_stream", ref: str | None = None) -> tuple[str, list[dict]]:
        key = _dedupe_key(self.account_id, tagged)
        if key in self._seen:
            return "duplicate", []
        self._seen.add(key)
        flushed: list[dict] = []
        if tagged["tag"] == "#macro":
            d = self._new(tagged["kv"].get("as_of", ""))
            d["lines"].append(tagged["line"]); d["keys"].append(key); d["raw_event_count"] = 1; d["source"] = source
            if ref:
                d["refs"].append(ref)
            return "added", [d]
        bar_ts = tagged["kv"].get("bar_ts", "")
        for other in sorted(self._open):
            if other < bar_ts:  # 새 봉 도착 → 이전 봉 flush
                flushed.append(self._open.pop(other)); self._last_add.pop(other, None)
        d = self._open.setdefault(bar_ts, self._new(bar_ts))
        d["lines"].append(tagged["line"]); d["keys"].append(key); d["raw_event_count"] += 1; d["source"] = source
        if ref:
            d["refs"].append(ref)
        self._last_add[bar_ts] = now
        return "added", flushed

    def is_empty(self) -> bool:
        return not self._open

    def flush_due(self, now: datetime) -> list[dict]:
        out = []
        for bar_ts, last in list(self._last_add.items()):
            if (now - last).total_seconds() >= self.quiet_sec:
                out.append(self._open.pop(bar_ts)); self._last_add.pop(bar_ts)
        return out

    def flush_all(self) -> list[dict]:
        out = [self._open.pop(k) for k in sorted(self._open)]
        self._last_add.clear()
        return out


def format_digest(d: dict, max_chars: int, skipped_bars: int = 0) -> list[str]:
    """한 줄 다이제스트. 태그 줄 보존, ' | ' 로 연결. 길이 상한 초과 시 part=k/n 으로 분할(줄 단위)."""
    prefix = f"[생략 {skipped_bars}봉] " if skipped_bars > 0 else ""
    def header(n_part: int | None, total: int | None) -> str:
        base = f"{prefix}[digest account={d['account_id']} bar_ts={d['bar_ts']} n={d['raw_event_count']}"
        if n_part is not None:
            base += f" part={n_part}/{total}"
        return base + "] "
    one = header(None, None) + " | ".join(d["lines"])
    if len(one) <= max_chars:
        return [one]
    # 분할: 헤더 길이를 감안해 태그 줄을 묶는다(한 줄이 상한을 넘으면 그 줄만 단독 part, 잘라내지 않는다)
    groups: list[list[str]] = []
    cur: list[str] = []
    hdr_len = len(header(99, 99))
    for ln in d["lines"]:
        cand = " | ".join(cur + [ln])
        if cur and hdr_len + len(cand) > max_chars:
            groups.append(cur); cur = [ln]
        else:
            cur.append(ln)
    if cur:
        groups.append(cur)
    total = len(groups)
    return [header(i + 1, total) + " | ".join(g) for i, g in enumerate(groups)]

# ---------------------------------------------------------------- 배달기

class HerdrBlockedError(Exception):
    """Raised when herdr returns agent_blocked."""
    pass


def deliver_stdout(text: str, out=None) -> None:
    import sys
    (out or sys.stdout).write(text + "\n"); (out or sys.stdout).flush()


def deliver_file(text: str, path: str) -> None:
    import json, os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "text": text}, ensure_ascii=False) + "\n")


DELIVER_RETRY_WINDOW_SEC = 300.0   # 배달 실패를 견디는 창(5분). 이 안에서는 백오프 재시도한다.
DELIVER_RETRY_MAX_SLEEP = 30.0


def deliver_herdr(text: str, target: str, run=None, *,
                  retry_window_sec: float = DELIVER_RETRY_WINDOW_SEC,
                  sleep=None, clock=None, log=None) -> None:
    """herdr agent prompt <target> <text>

    두 가지 실패를 다르게 다룬다.

    - `agent_blocked`(에이전트가 응답 중) — **즉시** HerdrBlockedError. 호출부가 큐에 넣고
      다음 기회에 다시 낸다(큐 상한 3, 초과분은 FIFO 로 버린다).
    - 그 밖의 비정상 종료(에이전트 이름 없음, herdr 재시작 등) — `retry_window_sec` 동안
      백오프 재시도. 창을 넘기면 마지막 실패를 CalledProcessError 로 올린다(어댑터 exit 3).
      한 번의 일시적 실패로 상주 프로세스가 끝나면 rs-lead 는 눈을 잃는다.

    재시도 중에는 SSE 를 읽지 않지만 커서는 아직 전진하지 않았으므로(at-least-once),
    끊겨도 재접속 뒤 같은 지점부터 이어진다.
    """
    import subprocess
    import time
    run = run or subprocess.run
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))

    cmd = ["herdr", "agent", "prompt", target, text]
    deadline = clock() + float(retry_window_sec)
    backoff = 1.0
    attempt = 0
    while True:
        attempt += 1
        res = run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            if attempt > 1:
                log(f"herdr_deliver_recovered target={target} attempts={attempt}")
            return
        err_out = ((res.stdout or "") + (res.stderr or "")).strip()
        if "agent_blocked" in err_out:
            raise HerdrBlockedError(f"agent_blocked: {err_out}")
        if clock() >= deadline:
            log(f"herdr_deliver_giving_up target={target} attempts={attempt} last={err_out.splitlines()[-1][:160] if err_out else ''}")
            res.check_returncode()
        delay = min(backoff, DELIVER_RETRY_MAX_SLEEP)
        log(f"herdr_deliver_retry target={target} attempt={attempt} in={delay:.0f}s last={err_out.splitlines()[-1][:120] if err_out else ''}")
        sleep(delay)
        backoff *= 2


class DeliveryLog:
    """local/delivery.jsonl — at-least-once 배달 기록. kind ∈ digest | market_check | skip."""

    def __init__(self, path: str):
        self.path = path

    def append(self, rec: dict) -> None:
        import json, os
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def delivered_ids(self) -> set[str]:
        import json, os
        ids: set[str] = set()
        if not os.path.exists(self.path):
            return ids
        with open(self.path, encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("kind") == "digest" and r.get("digest_id"):
                    ids.add(r["digest_id"])
        return ids



# ---------------------------------------------------------------- 처리기 (소스 무관)

class Processor:
    """tagged → 구독 매칭 → 다이제스트 → 배달. 판단 0. 소스별 변환은 호출자(inbound_rrr)가 한다."""

    def __init__(self, cfg: dict, sub: dict, log: DeliveryLog, deliver, now_fn=None, redeliver: bool = False):
        self.cfg = cfg
        self.sub = sub
        self.log = log
        self.deliver = deliver
        self.now_fn = now_fn or datetime.now
        self.redeliver = redeliver
        inbound = cfg.get("inbound") or {}
        self.max_chars = int(inbound.get("inject_max_chars", 1800))
        account = sub.get("account_id") or cfg.get("account_id") or "account"
        self.buf = DigestBuffer(account, int(inbound.get("quiet_sec", 30)))
        self._already = set() if redeliver else log.delivered_ids()
        self.market_check = {**MARKET_CHECK_DEFAULTS, **{k: v for k, v in (inbound.get("market_check") or {}).items() if not str(k).startswith("_")}}
        self._mc_last: tuple[str, str] | None = None  # (date, slot) 마지막 wake — 슬롯당 1회
        self.stats = {"updates": 0, "digests": 0, "market_checks": 0, "skipped": {}}
        self.delivery_queue: list[dict] = []
        self.skipped_bars: int = 0
        self.cursor_committed_ref: str | None = None
        self._last_seen_ref: str | None = None

    def _now(self) -> str:
        return self.now_fn().isoformat(timespec="seconds")

    def _skip(self, reason: str, *, source: str, ref: str | None, extra: dict | None = None) -> None:
        self.stats["skipped"][reason] = self.stats["skipped"].get(reason, 0) + 1
        rec = {"ts": self._now(), "kind": "skip", "reason": reason, "source": source, "ref": ref}
        if extra:
            rec.update(extra)
        self.log.append(rec)

    def _deliver_one(self, d: dict, commit_last_seen: bool = False) -> None:
        skipped = self.skipped_bars
        lines = format_digest(d, self.max_chars, skipped_bars=skipped)
        for line in lines:
            self.deliver(line)
        self.skipped_bars = 0
        if d.get("refs"):
            self.cursor_committed_ref = d["refs"][-1]
        if commit_last_seen and self.buf.is_empty() and self._last_seen_ref:
            self.cursor_committed_ref = self._last_seen_ref
        self._already.add(d["digest_id"])
        self.stats["digests"] += 1
        # 본문을 남긴다. 메타만 남기면 "무슨 이벤트로 그 판단을 했나" 를 사후에 복원할 수 없다 —
        # 다이제스트는 pane 에만 들어가므로 세션이 끝나면 어디에도 남지 않는다.
        self.log.append({"ts": self._now(), "kind": "digest", "digest_id": d["digest_id"], "bar_ts": d["bar_ts"],
                         "raw_event_count": d["raw_event_count"], "source": d.get("source"),
                         "delivered_to": getattr(self.deliver, "__name__", "deliver"), "lines": lines})

    def _enqueue_and_trim(self, d: dict) -> None:
        self.delivery_queue.append(d)
        while len(self.delivery_queue) > 3:
            discarded = self.delivery_queue.pop(0)
            self.skipped_bars += 1
            if discarded.get("refs"):
                self.cursor_committed_ref = discarded["refs"][-1]
            self._skip("delivery_queue_overflow", source=discarded.get("source"),
                       ref=discarded.get("refs")[-1] if discarded.get("refs") else None,
                       extra={"digest_id": discarded["digest_id"]})

    def _try_flush_queue(self) -> bool:
        while self.delivery_queue:
            item = self.delivery_queue[0]
            is_last = (len(self.delivery_queue) == 1)
            try:
                self._deliver_one(item, commit_last_seen=is_last)
            except HerdrBlockedError:
                return False
            self.delivery_queue.pop(0)
        return True

    def _emit_digests(self, digests: list[dict]) -> None:
        for i, d in enumerate(digests):
            if d["digest_id"] in self._already and not self.redeliver:
                self.stats["skipped"]["already_delivered"] = self.stats["skipped"].get("already_delivered", 0) + 1
                self.log.append({"ts": self._now(), "kind": "skip", "reason": "already_delivered",
                                 "source": d.get("source"), "ref": None, "digest_id": d["digest_id"]})
                continue
            all_flushed = self._try_flush_queue()
            if not all_flushed:
                self._enqueue_and_trim(d)
                continue
            is_last = (i == len(digests) - 1)
            try:
                self._deliver_one(d, commit_last_seen=is_last)
            except HerdrBlockedError:
                self._enqueue_and_trim(d)

    def handle_event(self, tagged: dict | None, *, source: str, ref: str | None = None, extra: dict | None = None) -> str:
        """이벤트 1건. 반환: delivered|buffered|skip:<reason>. tagged=None 은 bad_tag."""
        self.stats["updates"] += 1
        if ref:
            self._last_seen_ref = ref
        now = self.now_fn()
        if tagged is None:
            self._skip("bad_tag", source=source, ref=ref, extra=extra)
            if ref and self.buf.is_empty() and not self.delivery_queue:
                self.cursor_committed_ref = ref
            return "skip:bad_tag"
        ok, reason = subscription_matches(self.sub, tagged, now)
        if not ok:
            self._skip(reason, source=source, ref=ref, extra=extra)
            if ref and self.buf.is_empty() and not self.delivery_queue:
                self.cursor_committed_ref = ref
            return f"skip:{reason}"
        status, flushed = self.buf.add(tagged, now, source=source, ref=ref)
        if status == "duplicate":
            self._skip("duplicate", source=source, ref=ref, extra=extra)
            if ref and self.buf.is_empty() and not self.delivery_queue:
                self.cursor_committed_ref = ref
            return "skip:duplicate"
        self._emit_digests(flushed)
        return "buffered"

    def buffer_empty(self) -> bool:
        return self.buf.is_empty() and len(self.delivery_queue) == 0

    def _market_check(self, now: datetime) -> None:
        """30분 시장 체크 wake: 창 안에서 슬롯이 바뀔 때 1줄. 내용은 시각뿐 — 시장을 읽는 것은 세션의 일이다. 다이제스트와 독립, 소스 공통."""
        slot = market_check_slot(now, self.market_check)
        if slot is None:
            return
        key = (now.date().isoformat(), slot)
        if key == self._mc_last:
            return
        self._mc_last = key
        text = f"[market-check ts={now.strftime('%H:%M')}]"
        try:
            self.deliver(text)
        except Exception as exc:  # noqa: BLE001 - wake 는 best-effort: 배달 실패가 어댑터·다이제스트 경로를 멈추지 않는다(다음 슬롯에 다시)
            self._skip("market_check_deliver_failed", source="adapter", ref=slot, extra={"error": f"{type(exc).__name__}: {exc}"})
            return
        self.stats["market_checks"] += 1
        self.log.append({"ts": self._now(), "kind": "market_check", "slot": slot,
                         "delivered_to": getattr(self.deliver, "__name__", "deliver"), "lines": [text]})

    def tick(self) -> None:
        """루프에서 주기적으로 호출 — 30분 시장 체크 wake + quiet_sec 경과분 flush(서로 독립)."""
        now = self.now_fn()
        self._market_check(now)
        self._try_flush_queue()
        self._emit_digests(self.buf.flush_due(now))

    def finalize(self) -> None:
        self._try_flush_queue()
        self._emit_digests(self.buf.flush_all())


# ---------------------------------------------------------------- 설정·구독 로더

def load_config(path: str) -> dict:
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_subscriptions(path: str) -> dict:
    if not os.path.exists(path):
        return {"symbols": [], "event_types": [], "sessions": [], "expires_at": ""}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 게이트웨이(gw) 설정 및 주소 해석 (단일 출처)

# 키 값은 config.gw.api_key(gitignore 대상)에 둔다. 조회는 이 값을 X-API-Key 헤더로 직접 쓴다 —
# env 로 옮겨 담지 않는다(그 중간 단계는 `.mcp.json` 전개 때문에만 있었고, 그 파일은 없어졌다).


@dataclass(frozen=True)
class GwConfig:
    base_url: str
    api_key: str

    def api_url(self, subpath: str) -> str:
        """Fixed prefix REST {base}/api/..."""
        p = subpath if subpath.startswith("/") else f"/{subpath}"
        if p.startswith("/api/"):
            return f"{self.base_url}{p}"
        return f"{self.base_url}/api{p}"

    @property
    def events_stream_url(self) -> str:
        return f"{self.base_url}/api/events/stream"

    @property
    def events_replay_url(self) -> str:
        return f"{self.base_url}/api/events"

    @property
    def health_url(self) -> str:
        return f"{self.base_url}/api/health"

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        hdrs = {"X-API-Key": self.api_key}
        if extra:
            hdrs.update(extra)
        return hdrs


def resolve_gw_base_url(cfg: dict | None = None, *, env: dict | None = None) -> str:
    """RS_GW_BASE_URL 환경변수가 있으면 gw.base_url 을 덮는다. 없고 config 도 비었으면 거부."""
    if env is None:
        env = os.environ
    if cfg is None:
        cfg = {}

    # 1. RS_GW_BASE_URL 환경변수 우선
    env_url = (env.get("RS_GW_BASE_URL") or "").strip()
    if env_url:
        if env_url.startswith("<") and env_url.endswith(">"):
            raise ValueError("RS_GW_BASE_URL 값이 placeholder 입니다")
        return env_url.rstrip("/")

    # 2. config.gw.base_url fallback
    gw = cfg.get("gw") or {}
    cfg_url = str(gw.get("base_url") or "").strip()
    if (cfg_url.startswith("<") and cfg_url.endswith(">")) or not cfg_url:
        raise ValueError("config.gw.base_url 미설정 (또는 RS_GW_BASE_URL 환경변수 필요)")
    return cfg_url.rstrip("/")


def resolve_gw_config(cfg: dict | None = None, *, env: dict | None = None, require_key: bool = True) -> GwConfig:
    """단일 진실: base_url 과 api_key 를 해석하여 GwConfig 반환."""
    if env is None:
        env = os.environ
    base_url = resolve_gw_base_url(cfg, env=env)

    gw = (cfg or {}).get("gw") or {}
    api_key = str(gw.get("api_key") or "").strip()
    if api_key.startswith("<") and api_key.endswith(">"):
        api_key = ""
    if require_key and not api_key:
        raise ValueError("config.gw.api_key 미설정(gw API 키, 값은 출력하지 않음)")

    return GwConfig(base_url=base_url, api_key=api_key)


def check_gw_health(gw: GwConfig, timeout: float = 5.0) -> bool:
    """GET {base}/api/health 200 확인."""
    req = urllib.request.Request(gw.health_url, headers=gw.headers({"Accept": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200)) == 200
    except Exception:
        return False


def selftest() -> bool:
    cfg = {"gw": {"base_url": "http://h:15410", "api_key": "secret"}}
    env = {"TEST_KEY": "secret"}
    gw = resolve_gw_config(cfg, env=env)
    assert gw.base_url == "http://h:15410"
    assert gw.api_key == "secret"
    assert gw.events_stream_url == "http://h:15410/api/events/stream"
    assert gw.events_replay_url == "http://h:15410/api/events"
    assert gw.health_url == "http://h:15410/api/health"
    assert gw.headers()["X-API-Key"] == "secret"

    env_over = dict(env, RS_GW_BASE_URL="http://override:15410/")
    gw_over = resolve_gw_config(cfg, env=env_over)
    assert gw_over.base_url == "http://override:15410"

    try:
        resolve_gw_config({}, env={})
        assert False, "should fail"
    except ValueError:
        pass
    return True


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="rrr-swing 인바운드 코어 도구")
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--check-gw-health", action="store_true", help="gw 설정 해석 + GET {base}/api/health 200 검증")
    ap.add_argument("--selftest", action="store_true", help="단위 자가 점검")
    a = ap.parse_args(argv)

    if a.selftest:
        selftest()
        print("inbound_core selftest ok")
        return 0

    if a.check_gw_health:
        try:
            cfg = {}
            if os.path.exists(a.config):
                with open(a.config, encoding="utf-8") as f:
                    cfg = json.load(f)
            gw = resolve_gw_config(cfg)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        if not check_gw_health(gw):
            print(f"ERROR: gw /api/health 확인 실패 ({gw.health_url})", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


