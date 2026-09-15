# 운영 런북 — 하루와 한 주

값(한도·손실 정책·진입 기준)은 `local/memory/playbook/<최신>.md`(트레이더)가, 모드·만료 기본값·토큰 상한·회계 시각은 `config/config.json`(운영자)이 소유한다. 급정지는 게이트웨이 API 키 비활성화로 집행한다. 모든 명령은 이 작업 디렉터리에서 실행한다. 회계 도구(`scripts/marks.py`·`flags.py`·`review.py`)는 세션이 아니라 **운영자의 크론**이 돌린다(설치는 §3, 코드가 설치하지 않는다). 세션이 쓰는 원장 이벤트는 `scripts/ledger.py append` 로만 기록한다(역할 제한: `fill`·`cancel` 은 `--role safety`, `mark`·`flag`·`review` 는 `--role accounting`).

## 1. 장전

1. **기억을 읽는다** — 규칙·상태·최근 일지는 기동 시 시스템 프롬프트로 이미 실려 있다(`docs/05-context.md`). 더 볼 것은 활성 유니버스(보유 ∪ 활성 가설) 종목의 스토리다. 읽는 양은 고정한다 — `memory.load_budget_chars` 안에서 담고 초과분은 `[truncated]`, 통째로 빠진 절은 `[예산 소진 …]` 으로 알린다.
2. **회계를 읽는다**(쓰지 않는다).
   ```bash
   python3 scripts/marks.py --report                       # 마크 수·최신 NAV·동결북 대비·마크 누락·halted
   python3 scripts/flags.py --dry-run                      # 오늘 기준 expired·recheck_due (기록 없음)
   python3 scripts/ledger.py tail -n 200 | grep '"evt": "flag"'          # 회계가 남긴 flag — 처분 결정은 §3-4
   python3 scripts/ledger.py tail -n 200 | grep '"evt": "order"'         # 주문 기록
   ```
3. **시장을 선언한다** — 지수·업종·해외(rrr macro)와 운영자 자료를 보고 오늘의 장세·태도(하락에서 살지, 추격할지)를 STATE 에 한 줄로 적는다. **활성 유니버스**를 정리한다(보유는 계좌가 정하고, 관심은 여기서 고른다). 그 스토리는 `local/stories/<symbol>.md` — front matter(YAML 부분집합: `key: value`, `{lo: .., hi: ..}`, `- 항목` 목록)에 구조화 필드, 본문에 서사:
   ```yaml
   ---
   symbol: 336260
   plan_id: p-336260-1      # 원장 상관 키(plan_created·coverage 가 참조)
   thesis: <한 줄>
   entry_zone: {lo: 46000, hi: 47500}
   target: 52000
   invalidation_conditions:
     - "46000 종가 이탈"
     - 논지 훼손(서사)
   horizon_d: 10            # 거래일. 비우면 config.position_expiry_days
   recheck_by: 2026-06-24   # 스토리에 직접 적는다
   conviction: 중간
   status: active           # draft | active | triggered | invalidated | expired | cancelled
   activated_at: 2026-06-17T21:00:00
   as_of: 2026-06-17
   ---
   ```
   가설을 활성화하면 원장에도 남긴다(계획은 파일, 사실은 원장):
   ```bash
   python3 scripts/ledger.py append plan_created --json '{"plan_id":"p-336260-1","sym":"336260","account_id":"<account_id>","entry_zone":{"lo":46000,"hi":47500},"allocation_pct":10,"horizon_d":10,"conviction":"중간","invalidation_conditions":["46000 종가 이탈"],"recheck_by":"2026-06-24","story_ref":"stories/336260.md"}'
   python3 scripts/ledger.py append plan_activated --json '{"plan_id":"p-336260-1"}'
   ```
4. **사전 협의** — 오늘 손절이 예상되는 보유 종목은 수준·수량·이유를 `bin/consult.py pre` 로 남기고 운영자 채널에 알린다(AGENTS.md §4). 처음부터 agreed 이며, 합의한 수준에 도달하면 세션이 바로 집행·보고한다(§2-4). 운영자는 `decline` 으로 철회할 수 있다.
   ```bash
   python3 bin/consult.py pre --sym 336260 --level 46000 --qty 100 --reason "무효화 조건 46000 종가 이탈" --report   # → local/consults/<id>.json + 원장 consult(agreed, pre=true) + 보고 1줄
   python3 bin/consult.py list                                                                                        # 열린 협의(pending·agreed), 기한 경과는 overdue 표시
   ```
