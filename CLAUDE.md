# CLAUDE.md — 진입점

세션 계약은 `AGENTS.md` 다. 이 파일은 그 계약을 가리키는 얇은 진입점이며, 런타임에 따라 다른 것만 적는다.

- 읽는 순서: `AGENTS.md` → `config/config.json` → `local/memory/state/<오늘>.md` → `local/memory/playbook/<최신>.md`·`local/stories/` → `docs/02-runbook.md`.
- 수신: 기본 소스는 gw SSE 스트림 — **인바운드 어댑터** `bin/inbound_rrr.py`(stdlib SSE 클라이언트, 배달만)가 gw `GET /api/events/stream` 을 **쿼리 없이** 열어 전량을 받고, `local/watchlist.json` 의 종목만 남겨 계좌당 봉당 1건 `[digest …]` 로 herdr 세션(`rs-lead`, `--deliver herdr --target rs-lead`)에 주입한다. 목록은 **이벤트마다** 확인하므로 `bin/watchlist.py` 로 고치면 재기동 없이 다음 이벤트부터 듣는다. 목록이 정상이 아니면(파일 부재·손상·종목 코드가 아닌 항목) 같은 문제당 하루 1회 `[watchlist-problem …]` 으로 알린다 — 그 동안 종목 이벤트가 전부 버려지기 때문이다. 커서는 없다 — 언제나 현시점부터 받는다. 지나간 봉을 뒤늦게 받으면 실시간 판단에 쓸모가 없기 때문이다. 운영자 채널이 telegram 이면 수신은 텔레그램 플러그인이 맡고(봇 토큰당 getUpdates 폴러 1개), 능동 보고는 `bin/tg_send.py` 가 보낸다.
- 보고 발송: `config.operator_channel` 이 console(기본)이면 `bin/report.py --text …`(stdout + `local/reports/`), telegram 이면 `bin/tg_send.py --text …`. 일반 출력(transcript)은 운영자에게 도달하지 않는다. 운영자 지시는 console 이면 세션 직접 입력, telegram 이면 `[운영자 DM] …`.
- 브로커 도구: MCP 는 **운영자가 user scope 에 등록한 것을 그대로 쓴다**(`claude mcp add`). 계좌·시세 조회는 gw MCP 도구(`kiwoom_account_*`·`kiwoom_market_*`·`kiwoom_info_*` 등), 주문은 `kiwoom_order_*` 로 내며 집행은 rs-exec 가 맡는다. `bin/order.py` 는 집행 결과의 원장 기록 및 사후 보고 생성(주문 제출 0). `_AL` 접미사 심볼은 조회 전용이며 주문 코드에 넣지 않는다.
- 공급자 조회: gw 경유 `/api/stocks/{sym}/context`·`/api/stocks/{sym}/flow` 등은 `X-API-Key` 헤더가 필요하다. 키는 `config.gw.api_key`(값, `.gitignore` 대상)를 곧바로 쓴다.
