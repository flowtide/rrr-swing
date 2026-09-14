# CLAUDE.md — 진입점

세션 계약은 `AGENTS.md` 다. 이 파일은 그 계약을 가리키는 얇은 진입점이며, 런타임에 따라 다른 것만 적는다.

- 읽는 순서: `AGENTS.md` → `config/config.json` → `local/memory/state/<오늘>.md` → `local/memory/playbook/<최신>.md`·`local/stories/` → `docs/02-runbook.md`.
- 수신: 기본 소스는 gw SSE 스트림 — **인바운드 어댑터** `bin/inbound_rrr.py`(stdlib SSE 클라이언트, 배달만)가 gw `GET /api/events/stream` 을 구독 선언(`bin/subscribe.py`)의 `symbols` 쿼리로 열고, 계좌당 봉당 1건 `[digest …]` 로 herdr 세션(`rs-lead`, `--deliver herdr --target rs-lead`)에 주입한다. 커서는 `local/events.cursor`, 재시작 뒤 밀린 구간은 `bin/inbound_rrr.py --once`(`GET /api/events`). 운영자 채널이 telegram 이면 수신은 텔레그램 플러그인이 맡고(봇 토큰당 getUpdates 폴러 1개), 능동 보고는 `bin/tg_send.py` 가 보낸다.
- 보고 발송: `config.operator_channel` 이 console(기본)이면 `bin/report.py --text …`(stdout + `local/reports/`), telegram 이면 `bin/tg_send.py --text …`. 일반 출력(transcript)은 운영자에게 도달하지 않는다. 운영자 지시는 console 이면 세션 직접 입력, telegram 이면 `[운영자 DM] …`.
- 브로커 도구: MCP 는 **운영자가 user scope 에 등록한 것을 그대로 쓴다**(`claude mcp add`). 계좌·시세 조회는 gw MCP 도구(`kiwoom_account_*`·`kiwoom_market_*`·`kiwoom_info_*` 등), 주문은 `kiwoom_order_*` 로 내며 집행은 rs-exec 가 맡는다. `bin/order.py` 는 집행 결과의 원장 기록 및 사후 보고 생성(주문 제출 0). `_AL` 접미사 심볼은 조회 전용이며 주문 코드에 넣지 않는다.
- 공급자 조회: gw 경유 `/api/stocks/{sym}/context`·`/api/stocks/{sym}/flow` 등은 `X-API-Key` 헤더가 필요하다. 키는 `config.gw.api_key`(값, `.gitignore` 대상)를 곧바로 쓴다.