5. **감시 목록을 적는다** — 활성 유니버스의 종목. 판단을 담지 않는 기계적 표현이다. 목록에 없는 종목은 배달되지 않는다(종목 없는 macro 는 항상 온다). 유형·세션·만료로는 거르지 않는다.
   ```bash
   python3 bin/watchlist.py set 336260 005930 --append-ledger
   python3 bin/watchlist.py add 006800 --note "보유 120주"   # 나머지 종목과 메모를 보존한다
   python3 bin/watchlist.py drop 005930                       # 나머지를 보존한다
   python3 bin/watchlist.py show
   ```
   판정(전체 그림은 `docs/01-overview.md` §3):
   ```
   이벤트 도착 ─▶ ① watchlist.json mtime 확인(바뀌었으면 재읽기)
                  └▶ ② sym 이 목록에 있나?
                        ├ 있다     → 봉 단위로 합쳐 [digest …] 배달
                        ├ 없다     → 버림 (local/delivery.jsonl 에 unwatched)
                        └ sym 없음 → 배달 (macro = 시장 전체)
   ```
   ①이 매 이벤트마다 돈다 — 목록을 고치면 다음 이벤트부터 반영되고 재기동이 필요 없다.
6. **기동**(`bin/start.sh --role lead`, Herdr 환경): config 검증(gw 는 `gw.base_url`·`gw.api_key` 존재만, 값 미출력, `mode`) → selftest(`inbound_rrr.py --selftest`, `watchlist.py show`, `ledger.py selftest`, `rt_calc.py selftest`, `marks.py --selftest`, `flags.py --selftest`, `review.py --selftest`) → gw `/api/health` 1회 → 같은 저장소의 이전 인바운드 어댑터 종료(lsof cwd) → 인바운드 어댑터를 `--deliver herdr --target rs-lead` 로 백그라운드 기동(`local/inbound.pid`, 로그 `local/inbound.log`) → 기억 머지(`bin/context_load.py` → `local/system-prompts/<오늘>.md`) → 런타임 기동(부트 프롬프트 = AGENTS.md 적용 + `mode` + 기동 순서(AGENTS.md §10) + 도구 목록 + 오늘 원본 경로. 기억은 `--append-system-prompt-file` 로 들어간다). 기본 소스 rrr_stream(gw SSE)은 봇 토큰이 필요 없다. 최초 1회는 `bin/init_local.sh` 로 `local/` 골격과 첫 원본(`docs/templates/`)을 만든다(멱등). `local/` 에 무엇이 있고 무엇을 고쳐도 되는지는 `docs/06-local.md`.
   ```bash
   bin/init_local.sh                        # 최초 1회(멱등): 원본 local/memory/{playbook,state,journal} + 산출물 local/system-prompts/ + local/{stories,meetings,lessons,consults,packets,reports} + 첫 원본
   bin/start.sh --role lead --check-only    # config·자격증명 점검(기동 0)
   bin/start.sh --role lead                 # mode 는 config.mode. 런타임 env: RS_ROLE=lead
   bin/start.sh --role exec                 # rs-exec(집행). 런타임 env: RS_ROLE=exec RS_RUNTIME=claude
   bin/start.sh --role exec --runtime agy   # rs-exec 를 agy 로. 전제조건은 바로 아래
   ```
   **런타임**(`--runtime claude|agy`, 기본 claude): `rs-lead` 는 claude 고정이다 — 기억이 `--append-system-prompt-file` 로 들어가는데 agy 에 대응 플래그가 없어 `bin/start.sh` 가 `--runtime agy --role lead` 를 거부한다(`docs/05-context.md`). `rs-exec` 만 런타임을 고른다. agy 로 띄우기 전에 **agy 쪽에** 두 가지가 있어야 하며, 둘 다 없어도 세션은 정상 기동한 얼굴로 뜨고 주문만 되지 않는다:
   - **kiwoom-gw MCP 등록** — `~/.gemini/config/mcp_config.json` 의 `mcpServers.kiwoom-gw`. 없으면 `kiwoom_order_*` 가 목록에 없다.
   - **kiwoom-gw 스킬** — `~/.gemini/config/skills/kiwoom-gw`. exec 부트 프롬프트가 이 스킬의 절차(3소스 대사)를 지시하는데, 없으면 절차 없이 주문한다.

   기동 직후 `kiwoom_list_tools` 로 도구 목록을 확인한다. 주문은 gw MCP 주문 도구로 내고, `bin/order.py` 는 집행 결과 기록 및 사후 보고를 담당한다.
