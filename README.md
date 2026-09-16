# rrr-swing

키움 실계좌 하나를 맡은 **스윙 트레이더(LLM 세션)** 의 작업 공간. 시장의 흐름과 종목의 이야기를 알고, 자기 판단으로 베팅하고, 결과를 기록하고, 배운다. 공급자(rrr)의 이벤트를 `GET /events/stream`(SSE)으로 전량 받아 자기 감시 목록의 종목만 봉당 다이제스트로 걸러 보고, 증거를 읽어 판단하며, 주문은 MCP 도구로 내고 `bin/order.py` 는 집행 결과를 기록하고 사후 보고를 생성한다. 코드는 매매를 결정하지 않는다 — 이벤트 전달·장부 기록·사후 보고만 코드다(급정지는 gw API 키 비활성화). 운영자 채널은 콘솔 또는 텔레그램 플러그인이다.

- **개요(목적·개념, 다이어그램)**: `docs/01-overview.md` — 상위 문서. 처음 읽는 사람은 여기부터.
- 트레이더 계약: `AGENTS.md`. 진입점: `CLAUDE.md`. 역할은 `rs-lead`·`rs-exec`(AGENTS.md §8) — `rs-lead` 는 claude 고정이고 `rs-exec` 만 `--runtime claude|agy` 를 고른다.
- 운영: `docs/02-runbook.md`(장전·장중·마감 후·주간·운전 모드·예외). 참고: `docs/03-evidence_guide.md`(증거 필드·신뢰도·결측), `docs/04-price_source.md`(현재가 폴백), `docs/05-context.md`(기억이 세션에 들어가는 두 경로), `docs/06-local.md`(`local/` 의 구역과 손대는 규칙).
- 경로 표기: `rrr-swing` 작업 디렉터리 기준 상대경로.

## 실행

준비는 파일 하나다. 셸 환경을 따로 만들지 않는다.

```bash
# 1. 설정 — <YOUR_*> 를 채운다. 이 파일은 .gitignore 대상이라 커밋되지 않는다.
cp config/config.example.json config/config.json && chmod 600 config/config.json
#    필수: gw.base_url · gw.api_key(키 값) · account_id
#    선택: operator_channel=telegram 이면 telegram_bot_token · operator_chat_id

# 2. local/ 골격 — 최초 1회, 멱등
bin/init_local.sh

# 3. 역할 이름은 미리 붙이지 않는다 — start.sh 가 런타임이 뜬 뒤 자기 pane 에 붙인다.
#    pane 은 **포커스가 아니라 실행 위치**로 정해진다. 판별이 안 되면 기동을 거부하며,
#    그때만 직접 지정한다: RS_PANE_ID=w3:p1 bin/start.sh --role lead

# 4. 기동 전 점검 (기동 0)
bin/start.sh --role lead --check-only

# 5. lead agent 기동 — rs-lead 로 쓸 herdr pane **안에서** 실행한다(이 셸이 런타임으로 교체된다)
bin/start.sh --role lead
# tail -f ./local/inbound.log 동작 확인

```

기동 뒤 세션이 **감시 목록**을 적어야 종목 이벤트가 온다. 목록이 비면 종목 이벤트는 배달되지 않는다(종목 없는 macro 는 항상 온다). 어댑터가 이벤트마다 목록을 확인하므로 고치면 바로 듣는다.

```bash
python3 bin/watchlist.py set 005930 000660 --append-ledger
python3 bin/watchlist.py add 006800 --note "보유 120주"     # 나머지는 그대로
python3 bin/watchlist.py drop 000660                        # 나머지는 그대로
python3 bin/watchlist.py show
```

그 밖:

```bash
bin/start.sh --role exec                           # rs-exec(집행 역할) — 인바운드 어댑터를 띄우지 않는다
bin/start.sh --role exec --runtime agy             # rs-exec 를 agy 로 (전제: agy 에 kiwoom-gw MCP·스킬. docs/02-runbook.md §1-6)
bin/start.sh --adapter-only                        # 인바운드 어댑터만, --deliver file 로 1거래일 관측
python3 bin/inbound_rrr.py --probe 0               # 연결 테스트 — 스트림을 열어 받은 것만 보여 주고 끝(배달 0)
```

