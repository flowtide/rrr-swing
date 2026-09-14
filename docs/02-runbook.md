# 운영 런북 — 하루와 한 주

값(한도·손실 정책·진입 기준)은 `local/memory/playbook/<최신>.md`(트레이더)가, 모드·만료 기본값·토큰 상한·회계 시각은 `config/config.json`(운영자)이 소유한다. 급정지는 게이트웨이 API 키 비활성화(D18)로 집행한다. 모든 명령은 이 작업 디렉터리에서 실행한다. 회계 도구(`scripts/marks.py`·`flags.py`·`review.py`)는 세션이 아니라 **운영자의 크론**이 돌린다(설치는 §3, 코드가 설치하지 않는다). 세션이 쓰는 원장 이벤트는 `scripts/ledger.py append` 로만 기록한다(역할 제한: `fill`·`cancel` 은 `--role safety`, `mark`·`flag`·`review` 는 `--role accounting`).

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
5. **구독을 선언한다** — 활성 유니버스 × 이벤트 유형 × 세션 × 만료. 구독 선언은 활성 유니버스의 기계적 표현이며 판단을 담지 않는다. 선언 없는 종목은 배달되지 않는다.
   ```bash
   python3 bin/subscribe.py set --symbols 336260,005930 --events support_return,resistance_break,support_break,resistance_return,support_enter,resistance_enter --sessions REG_KRX_NXT --expires 2026-06-30T15:30:00 --append-ledger
   python3 bin/subscribe.py show
   ```
6. **기동**(`bin/start.sh --role lead`, Herdr 환경): config 검증(gw 는 `gw.base_url`·`gw.api_key` 존재만, 값 미출력, `mode`) → selftest(`inbound_rrr.py --selftest`, `subscribe.py show`, `ledger.py selftest`, `rt_calc.py selftest`, `marks.py --selftest`, `flags.py --selftest`, `review.py --selftest`) → gw `/api/health` 1회 → 같은 저장소의 이전 인바운드 어댑터 종료(lsof cwd) → 인바운드 어댑터를 `--deliver herdr --target rs-lead` 로 백그라운드 기동(`local/inbound.pid`, 로그 `local/inbound.log`) → 기억 머지(`bin/context_load.py` → `local/system-prompts/<오늘>.md`) → 런타임 기동(부트 프롬프트 = AGENTS.md 적용 + `mode` + 기동 순서(AGENTS.md §10) + 도구 목록 + 오늘 원본 경로. 기억은 `--append-system-prompt-file` 로 들어간다). 기본 소스 rrr_stream(gw SSE)은 봇 토큰이 필요 없다. 최초 1회는 `bin/init_local.sh` 로 `local/` 골격과 첫 원본(`docs/templates/`)을 만든다(멱등). `local/` 에 무엇이 있고 무엇을 고쳐도 되는지는 `docs/06-local.md`.
   ```bash
   bin/init_local.sh                        # 최초 1회(멱등): 원본 local/memory/{playbook,state,journal} + 산출물 local/system-prompts/ + local/{stories,meetings,lessons,consults,packets,reports} + 첫 원본
   bin/start.sh --role lead --check-only    # config·자격증명 점검(기동 0)
   bin/start.sh --role lead                 # mode 는 config.mode. 런타임 env: RS_ROLE=lead
   ```
   기동 직후 `kiwoom_list_tools` 로 도구 목록을 확인한다. 주문은 gw MCP 주문 도구로 내고, `bin/order.py` 는 집행 결과 기록 및 사후 보고를 담당한다.
