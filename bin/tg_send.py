#!/usr/bin/env python3
"""rrr-swing 운영자 보고 발송 — Telegram Bot API sendMessage (stdlib). 플러그인 reply 도구 대체.

  bin/tg_send.py --text "보고"            # 기본 대상 = config.operator_chat_id
  echo "보고" | bin/tg_send.py --chat 111
종료 코드: 성공 0, 전송 실패 1, 설정/인자 오류 2. 토큰은 출력하지 않는다.
"""
from __future__ import annotations

import json
import os
import sys


def resolve_text(arg_text: str | None, stdin_text: str | None = None) -> str:
    if arg_text is not None:
        return arg_text
    if stdin_text is None:
        stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()
    return stdin_text.strip()


def _post_json(url: str, payload: dict) -> dict:
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def send(token: str, chat_id: str, text: str, post=None) -> int:
    if not token or not chat_id or not text:
        return 2
    post = post or _post_json
    try:
        res = post(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": str(chat_id), "text": text})
    except Exception as exc:  # 네트워크·API 오류 — 토큰 미출력
        print(f"ERROR: sendMessage 실패 ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0 if res.get("ok") else 1


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="운영자 보고 발송 (Bot API)")
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--chat", "-c", default=None, help="대상 chat id (기본 operator_chat_id)")
    ap.add_argument("--text", "-t", default=None, help="보낼 메시지. 생략하면 stdin")
    a = ap.parse_args(argv)
    cfg = {}
    if os.path.exists(a.config):
        with open(a.config, encoding="utf-8") as f:
            cfg = json.load(f)
    token = str(cfg.get("telegram_bot_token", ""))
    if token.startswith("<"):
        token = ""
    chat = a.chat or str(cfg.get("operator_chat_id", ""))
    if chat.startswith("<"):
        chat = ""
    rc = send(token, chat, resolve_text(a.text))
    if rc == 2:
        print("ERROR: token/chat/text 중 비어 있음", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