7. 세션 기동 직후: 브로커 전제 확인 → 계좌·원장 대사(`python3 scripts/ledger.py validate`, `python3 scripts/ledger.py tail -n 20`, `python3 scripts/marks.py --report`) → 상태 확인(시스템 프롬프트) → **감시 목록을 오늘의 활성 유니버스와 대조**(`bin/watchlist.py show`; 어긋나면 §1-5 로 고친다 — 어제 목록을 물려받고도 조용한 장으로 읽는 일이 실제로 있었다) → 운영자 보고(console: `python3 bin/report.py --text "…"`, telegram: `bin/tg_send.py`).
8. 전달: **인바운드 어댑터**(`bin/inbound_rrr.py`)가 SSE 를 **쿼리 없이** 열어 전량을 받고, 이벤트마다 `local/watchlist.json` 을 확인해 감시 종목만 남긴다. 서버에는 아무 상태도 두지 않는다 — 쿼리는 접속 시점에 굳어, 목록을 고쳐도 듣지 않게 만든다.
9. 사전 조건(사람, 1회): gw 가 `/api/events/stream`(SSE)·`/api/events` 를 제공하도록 기동돼 있을 것, API 키를 환경변수로 준비. Telegram 운영자 채널을 쓸 때만 봇 생성·`operator_chat_id` 확인이 필요하다. 테스트 실행: `uv run --no-project --with pytest pytest -q`.

## 2. 장중 — 다이제스트·30분 체크·주문·협의

1. **다이제스트 도착**(계좌당 봉당 1건, 소스 무관 같은 형식). 형식: `[digest account=<id> bar_ts=<ts> n=<raw_event_count>] <태그 줄> | <태그 줄> …`(한 줄, 상한 초과 시 `part=k/n` 분할). rrr 스트림 엔트리는 계약 키 순서의 태그 줄로 되살려 넣는다(Telegram 태그와 동일). 운영자 지시는 console 채널이면 pane 직접 입력, telegram 이면 `[운영자 DM] <본문>`. 어댑터는 같은 5분봉의 감시 종목 이벤트를 `quiet_sec`(config) 또는 새 봉 도착까지 모아 1건으로 합치고, 중복 키 (account_id, sym, evt, bar_ts) 는 버린다. 감시 밖·태그 깨짐·(Telegram) 잘못된 발신자는 배달하지 않고 `local/delivery.jsonl` 에 `source`·reason 으로만 남긴다(unwatched | bad_sender | bad_tag | other_chat | duplicate | already_delivered | operator_only | delivery_queue_overflow). 목록 자체가 정상이 아니면(파일 부재·손상·종목 코드가 아닌 항목) 같은 문제당 하루 1회 `[watchlist-problem …]` 을 배달하고 `kind=watchlist_problem` 으로 남긴다. 커서는 없다 — 스트림이 끊기면 backoff(1→60s)로 **쿼리 없이** 재접속해 **언제나 현시점부터** 받는다. 지나간 봉을 뒤늦게 받으면 판단에 쓸모가 없고 현재로 착각할 위험만 남기 때문이다. 재접속은 로그에 `gap=not_replayed` 로 남는다. 하트비트가 `heartbeat_timeout_sec` 동안 없으면 끊고 재접속한다.
2. **decision packet pull** — 태그 줄의 `sym`·`bar_ts` 로 종목당 1회. `/health` 1회 + `/context`(lean) + `/flow`(partial 포함) 필수, `--with-momentum` 선택. GET 전용, 원문 그대로 `local/packets/<ts>-<sym>.json` 에 저장하고 `summary.warnings`(schema_mismatch·fetch_failed·stale·missing) 만 요약한다. exit 1 = 필수 소스 실패(패킷은 남음 → 해당 항목은 결측으로).
   ```bash
   python3 bin/decision_packet.py 336260 --bar-ts 2026-06-25T10:35:00            # → {"packet_id","path","required_ok","schema_ok","warnings"}
   python3 bin/decision_packet.py 336260 --bar-ts 2026-06-25T10:35:00 --with-momentum --stdout | head -c 4000
   ```
