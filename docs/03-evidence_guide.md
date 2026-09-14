# 증거 읽기 참고 — 필드·신뢰도·결측

**이 문서는 판단 순서를 정하지 않는다. 필드가 무엇을 뜻하고 언제 믿을 수 있는지만 적는다.** 무엇을 먼저 보고 무엇을 근거로 삼을지는 트레이더(세션)가 `AGENTS.md` §3·§4 아래에서 스스로 정한다. 아래 표의 CK 라벨은 원장 `decision.checklist` 의 키로 쓰는 이름표이며, 항목마다 한 줄 요약 또는 NA 를 적는다.

공통 규칙: `null`·빈 배열·`stale` 은 결측이지 0 이나 안정이 아니다. 결측은 NA 로 적고 단독 근거로 쓰지 않는다. 타임스탬프는 tz 접미사 없는 naive KST. 필드 원본 계약은 rrr-gw-skills 이며, 원본과 다르면 원본이 맞다.

## 1. 증거 항목표 (CK)

| CK | 항목 | 필드 | 소스 | 장중 신뢰도 | 결측 처리 |
|---|---|---|---|---|---|
| 1 | 이벤트 위치(존 6종 또는 `heartbeat` 중 무엇, 레벨, 강도, 봉) | 다이제스트 태그(`#mon schema=rrr_mon_alert_v1`)의 `evt` ∈ {support_enter · support_return · support_break · resistance_enter · resistance_return · resistance_break · heartbeat}, `lvl` `lvl_px` `px` `bar_ts` `str` `sess` | 태그 줄(계약), `bar_ts` 는 `/context.candles_5m[].t` 와 대조 | `px` 는 평가봉 종가(라이브 아님). `sess` 토큰과 `/context.session.phase` 는 다른 enum | 태그 스키마 불일치·필드 부재 → 그 이벤트로는 판단하지 않는다 |
| 2 | 프로그램 당일 누적과 최근 봉 방향 | `/flow.program_cum[].net` `.partial` `.t`, `/context.supply.program_net_eok` | 공급자 flow·context 스펙 | 진행 중 봉은 `partial=true`. 누적은 인접 차분으로 봉별 방향 | `[]`/null = 증거 없음(0 아님) |
| 3 | 큰손(체결금액 상위 그룹) 당일 누적과 방향 | `/flow.bucket_bars[].big/mid/retail` `.strength` `.partial` | 공급자 flow 스펙 | 5분봉·partial. **체결 크기 그룹이지 투자자 유형 아님** | `[]`/null = 증거 없음 |
| 4 | 외국인·기관 누적(장중 잠정치) | `/flow.investor_cum[].frgn/orgn`, `freshness.investor_freshness` `investor_age_sec`, `/context.supply.foreign_net_eok/institution_net_eok/age_sec` | 공급자 flow·context 스펙 | **낮음** — 장중 결측·역전 흔함, `age_sec` 확인, `stale` = 결측 | null·stale = 결측이지 0·안정 아님. 확정치는 마감 후 |
| 5 | 움직임의 귀속(시장 기인 vs 종목 기인) | `/context.market_context.{kospi_change_pct, kospi_close, program_market_net_eok, breadth, sampled_at, age_sec}` | 공급자 context 스펙 | 당일 `age_sec` | null leaf = 결측 |
| 6 | 스토리 존재와 논지 정합 | `local/stories/<symbol>.md` 의 thesis·가격 범주·무효화 조건·기간·재검토일·상태 | 로컬 스토리(공급자 엔드포인트 없음) | 트레이더 소유, 마감 후 갱신 | 스토리 없음 → 사지 않는다(관심 등재까지) |
| 7 | 계좌 상태(보유·미체결·현금)와 플레이북 한도 | 브로커 조회 + 자기 원장 + `local/memory/playbook/<최신>.md` | 로컬(공급자 엔드포인트 없음) | 주문 직전 재조회. 체결 원장 > 잔고 스냅샷 | 조회 실패 → 주문 없음 |

## 2. 보유 종목의 반전 읽기 (CK-S)

보유 종목의 wake 에서 같은 항목을 반전 방향으로 읽을 때의 참고표. `coverage.basis` 에 요약을 남긴다.

| 항목 | 매수 쪽 읽기(요지) | 매도·커버리지 쪽 읽기 | 추가 입력 |
|---|---|---|---|
| CK-1 | 지지 복귀·저항 돌파 등 순추세 위치, 확정봉 | 지지 이탈·저항 복귀(역추세) 위치, 확정봉 | 역추세 유형은 다음 확정봉 방향을 함께 보는 관행이 있다 |
| CK-2 | 프로그램 누적 양(+)·최근 봉 유입 | 누적 부호 전환·최근 봉 유출 지속 | partial 봉은 확정 뒤 재확인 |
| CK-3 | 큰손 그룹 순매수·strength 상승 | 큰손 순매도 전환·strength 하락 | 체결 크기 그룹 해석 한계 |
| CK-4 | 외인·기관 누적 양(+)(참고, 낮은 신뢰) | 누적 부호 역전(참고) | 확정치는 마감 후. 단독 근거 금지 |
| CK-5 | 종목 기인 강세(시장 대비 초과) | 시장 기인 하락에 종목 동조·breadth 악화 | market_context age_sec |
| CK-6 | 논지 정합·목표 미달 | 목표 도달, 무효화 조건 충족, 논지 훼손 | 기간 만료·재검토일 경과는 회계 `flag` 로 온다 |
| CK-7 | 플레이북 한도 안·현금 가용 | 플레이북 비중 초과·kill·운영자 지시 | 한도는 플레이북(코드가 읽지 않는다) |

## 3. 그 밖의 정보원

| 층 | 필드·출처 | 신뢰도·주의 |
|---|---|---|
| 시장·매크로 | rrr macro(다이제스트 `#macro` 태그: `slot` `sess` `as_of`), `/context.market_context` | 슬롯 단위 스냅샷. `as_of` 로 나이를 본다 |
| 서사 | 공시 → 테마·업종 → 뉴스 헤드라인(정형 피드, 필요할 때 웹 검색) | 출처·시각을 적는다. 헤드라인은 가격에 이미 반영됐을 수 있다 |
| 현재가 | `docs/04-price_source.md` 폴백 체인(`/context.quote.price` → 브로커 실시간 호가 `_AL` → 기본 정보 `cur_prc`) | 분석 가격과 주문 가격은 별도. 주문 직전에는 브로커 호가를 다시 읽는다. 개장 전 `cur_prc` 는 stale |
| 계좌 | 브로커 조회 도구(잔고·예수금·미체결·당일 체결) | 운영자 직접 매매가 섞일 수 있다. 캐시 금지 |
| 세션 코드 | 태그 `sess` ∈ PRE_NXT · PRE_TO_REG_BREAK · REG_KRX_NXT · REG_TO_POST_BREAK · POST_NXT | 공급자는 분류만 한다. 세션별 해석·주문 가능 여부는 트레이더 책임 |