| 명령 | 용도 |
|---|---|
| `bin/start.sh [--role lead|exec] [--runtime claude|agy] [--adapter-only]` | config 검증(비밀 미출력, `gw.base_url`·`gw.api_key` 존재만 확인) → selftest → `/api/health` 1회 → 내 uid 의 이전 인바운드 어댑터 전부 정리 → 인바운드 어댑터 백그라운드(`local/inbound.pid`, lead 만) → 런타임 기동. `operator_channel=telegram` 이면 텔레그램 플러그인 채널 주입 |
| `python3 bin/inbound_rrr.py --deliver stdout|file|herdr [--target …] [--watchlist …] [--redeliver]` | **인바운드 어댑터**: 쿼리 없는 SSE 로 전량 수신 → `EventEntry`→태그 정규화 → **이벤트마다** `local/watchlist.json` 대조 → 계좌당 봉당 다이제스트 → 배달. 장중 창 안 30분마다 `[market-check ts=HH:MM]` wake(시각뿐). 커서 없음 — 언제나 현시점부터(지나간 봉은 실시간 판단에 쓸모가 없다). 재접속은 로그에 `gap=not_replayed`, 재접속 backoff 1→60s, 하트비트 타임아웃, 401 종료·503 backoff |
| `python3 bin/inbound_rrr.py --probe <0\|stream id>` | **연결 테스트**: 그 지점부터 스트림을 열어 받은 것을 보여 주고 끝난다. 배달 0 — 감시 목록도 배달 로그도 열지 않는다. `since` 를 받는 유일한 자리다(운전용 `build_stream_url` 에는 그 인자가 없다). 받은 게 0건이면 종료 코드도 0이 아니다 |
| `python3 bin/watchlist.py set|add|drop|show|clear` | 감시 목록(세션이 실행). `add`·`drop` 은 나머지 종목과 메모를 보존한다. 쓰는 명령은 `--append-ledger` 로 원장에 남긴다(서브명령 뒤에 붙인다) |
| `python3 bin/decision_packet.py <sym> --bar-ts <ts> [--with-momentum] [--stdout]` | 게이트웨이 `/api/stocks/{sym}/context`·`/api/stocks/{sym}/flow`(·`/api/stocks/{sym}/momentum`) GET 전용 조회(X-API-Key 인증), 원문을 `local/packets/` 에 보존 |
| `python3 scripts/ledger.py append <evt> --json '…'` / `tail -n N` / `validate` | 원장 기록·조회·검증(계좌별 append-only JSONL) |
| `python3 scripts/rt_calc.py tick|floor|qty|avg|pnl|r …` | 주문·평가 산술(세션은 암산하지 않는다) |
| `python3 bin/order.py --order-ref <dup_key> [--json]` | 주문 집행 결과의 판단 원장 기록 및 사후 보고 생성 (주문 제출 0) |
| `python3 bin/consult.py propose|pre|agree|decline|status|list …` | 손절·청산 협의 기록(`local/consults/` + 원장 `consult`). 실시간 `propose` 는 응답 기한(기본 30분), `status` 가 기한 경과를 timeout 으로 1회 기록. 집행 판단은 세션(주문 0) |
| `python3 scripts/marks.py --write|--report|--freeze …`, `scripts/flags.py`, `scripts/review.py --start … --end … [--write]` | 회계(운영자 크론): 전 포지션 마크·동결북, 만료·재검토 flag, 주간 review |
| `python3 bin/report.py --text "…"` / `python3 bin/tg_send.py --text "…"` | 운영자 보고(console: stdout + `local/reports/<date>.md` / telegram: Bot API) |
| `bin/init_local.sh [<dir>]` | `local/` 골격 — 원본 `local/memory/{playbook,state,journal}`, 기동 산출물 `local/system-prompts/`, 그 밖 `stories·meetings·lessons·consults·packets·reports` + 첫 원본(state·playbook) 깔기. 멱등 |
| `python3 bin/context_load.py [--local …] [--out …] [--days N] [--budget N] [--file-cap N]` | 기동 컨텍스트: `local/memory/` 세 원본에서 운영자 지시 → 규칙 → 상태 → 최근 N개 일지를 고정 예산 안에(초과분 `[truncated]`, 빠진 절은 `[예산 소진 …]`). 담는 순서가 우선순위다. `bin/start.sh` 가 `--append-system-prompt-file` 로 넣는다 |
| `python3 bin/turn_context.py [--local …] [--cap N]` | 매 턴 컨텍스트(mode·운영자 지시·태도·열린 협의, 상한 1,000자). `UserPromptSubmit` 훅이 부른다 |
| `uv run --no-project --with pytest pytest -q` | 전체 테스트 스위트 실행(네트워크 0) |

**인바운드 어댑터**(`bin/inbound_rrr.py` — pid `local/inbound.pid`, 로그 `local/inbound.log`)는 배달만 한다. 판단·주문·`/context` 호출은 세션(LLM)의 일이며 어댑터 코드에 없다. 서버에는 아무 필터도 보내지 않는다 — 무엇을 볼지는 로컬 감시 목록만 정한다.

## 성과를 읽는 법

완결 거래 통계를 쓰지 않는다. 성과의 단일 소스는 회계가 매 거래일 마감 후 남기는 **전 포지션 마크**이고 기준선은 **동결북**이다. 성적표는 코드가 매기고 트레이더는 읽는다. 운전 모드(dry_run·confirm)의 전환은 성과가 아니라 운영자의 주간 미팅 결정이다(`docs/02-runbook.md` §5).