3. **판단 → `decision`** — 증거(`docs/03-evidence_guide.md` 는 필드의 뜻과 신뢰도만 적는다)를 스토리·플레이북·계좌 상태와 함께 읽고 `action` 하나를 고른다. `decision{action, px, qty, rationale, event_label, checklist}` — `checklist` 는 원장 스키마의 근거 요약 객체(키 = `03-evidence_guide.md` 의 CK 라벨, 값 = 한 줄 요약 또는 NA). hold·no_action 도 기록한다. `packet_ref` 에 패킷 경로, `bar_ts`, 선택 `token_usage{input, output}` 를 함께 남기면 review 가 토큰 일합을 센다. 산술은 `rt_calc` 로만(호가 단위·바닥가·수량·평단·손익·R).
   ```bash
   python3 scripts/rt_calc.py tick --px 46950 --side buy               # 호가 단위 정규화
   python3 scripts/rt_calc.py qty --nav 50000000 --alloc-pct 10 --px 47000 --cash 20000000
   python3 scripts/ledger.py append decision --json '{"decision_id":"d-20260625-1035-336260","trigger":"event","event_label":"support_return","sym":"336260","bar_ts":"2026-06-25T10:35:00","packet_ref":"local/packets/20260625T103605-336260.json","checklist":{"CK-1":"support_return lvl=46800 str=2 확정봉","CK-2":"프로그램 누적 +12억, 최근 3봉 유입","CK-3":"big 순매수 strength 상승","CK-4":"NA(stale)","CK-5":"종목 기인(kospi -0.2%)","CK-6":"스토리 p-336260-1 active, entry_zone 안","CK-7":"현금 가용, 플레이북 비중 안"},"action":"enter","px":"47000","qty":100,"rationale":"…","token_usage":{"input":12000,"output":800}}'
   ```
4. **주문 흐름**(집행 지시는 rs-lead → rs-exec 한 방향):
   `decision` 수립 → rs-exec 가 gw MCP `kiwoom_order_*` 도구로 집행 → 체결/미체결 대사 → `bin/order.py --order-ref <dup_key>` 로 원장 `order` 기록 및 사후 보고 1줄 생성.
   ```bash
   python3 scripts/ledger.py append order --json '{"decision_ref":"d-20260625-1035-336260","sym":"336260","side":"buy","px":"47000","qty":100,"exchange":"SOR","ord_type":"limit","digest_id":"<account>|2026-06-25T10:35:00","dup_key":"o|<account>|336260|2026-06-25T10:35:00"}'
   python3 bin/order.py --order-ref 'o|<account>|336260|2026-06-25T10:35:00' --json     # 원장 order 기록 및 사후 보고 생성 (주문 제출 0)
   ```
   - dry_run: 판단·결정·원장 기록 전부, 주문 제출 0.
   - confirm: 결정 → MCP 주문 집행 → 원장 기록.
   - 주문은 지정가 + SOR 고정(`dmst_stex_tp="SOR"`). 시장가는 쓰지 않으며 규칙에 의한 취소가 없으면 20:00 장 종료 시 자동 소멸한다.
   - 급정지는 게이트웨이 API 키 비활성화로 집행하며, 해제는 운영자만 수행한다.
