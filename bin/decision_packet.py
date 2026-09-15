#!/usr/bin/env python3
"""rrr-swing 결정 패킷 조립 — GET 전용. 판단·점수·추천 0.

세션(LLM)이 이벤트 wake 에서 증거를 읽기(docs/03-evidence_guide.md, AGENTS.md §3) 위해 pull 하는 증거 묶음:
`/api/health`(1회) + `/api/stocks/{sym}/context`(lean) + `/api/stocks/{sym}/flow` 필수, 항상 `/api/stocks/{sym}/market-context`
(호가·OFI 등 5분봉, bars=2 로 [직전 확정봉, 현재 partial]) 도 조회하나 필수는 아니다(실패해도 rc 영향 없음), `--with-momentum` 이면 `/api/stocks/{sym}/momentum` 추가.
응답은 **원문 그대로** 저장하고, 계약 사실(schema_version 일치·symbol 일치·신선도 필드 age_sec/partial/freshness·호가 확정봉 confidence)만 요약한다.
market-context 는 REQUIRED_SOURCES 가 아니다 — 실패해도 패킷은 성공(rc=0), 확정봉 confidence 가 ok 아니면 경고만 남긴다
(docs/07-orderbook_evidence_study.md §6, "판단 없이 사실만" — CK-8 채택 등 규칙 반영은 정규장 검증 후).
출력: local/packets/<ts>-<sym>.json (packet_id·sym·bar_ts·fetched_at·summary). X-API-Key = config gw.api_key.
exit: 0 필수 소스 성공 / 1 필수 소스 실패(패킷은 남긴다 — 증거) / 2 인자·설정 오류. 재사용 출처: rrr-trading2 bin/decision_packet.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inbound_core as core  # noqa: E402

PACKET_SCHEMA_VERSION = "rrr_swing_packet.v1"
EXPECTED_SCHEMAS = {"context": "1.0", "flow": "flow.1.0", "momentum": "momentum.1.0"}
REQUIRED_SOURCES = ("health", "context", "flow")
REQUEST_TIMEOUT_SEC = 8.0
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
FRESHNESS_SUFFIXES = ("age_sec", "age_days", "as_of", "sampled_at")
FRESHNESS_KEYS = ("partial", "freshness", "investor_freshness")


def fetch_json(base_url: str, path: str, *, token: str, timeout: float = REQUEST_TIMEOUT_SEC, max_bytes: int = MAX_RESPONSE_BYTES) -> dict:
    """GET 1회. 성공/실패를 구조화해 돌려주고 예외를 던지지 않는다."""
    started = time.monotonic()
    result: dict = {"success": False, "status_code": None, "elapsed_ms": None, "data": None, "error": None}
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}{path}", method="GET",
                                     headers={"Accept": "application/json", "User-Agent": "rrr-swing-packet/1", "X-API-Key": token})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["status_code"] = int(getattr(resp, "status", 200))
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError(f"response exceeds {max_bytes} bytes")
            result["data"] = json.loads(raw.decode("utf-8"))
            result["success"] = True
    except urllib.error.HTTPError as exc:
        result["status_code"] = exc.code
        result["error"] = {"kind": "http", "message": f"HTTP {exc.code}"}
    except urllib.error.URLError as exc:
        result["error"] = {"kind": "network", "message": str(getattr(exc, "reason", exc))}
    except TimeoutError as exc:
        result["error"] = {"kind": "timeout", "message": str(exc)}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        result["error"] = {"kind": "invalid_json", "message": str(exc)}
    except (OSError, ValueError) as exc:
        result["error"] = {"kind": "response", "message": str(exc)}
    finally:
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


def freshness_facts(value, prefix: str = "") -> list[tuple[str, object]]:
    """age_sec/age_days/as_of/sampled_at/partial/freshness 류 leaf 를 (경로, 값) 으로 수집. 값은 변형하지 않는다."""
    facts: list[tuple[str, object]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, (dict, list)):
                facts.extend(freshness_facts(child, path))
            elif str(key).endswith(FRESHNESS_SUFFIXES) or str(key) in FRESHNESS_KEYS:
                facts.append((path, child))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            facts.extend(freshness_facts(child, f"{prefix}[{index}]"))
    return facts


def payload_symbol(payload):
    """응답의 종목 코드. 공급자마다 모양이 다르다 — 둘 다 받는다.

    `/context` 는 객체({code,name,market}), `/flow`·`/momentum` 은 문자열을 준다.
    문자열만 비교하면 context 는 **항상** symbol_mismatch 로 잡혀 경고 채널이 무뎌진다
    (라이브에서 매 조회마다 오탐했다).
    """
    if not isinstance(payload, dict):
        return None
    sym = payload.get("symbol")
    if isinstance(sym, dict):
        return sym.get("code")
    return sym


def source_quality(name: str, payload, success: bool, symbol: str) -> dict:
    expected = EXPECTED_SCHEMAS.get(name)
    actual = payload.get("schema_version") if isinstance(payload, dict) else None
    schema_match = (actual == expected) if (expected is not None and payload is not None) else None
    expected_sym = symbol if name in EXPECTED_SCHEMAS else None
    actual_sym = payload_symbol(payload) if expected_sym else None
    symbol_match = (actual_sym == expected_sym) if (expected_sym and payload is not None) else None
    facts = freshness_facts(payload)
    warnings: list[str] = []
    if not success:
        warnings.append(f"{name}:fetch_failed")
    if expected is not None and payload is not None and schema_match is not True:
        warnings.append(f"{name}:schema_mismatch")
    if expected_sym and payload is not None and symbol_match is not True:
        warnings.append(f"{name}:symbol_mismatch")
    for path, val in facts:
        if path.endswith("freshness") and val in ("stale", "unavailable"):
            warnings.append(f"{name}:{path}={val}")
        elif val is None and path.endswith(FRESHNESS_SUFFIXES):
            warnings.append(f"{name}:{path}=missing")
    if name == "context" and success:
        warnings.append("context:quote_freshness_not_exposed")  # created_at 은 조립 시각 — quote 신선도가 아니다
    if name == "market_context" and success:
        # 직전 확정봉(bars=2 요청 시 bars[-2], 1개뿐이면 bars[-1])의 confidence 만 사실로 남긴다.
        # ok 아니면 그 봉의 호가·체결·프로그램 축은 신뢰 낮음(phenomena 는 low_confidence 하나) — 판단 없이 경고만.
        bars = payload.get("bars") if isinstance(payload, dict) else None
        confirmed = (bars[-2] if len(bars) >= 2 else bars[-1]) if isinstance(bars, list) and bars else None
        confirmed_confidence = confirmed.get("confidence") if isinstance(confirmed, dict) else None
        if confirmed_confidence != "ok":
            warnings.append(f"market_context:orderbook_confidence={confirmed_confidence}")
    return {"schema": {"expected": expected, "actual": actual, "match": schema_match},
            "symbol": {"expected": expected_sym, "actual": actual_sym, "match": symbol_match},
            "freshness_facts": [{"path": p, "value": v} for p, v in facts],
            "warnings": list(dict.fromkeys(warnings))}


def build_packet(symbol: str, *, base_url: str, token: str, bar_ts: str | None, now: str | None = None,
                 with_momentum: bool = False, fetch=fetch_json) -> dict:
    fetched_at = now or datetime.now().isoformat(timespec="seconds")
    plan = [("health", "/api/health"), ("context", f"/api/stocks/{symbol}/context"), ("flow", f"/api/stocks/{symbol}/flow"),
            ("market_context", f"/api/stocks/{symbol}/market-context?bars=2&market=none")]
    if with_momentum:
        plan.append(("momentum", f"/api/stocks/{symbol}/momentum"))
    sources: dict[str, dict] = {}
    for name, path in plan:
        res = fetch(base_url, path, token=token)
        quality = source_quality(name, res["data"], res["success"], symbol)
        sources[name] = {"path": path, "success": res["success"], "status_code": res["status_code"], "elapsed_ms": res["elapsed_ms"],
                         "error": res["error"], "data": res["data"], "quality": quality}
    freshness = {f"{name}.{fact['path']}": fact["value"] for name, src in sources.items() for fact in src["quality"]["freshness_facts"]}
    warnings = list(dict.fromkeys(w for src in sources.values() for w in src["quality"]["warnings"]))
    required_ok = all(sources.get(n, {}).get("success") for n in REQUIRED_SOURCES)
    schema_ok = all(src["quality"]["schema"]["match"] is not False for src in sources.values() if src["success"])
    return {"schema_version": PACKET_SCHEMA_VERSION, "packet_id": uuid4().hex[:12], "sym": symbol, "bar_ts": bar_ts,
            "fetched_at": fetched_at, "base_url": base_url, "sources": sources,
            "summary": {"required_ok": required_ok, "schema_ok": schema_ok, "freshness": freshness, "warnings": warnings,
                        "boundary": "measurement only — no judgement, score or recommendation; the session decides"}}


def save_packet(packet: dict, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    stamp = packet["fetched_at"].replace(":", "").replace("-", "")
    path = os.path.join(out_dir, f"{stamp}-{packet['sym']}.json")
    tmp = path + ".tmp"
    packet["path"] = path
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(packet, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def build_and_save(symbol: str, *, base_url: str, token: str, out_dir: str, bar_ts: str | None, now: str | None = None,
                   with_momentum: bool = False) -> tuple[dict, str, int]:
    packet = build_packet(symbol, base_url=base_url, token=token, bar_ts=bar_ts, now=now, with_momentum=with_momentum)
    path = save_packet(packet, out_dir)
    return packet, path, (0 if packet["summary"]["required_ok"] else 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="결정 패킷 조립 (GET 전용, 판단 0)")
    ap.add_argument("symbol")
    ap.add_argument("--bar-ts", default=None, help="다이제스트의 bar_ts(상관용, 서버로 보내지 않음)")
    ap.add_argument("--with-momentum", action="store_true")
    ap.add_argument("--output-dir", default="local/packets")
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--stdout", action="store_true", help="패킷 JSON 전체를 stdout 에도 출력")
    a = ap.parse_args(argv)
    if not (len(a.symbol) == 6 and a.symbol.isalnum() and a.symbol.upper() == a.symbol):
        print("ERROR: symbol 은 6자리 코드", file=sys.stderr); return 2
    try:
        with open(a.config, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"ERROR: config 읽기 실패: {exc}", file=sys.stderr); return 2
    try:
        gw = core.resolve_gw_config(cfg)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2
    packet, path, rc = build_and_save(a.symbol, base_url=gw.base_url, token=gw.api_key, out_dir=a.output_dir, bar_ts=a.bar_ts, with_momentum=a.with_momentum)
    if a.stdout:
        print(json.dumps(packet, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"packet_id": packet["packet_id"], "path": path, "required_ok": packet["summary"]["required_ok"],
                          "schema_ok": packet["summary"]["schema_ok"], "warnings": packet["summary"]["warnings"]}, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