7. 세션 기동 직후: 브로커 전제 확인 → 계좌·원장 대사(`python3 scripts/ledger.py validate`, `python3 scripts/ledger.py tail -n 20`, `python3 scripts/marks.py --report`) → 상태 확인(시스템 프롬프트) → 활성 구독 복원(`bin/subscribe.py show`; 비었거나 만료면 §1-5 로 다시 선언) → 재시작 뒤 밀린 이벤트가 있으면 `bin/inbound_rrr.py --once`(커서 이후 `GET /api/events` 페이지 catch-up) → 운영자 보고(console: `python3 bin/report.py --text "…"`, telegram: `bin/tg_send.py`).
8. 전달: **인바운드 어댑터**(`bin/inbound_rrr.py`)가 `local/subscriptions.json` 을 읽어 구독 선언을 서버 쿼리(`symbols`·`kinds`)로 넘기고, 받은 엔트리를 **로컬에서 다시 검사**한다(세션·만료는 서버가 모른다; macro 도 `symbols` 필터에서 면제되므로 `symbols` 는 항상 전달, `evts`·`tick` 은 전송 0).
9. 사전 조건(사람, 1회): gw 가 `/api/events/stream`(SSE)·`/api/events` 를 제공하도록 기동돼 있을 것, API 키를 환경변수로 준비. Telegram 운영자 채널을 쓸 때만 봇 생성·`operator_chat_id` 확인이 필요하다. 테스트 실행: `uv run --no-project --with pytest pytest -q`.

## 2. 장중 — 다이제스트·30분 체크·주문·협의

1. **다이제스트 도착**(계좌당 봉당 1건, 소스 무관 같은 형식). 형식: `[digest account=<id> bar_ts=<ts> n=<raw_event_count>] <태그 줄> | <태그 줄> …`(한 줄, 상한 초과 시 `part=k/n` 분할). rrr 스트림 엔트리는 계약 키 순서의 태그 줄로 되살려 넣는다(Telegram 태그와 동일). 운영자 지시는 console 채널이면 pane 직접 입력, telegram 이면 `[운영자 DM] <본문>`. 어댑터는 같은 5분봉의 구독 종목 이벤트를 `quiet_sec`(config) 또는 새 봉 도착까지 모아 1건으로 합치고, 중복 키 (account_id, sym, evt, bar_ts) 는 버린다. 미구독·태그 깨짐·(Telegram) 잘못된 발신자는 배달하지 않고 `local/delivery.jsonl` 에 `source`·reason 으로만 남긴다(unsubscribed | bad_sender | bad_tag | other_chat | duplicate | session_mismatch | session_unknown | subscription_expired | already_delivered | operator_only). 커서 `local/events.cursor` 는 엔트리가 배달 또는 skip 기록된 뒤에만 전진한다(at-least-once). 스트림이 끊기면 어댑터가 backoff(1→60s)로 `since=<커서>` 재접속하고, 하트비트가 `heartbeat_timeout_sec` 동안 없으면 끊고 재접속한다.
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
   - 주문은 지정가 + SOR 고정(`dmst_stex_tp="SOR"`). 시장가는 쓰지 않으며 규칙에 의한 취소가 없으면 20:00 장 종료 시 자동 소멸한다(D22).
   - 급정지는 게이트웨이 API 키 비활성화(D18)로 집행하며, 해제는 운영자만 수행한다.