5. **30분 시장 체크** — 인바운드 어댑터가 창(`inbound.market_check`, 기본 09:00~15:30 매 30분) 안에서 `[market-check ts=HH:MM]` 한 줄을 pane 에 넣는다(내용은 시각뿐, 다이제스트와 독립, 두 소스 공통, `local/delivery.jsonl` kind=market_check). 세션은 그때 시장 전체(지수·업종·해외·breadth)를 본다. 장세 판단이 바뀌면 STATE 의 장세·태도 줄을 갱신하고 보유 종목의 커버리지를 다시 본다. 창·주기는 운영자가 config 로 정하고, 그 안에서 더 자주 볼지는 세션이 정한다.
6. **손절 실시간 협의** — 사전 협의가 없는 손절은 `bin/consult.py propose` 로 제안(청산·축소·보류, 가격, 수량, 이유)을 남기고 운영자 채널에 보낸 뒤 기다린다(응답 기한 기본 30분 = `respond_by`). 운영자는 콘솔에서 `agree`/`decline`. 세션은 `status <id>` 로 확인한다 — pending 이고 기한이 지났으면 timeout 이 1회 기록된다. agreed·timeout 이면 제안대로 집행(§2-4, `decision` 에 `consult_ref`)하고 보고하며, declined 면 보유를 유지하고 다시 판단한다. 집행 판단은 세션의 것이고 consult.py 는 주문을 만들지 않는다.
   ```bash
   python3 bin/consult.py propose --sym 336260 --action exit --px 45900 --qty 100 --reason "지지 이탈, 논지 훼손" --report   # → id, respond_by = 제안 시각 + 30분, 보고 1줄
   python3 bin/consult.py agree <id> --note "ok"        # 운영자(콘솔). 거절은 decline <id>
   python3 bin/consult.py status <id>                   # pending | agreed | declined | timeout(기한 경과 시 1회 기록, 재호출 중복 0)
   ```
7. 체결은 주문 집행 후 대사하며 `fill` 을 남긴다. 주문 거래소는 SOR 고정(`dmst_stex_tp="SOR"`)이며 장 종료(20:00) 시 자동 소멸하므로 마감 전 취소 작업을 두지 않는다.
8. 보유 종목의 wake 에서는 반전 읽기(`03-evidence_guide.md` CK-S)로 `coverage` 를 갱신한다(§3-4 명령).
9. 장중에 발견한 종목은 STATE 관심 목록에 올린다. 스토리에 계획한 가격 범주가 없으면 사지 않는다.

## 3. 장 마감 후 — 일지·회계 (회계 = 운영자의 크론, 세션은 읽기만)

1. **주문 만료**: 거래소는 SOR 고정(`dmst_stex_tp="SOR"`)이며, 규칙에 의한 취소가 없으면 20:00 장 종료와 함께 자동 소멸한다. 따라서 마감 전 취소 작업을 두지 않는다.
2. **일지** — `local/memory/journal/<date>.md`: 오늘 무엇을 보고, 무엇을 했고, 무엇이 어긋났나. STATE·스토리의 정리(갱신)는 이때 한다 — 장중에는 쓰기만.
3. 회계(운영자 크론): 전 포지션 종가 마크(`mark`) → 기간 만료·재검토일 도달 `flag`. 마크 누락은 리포트 결함(review `mark_missing`). 브로커 계좌 평가 응답은 **주입**한다(코드가 조회하지 않는다) — MCP/SDK 로 받은 JSON 을 파일로 두고 넘긴다. 종가 소스는 `accounting.close_source`(기본 supplier_daily_candle: `--closes-json {sym: close}` 로 공급자 일봉 종가 주입, 없으면 브로커 현재가 폴백·출처 기록). 거래정지·휴장은 직전 종가 carry + `halted=true`.
   ```bash
   # 마크(같은 거래일 재실행은 exit 3 = skip, 소급은 --date, 강제 추가는 --force)
   python3 scripts/marks.py --write --from-json local/broker/evaluation-2026-06-25.json --closes-json local/broker/closes-2026-06-25.json --date 2026-06-25
   python3 scripts/flags.py --date 2026-06-25                     # expired·recheck_due → 원장 flag(accounting), 1회/일 멱등
   python3 scripts/marks.py --report                              # 누락 셀·halted 확인
   python3 scripts/marks.py --freeze --date 2026-06-25            # 동결북 = 복기 기준선. 운영자가 정한 시점(운전 모드 전환 등)에 1회
   ```
   크론 행(사람이 `crontab -e` 로 설치, 코드는 설치하지 않는다; 평가 응답 파일 준비는 사람 또는 별도 fetch 절차):
   ```cron
   # rrr-swing 회계 — 평일 20:30 KST 마크 → 20:35 플래그 (naive KST 머신 기준)
   30 20 * * 1-5  cd /path/to/rrr-swing && python3 scripts/marks.py --write --from-json local/broker/evaluation-$(date +\%F).json --closes-json local/broker/closes-$(date +\%F).json >> local/accounting.log 2>&1
   35 20 * * 1-5  cd /path/to/rrr-swing && python3 scripts/flags.py >> local/accounting.log 2>&1
   ```
4. **flag 처분과 커버리지** — 회계가 남긴 `flag(expired·recheck_due)` 는 다음 장전 전까지 청산(`decision(trigger=flag)` → `exit`) 또는 재언더라이팅(스토리 갱신 + 새 `plan_created`, rollover 는 review 가 센다)을 결정한다. 미결정은 review 의 `overdue_decision`. 자동 청산은 없다. 보유 종목 커버리지는 `coverage` 로 갱신한다.
   ```bash
   python3 scripts/ledger.py append coverage --json '{"sym":"336260","plan_id":"p-336260-1","status":"valid","basis":"CK-S 요약","recheck_by":"2026-07-01","as_of":"2026-06-24","evidence":"full"}'
   python3 scripts/ledger.py append decision --json '{"decision_id":"d-20260624-1","trigger":"flag","event_label":"none","sym":"336260","checklist":{"CK-1":"NA","CK-2":"…","CK-3":"…","CK-4":"…","CK-5":"…","CK-6":"…","CK-7":"…"},"action":"exit","px":"47000","qty":10,"rationale":"…"}'
   ```

## 4. 주간 — 복기·미팅 (회계 = 운영자의 크론 또는 수동 1회)

1. 회계 `review` — 마크 vs 동결북, 이벤트 유형×action×mark20 전환율(빈 셀 NA), safety reject 분포(0건이면 NA), wake·토큰(일합·p50·p95·최대), rule_change 목록·unregistered_pivots, rollover, overdue_decision·overdue_escalation.
   ```bash
   python3 scripts/review.py --start 2026-06-22 --end 2026-06-26                # 출력만
   python3 scripts/review.py --start 2026-06-22 --end 2026-06-26 --write        # 원장 review 이벤트 1건(기간당 1회 멱등)
   ```
   ```cron
   0 21 * * 6  cd /path/to/rrr-swing && python3 scripts/review.py --start $(date -v-6d +\%F) --end $(date +\%F) --write >> local/accounting.log 2>&1
   ```
2. **자기 복기** — review 를 읽고 무엇이 맞고 틀렸는지 적는다. 스토리(깨진 가설은 상태만 바꾸고 보존)·플레이북(바꾼 이력 포함)을 갱신하고 교훈은 `local/lessons/` 에 남긴다. 성적표는 매기지 않는다.
3. **운영자 미팅** — `local/meetings/<date>.md`. 전략 변경(한도·손실 정책·진입/청산 기준·주의 배분)은 여기서 합의하고, 평가에 영향을 주는 변경은 `rule_change` 를 먼저 등록한다(미등록 변경은 review 의 `unregistered_pivots`). 운전 모드(§5)도 여기서 정한다.
   ```bash
   python3 scripts/ledger.py append rule_change --json '{"rule_id":"rc-2026-06-24-1","hypothesis":"…","metric":"…","n_required":30,"until":"2026-07-31","reject_condition":"…","registered_at":"2026-06-24T22:00:00","applies_from":"2026-06-25"}'
   ```

## 5. 운전 모드 전환 (운영자)

모드가 무엇을 뜻하는지는 `AGENTS.md` §9 에 있다. 여기서는 **바꾸는 절차**만 적는다.

절차: 주간 미팅에서 결정 → `config/config.json` 의 `mode` 를 바꾼다 → `bin/start.sh --check-only` 로 점검 → 세션 재기동. 되돌리기도 같은 절차다.

사후 보고 1줄(`config.report.order_format`, 필수 8항목 sym·side·qty·px·exchange·ord_no·status·decision_ref; console 은 stdout + `local/reports/<date>.md`):
```text
[order] 매수 336260 100주 @47000 SOR 체결 (ord_no=0088543, decision_ref=d-20260625-1035-336260, 10:36:05)
```
급정지 해제는 사람만: 게이트웨이 API 키 재활성화.

## 6. 예외