5. **30분 시장 체크** — 인바운드 어댑터가 창(`inbound.market_check`, 기본 09:00~15:30 매 30분) 안에서 `[market-check ts=HH:MM]` 한 줄을 pane 에 넣는다(내용은 시각뿐, 다이제스트와 독립, 두 소스 공통, `local/delivery.jsonl` kind=market_check). 세션은 그때 시장 전체(지수·업종·해외·breadth)를 본다. 장세 판단이 바뀌면 STATE 의 장세·태도 줄을 갱신하고 보유 종목의 커버리지를 다시 본다. 창·주기는 운영자가 config 로 정하고, 그 안에서 더 자주 볼지는 세션이 정한다.
6. **손절 실시간 협의** — 사전 협의가 없는 손절은 `bin/consult.py propose` 로 제안(청산·축소·보류, 가격, 수량, 이유)을 남기고 운영자 채널에 보낸 뒤 기다린다(응답 기한 기본 30분 = `respond_by`). 운영자는 콘솔에서 `agree`/`decline`. 세션은 `status <id>` 로 확인한다 — pending 이고 기한이 지났으면 timeout 이 1회 기록된다. agreed·timeout 이면 제안대로 집행(§2-4, `decision` 에 `consult_ref`)하고 보고하며, declined 면 보유를 유지하고 다시 판단한다. 집행 판단은 세션의 것이고 consult.py 는 주문을 만들지 않는다.
   ```bash
   python3 bin/consult.py propose --sym 336260 --action exit --px 45900 --qty 100 --reason "지지 이탈, 논지 훼손" --report   # → id, respond_by = 제안 시각 + 30분, 보고 1줄
   python3 bin/consult.py agree <id> --note "ok"        # 운영자(콘솔). 거절은 decline <id>
   python3 bin/consult.py status <id>                   # pending | agreed | declined | timeout(기한 경과 시 1회 기록, 재호출 중복 0)
   ```
7. 체결은 주문 집행 후 대사하며 `fill` 을 남긴다. 주문 거래소는 SOR 고정(`dmst_stex_tp="SOR"`)이며 장 종료(20:00) 시 자동 소멸하므로 마감 전 취소 작업을 두지 않는다(D22).
8. 보유 종목의 wake 에서는 반전 읽기(`03-evidence_guide.md` CK-S)로 `coverage` 를 갱신한다(§3-4 명령).
9. 장중에 발견한 종목은 STATE 관심 목록에 올린다. 스토리에 계획한 가격 범주가 없으면 사지 않는다.

## 3. 장 마감 후 — 일지·회계 (회계 = 운영자의 크론, 세션은 읽기만)

1. **주문 만료**: 거래소는 SOR 고정(`dmst_stex_tp="SOR"`)이며, 규칙에 의한 취소가 없으면 20:00 장 종료와 함께 자동 소멸한다. 따라서 마감 전 취소 작업을 두지 않는다(D22).
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
급정지 해제는 사람만: 게이트웨이 API 키 재활성화(D18).

## 6. 예외

| 상황 | 처리 |
|---|---|
| 기간 만료(`horizon_d` 도달) | `flag(expired)` → 다음 장전 전까지 `exit` 또는 재언더라이팅. 미결정=`overdue_decision`. 자동 청산 없음 |
| 재검토일 경과 | `flag(recheck_due)` → 진입 전 스토리는 expired/재확인 결정, 보유 포지션은 coverage 재판정 |
| 주문 거절 / 확인 불발 | 재결정(수정·포기). 사용자 미응답 시 중단 |
| 손절 협의 무응답 | `consult.py status` 가 timeout 을 기록하면 제안대로 집행·보고. 결과 `decision`(`consult_ref`)·`exit` 을 원장에 |
| 배달 불가(herdr·에이전트 이름 유실) | 인바운드 어댑터가 5분간 백오프 재시도하고, 넘기면 `EXIT code=3 reason=undelivered` 로 종료. 재기동은 `bin/start.sh --role lead`(어댑터가 죽어 있으면 런타임을 띄우지 않는다) |
| 공급자 장애 | 구독분 이벤트 유실 → 판단 기회 유실. 인바운드 어댑터가 재접속하고 `--once` 로 catch-up. 유실 구간은 일지에 |
| 토큰 상한 접근 | 세션이 구독을 줄인다(종목·유형·세션). 코드는 줄이지 않는다 |
| 급정지(gw 키 비활성화)·연속 주문 오류 | 신규 주문 중단·보고. 급정지는 운영자가 소유한다(D18) |
| 등록 없는 룰 변경 | 적용은 막지 않지만 `unregistered_pivot`, 평가창 리셋 금지 |
| 운영자 직접 매매 발견 | 대사에서 드러난 미기록 포지션은 보고하고, 세션의 결정과 분리해 둔다 |