| 상황 | 처리 |
|---|---|
| 기간 만료(`horizon_d` 도달) | `flag(expired)` → 다음 장전 전까지 `exit` 또는 재언더라이팅. 미결정=`overdue_decision`. 자동 청산 없음 |
| 재검토일 경과 | `flag(recheck_due)` → 진입 전 스토리는 expired/재확인 결정, 보유 포지션은 coverage 재판정 |
| 주문 거절 / 확인 불발 | 재결정(수정·포기). 사용자 미응답 시 중단 |
| 손절 협의 무응답 | `consult.py status` 가 timeout 을 기록하면 제안대로 집행·보고. 결과 `decision`(`consult_ref`)·`exit` 을 원장에 |
| 배달 불가(herdr·에이전트 이름 유실) | 인바운드 어댑터가 5분간 백오프 재시도하고, 넘기면 `EXIT code=3 reason=undelivered` 로 종료. 재기동은 `bin/start.sh --role lead`(어댑터가 죽어 있으면 런타임을 띄우지 않는다) |
| `[watchlist-problem …]` 수신 | 감시 목록이 정상이 아니다 — 파일이 없거나, 읽을 수 없거나, 종목 코드가 아닌 항목이 들어 있다. 그 동안 종목 이벤트는 **전부** 버려진다(빈 목록은 의도로 보아 알리지 않는다). `bin/watchlist.py show` 로 확인하고 고치면 다음 이벤트부터 반영된다 |
| 공급자 장애·연결 끊김 | 어댑터가 재접속해 **현시점부터** 다시 받는다. 끊긴 동안의 이벤트는 오지 않으며 그것이 설계다 — 지나간 봉은 판단에 쓸모가 없다. 끊긴 사실은 `local/inbound.log` 의 `gap=not_replayed` 로 확인한다. 연결 자체가 의심되면 `python3 bin/inbound_rrr.py --probe 0`(§7). 그 구간이 궁금하면 공급자 로그를 직접 되짚어 일지에 남긴다 |
| 토큰 상한 접근 | 세션이 감시 종목을 줄인다. 코드는 줄이지 않는다 |
| 급정지(gw 키 비활성화)·연속 주문 오류 | 신규 주문 중단·보고. 급정지는 운영자가 소유한다 |
| 등록 없는 룰 변경 | 적용은 막지 않지만 `unregistered_pivot`, 평가창 리셋 금지 |
| 운영자 직접 매매 발견 | 대사에서 드러난 미기록 포지션은 보고하고, 세션의 결정과 분리해 둔다 |

## 7. 연결 테스트 (운영자)

이벤트가 안 온다 — 연결이 끊긴 것인가, 장이 조용한 것인가.

```bash
python3 bin/inbound_rrr.py --probe 0                  # 스트림의 처음부터
python3 bin/inbound_rrr.py --probe 1789450000000-0    # 특정 지점부터
```

그 지점부터 열어 받은 것을 한 줄씩 보여 주고 끝난다. **배달 0** — 감시 목록도 배달 로그도
열지 않는다.

```text
probe http://<host>/api/events/stream?since=1789450000000-0
HTTP 200 text/event-stream; charset=utf-8
  1789450204283-0 zone 336260 support_return sess=REG_KRX_NXT
  1789451053445-0 macro - h1444 sess=REG_KRX_NXT
받은 이벤트 10건
```

| 나온 것 | 뜻 |
|---|---|
| `HTTP 200` + 이벤트 줄들 | 연결·인증·이벤트 흐름 모두 정상. 안 오는 것은 감시 목록 문제다 — `bin/watchlist.py show` |
| `HTTP 200` + `받은 이벤트 0건` | 스트림은 열렸으나 그 지점 뒤로 아무것도 없다. `--probe` 를 더 과거로 두고 다시 본다 |
| `ERROR HTTP 401` | 키가 틀렸다(`config.gw.api_key`). 어댑터도 같은 이유로 exit 2 로 죽는다 |
| `ERROR URLError`·`OSError` | 게이트웨이에 닿지 않는다. 주소(`config.gw.base_url`)와 gw 기동 상태를 본다 |

**커서(`since`)를 쓰는 곳은 여기뿐이다.** 운전은 언제나 현시점부터다(§2-1). 연결 확인은
반대로 과거 지점이 필요하다 — 현시점부터 열면 조용한 장과 끊긴 연결이 똑같이 보인다.
운전용 URL 빌더에는 `since` 인자가 없다(넘기면 `TypeError`).
