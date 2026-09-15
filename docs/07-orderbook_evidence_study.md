# 호가 증거 연구 — `orderbook_enabled` 가 스윙 트레이더의 타이밍에 주는 것

Date: 2026-09-15 (관측 18:20~19:15 KST, NXT 애프터마켓)
Scope: rrr 의 호가 파생값(5분봉 OFI 근사·1호가 불균형·스프레드·소진·현상 라벨)이 rrr-swing 트레이더의
**진입·청산·집행 타이밍**에 어떻게 쓰일 수 있는지. 사실(코드·문서·실측) → 해석 → 최소 변경 제안 → 검증 계획 순.
근거 문서: rrr `docs/rrr-ingestor-orderbook-enable-analysis.md`, `docs/market_context_bars_handoff.md`,
`docs/orderbook_summary_timeseries_handoff.md`. 이 문서는 **제안**이며 계약(AGENTS.md·03-evidence_guide.md)을 바꾸지 않는다.

## 0. 요약

- 호가 파생값은 트레이더가 **이미 스스로 못 본다고 적은 질문** — "매수호가가 줄었는데 체결 소진인지 취소인지 확인하지 못했다"
  (`local/memory/journal/2026-09-14.md:45-47`) — 에 봉 단위로 답한다. `bid_flow = Δ매수총잔량 + 매도체결수량` 이 양수면 체결로 깎인 만큼
  이상 되메워진 것(흡수), 음수면 취소·철수다.
- 쓰임새는 **방향 예측이 아니라 국면 진단**이다. 같은 봉 안에서 `net_ofi` 부호와 수익률 부호는 24.6% 만 일치한다(§4.1) — 구조상
  그렇게 되어 있어(10호가 창 이동) 예측 지표로 읽으면 틀린다. 읽어야 할 것은 **가격이 안 움직이는 봉에서 누가 버티는가**다.
- 도달 경로는 이미 열려 있다(rrr-gw 가 rrr GET 전체를 `/api/*` 로 프록시). 트레이더가 **안 읽고 있을 뿐**이다.
  최소 변경은 `bin/decision_packet.py` 의 조회 목록에 `/api/stocks/{sym}/market-context?bars=2&market=none` 한 줄(§6).
- 전제: rrr `roles.ingestor.orderbook_enabled: true` 유지(09-15 18:01 수동 기동으로 켜짐, 서비스 복귀 시 config 반영 필요),
  쓰기 스로틀은 실측으로 검증됨(§5.3). 정규장 데이터로 §7 의 검증을 마치기 전에는 **단독 근거로 쓰지 않는다**.

## 1. 트레이더가 지금 보는 것과 못 보는 것

| 경로 | 무엇 | 호가 |
|---|---|---|
| SSE 다이제스트 (`bin/inbound_rrr.py`, `inbound_core.py:185`) | zone 6종·heartbeat 의 `evt sym lvl lvl_px px bar_ts as_of str sess istr_* ma20 ma60` — **포인터**만 | 없음 (설계상 값은 pull) |
| `bin/decision_packet.py:126-129` | `/api/stocks/{sym}/context` + `/flow` (+`/momentum`) | **없음** — 셋 다 호가 필드 0개(rrr `stock_flow.py`, `stock_context.py` 확인) |
| MCP `kiwoom_market_quote` (ka10004) | 호출 시점 10호가 **단발 스냅샷** | 주문 직전 유효성·"수량÷잔량" 1회 계산에만 사용(`docs/04-price_source.md:25-30`) |

즉 트레이더의 증거 묶음은 전부 **체결 축**(큰손/중간/개미, 체결강도, 프로그램·투자자 누적, VWAP·ATR·S/R)이다.
체결만으로는 "매도 체결이 쏟아지는데 가격이 안 빠진다"까지 보이고, 그것이 **매수벽이 받아주는 것**인지 **매도 물량이 마른 것**인지는
가를 수 없다. 09-14 006800 지지 방어 국면에서 트레이더가 정확히 이 지점에서 멈췄다.

## 2. rrr 가 이제 제공하는 것 (계약 사실)

`GET /stocks/{sym}/market-context?bars=N&market=none` → `bars[]` 각 5분봉에 price·trade·program·**orderbook** 네 축이 같은 `ts_start` 로 결합,
`confidence`·`weakness`·`phenomena` 동봉 (rrr `src/rrr/contract/market_context.py`).

| 필드 | 산식 (rrr `contract/orderbook_bars.py:123`) | 읽는 뜻 |
|---|---|---|
| `bid_flow_qty` | (창내 Δ매수총잔량) + 매도체결수량 | +: 매수벽 재적층(흡수) / −: 매수벽 철수 |
| `ask_flow_qty` | (창내 Δ매도총잔량) + 매수체결수량 | +: 매도벽 재적층 / −: 매도벽 철수 |
| `net_ofi_qty` | bid_flow − ask_flow | 순방향(§4.1 함정 참조) |
| `exhaustion_delta` | ask1 상향 횟수 − bid1 하향 횟수 | 봉 안에서 호가가 밀린 방향(사실상 가격 이동 카운터) |
| `l1_imbalance` | Σbid1 / (Σbid1+Σask1), 이벤트 가중 | 1호가 압력 |
| `avg_spread` | Σspread / n | 슬리피지·유동성 |
| `n` | 창내 0D 이벤트 수 | 관심도; `n<50` 이면 `thin` |
| `phenomena` | 위 값의 부호 조합 라벨 8종 | `buy_absorption`·`sell_absorption`·`bid/ask_replenishment`·`bid/ask_withdrawal`·`buy/sell_depletion` |
| `confidence` | ok / thin / partial / missing_join / gap / unavailable | **ok 아니면 phenomena 는 `low_confidence` 하나** |

`market-context` 의 `bars[].ts_start` 는 다이제스트의 `bar_ts` 와 같은 5분 격자다. 확정봉 규칙(AGENTS.md:58, playbook ③)과 그대로 맞물린다.

## 3. 타이밍 국면별 매핑 (제안)

| 국면 | 트레이더의 현재 규칙 | 호가 축이 더하는 질문 | 읽기 |
|---|---|---|---|
| 지지 이벤트 후 **진입 확인** | 확정봉으로 온 지지 이벤트 + 스토리·범주·한도 (playbook:61-66) | 그 확정봉에서 매수벽이 **받았나 빠졌나** | `bid_flow>0` & 가격 유지 → 흡수(근거 강화). `bid_flow<0`(`bid_withdrawal`) → 철수, **한 봉 더 기다린다** |
| 저항 접근·**돌파 시도** | 저항 복귀는 역추세 유형, 다음 확정봉 방향 병행(CK-1) | 매도벽이 **물러나나 되메우나** | `ask_flow>0` & `return_bp≈0` & 매수 체결 우위 → **매도벽에 흡수**(추격 금지, §4.3 예시). `ask_withdrawal`/`buy_depletion` & 가격 상승 → 벽 후퇴(돌파 확인) |
| **손절 판단** (5분 종가 하회, playbook:33) | 종가 기준 하회, 같은 값은 하회 아님 | 하회 봉에서 매수벽이 **같이 빠졌나** | `bid_withdrawal` 동반 → 하회 신뢰↑. `bid_replenishment` 동반 → 다음 확정봉 재확인(허위 하회 가능) |
| **집행**(일괄/분할, 주문가) | "80주 = 최우선 매수잔량의 2.5%" 1스냅샷 (journal 09-15:68-79) | 최근 N봉의 스프레드·이벤트율 | `avg_spread`(bp)·`n` 으로 슬리피지·얇음 판단 → 분할 여부. 1스냅샷보다 안정 |
| **보류** | 호가 0·누락·stale 이면 hold (AGENTS.md:78) | 호가 축의 결측 | `confidence∈{thin, unavailable, missing_join}` → 호가 축 **NA**, 다른 축으로만 판단(결측은 0이 아니다, AGENTS.md:56) |

핵심 원칙: 호가 축은 **이벤트 라벨을 주문 규칙으로 직역하지 않는다**(AGENTS.md:66)는 기존 원칙 아래 **다른 축과 병렬로 종합하는 한 축**이다.
단독 트리거로 쓰지 않는다.

## 4. 읽을 때의 함정 (이 보고서에서 가장 중요한 절)

### 4.1 같은 봉의 `net_ofi` 부호는 수익률과 **반대로** 나온다 — 구조 때문이다

실측(6종목·144 bar entries, §5.1): 둘 다 0 이 아닌 61건 중 부호 일치 15건(**24.6%**). 이는 데이터 결함이 아니라 산식의 구조다.
`tot_ask/tot_bid` 는 **보이는 10단계**의 총잔량이다. 가격이 k틱 오르면 위쪽 k단계가 새로 창에 들어와 매도총잔량이 늘고,
아래쪽 k단계가 빠져 매수총잔량이 준다 → `ask_flow` 양수·`bid_flow` 음수 → `net_ofi` 음수, 수익률 양수. 즉 **가격이 움직인 봉의 OFI 는 창 이동을 함께 세고 있다.**

따라서:
- `net_ofi` 를 "다음 봉 방향" 으로 읽지 않는다.
- 읽을 가치가 있는 봉은 **가격이 정체한 봉**(`return_bp≈0`, 존 레벨 근처)이다. 창이 안 움직였으니 flow 는 순수하게 적층/철수다.
- `exhaustion_delta` 는 봉 안 가격 이동의 카운터라 수익률과 71% 일치(31건)하지만 이는 동어반복이지 예측이 아니다.

### 4.2 결측·저신뢰 조건 (실측 근거 §5.1)

- `thin`(n<50): 애프터마켓 표본의 **43%**. 정규장에서는 n 이 수백~천 단위(18:20~18:30 봉 005930 n=1,400~1,470)라 비율이 크게 낮아질 것이나 **미측정**.
- `missing_join`: 체결이 없는 봉(체결 없는 얇은 종목·시간대)은 tradebkt 봉이 없어 `bid/ask_flow` 가 null — 006800 은 25%, 000660·005930 은 4%.
- **재기동 결손**: tradebkt 시드가 600페이지 상한에서 잘리면(09-15 18:01 재기동에서 7종목: 000660·005930·006400·009150·034020·108490·336260)
  그 이전 봉은 `missing_trade_join` 으로 OFI 가 null 이다. 호가(obbar)는 애초에 시드 불가라 재기동 이전 봉은 `orderbook: null`(`unavailable`).
  → **장중 재기동은 호가 증거를 그 세션 내내 훼손한다.** 06:05 크론 재기동만 허용하는 운영 원칙이 여기에도 적용된다.
- `partial`(형성 중 봉): 기존 규칙대로 확정 뒤 재확인. `bars=2` 로 받으면 [직전 확정봉, 현재 partial] 이 온다.
- 이벤트 가중 평균(시간 가중 아님), L1+총잔량만(10단계 사다리 미저장), `session_date` 는 KST 현재일 — rrr 쪽 명시 한계.

### 4.3 실제 봉 하나 — 체결만 보면 틀리는 예

005930, 2026-09-15 18:25 확정봉 (`confidence: ok`, n=1,459):

| 축 | 값 | 체결만 읽으면 | 호가까지 읽으면 |
|---|---|---|---|
| trade | 매수 90.1% · 체결강도 906.6 · 큰손 +49.1억 | 강한 매수세, 돌파 임박 | — |
| price | 247,500 → 247,500 · `close_position 1.00` · `return_bp 0` | 고점 마감 | 가격이 **안 움직였다** |
| orderbook | `ask_flow +65,321` · `bid_flow −2,489` · `l1_imbalance 0.80` | — | 매수 체결 3.1만 주가 쏟아졌는데 **매도 잔량이 6.5만 주 되메워짐** |
| phenomena | `sell_absorption`, `ask_replenishment`, `bid_withdrawal` | | 매수세가 매도벽에 흡수되는 중 → 이 봉을 근거로 추격하지 않는다 |

가격 정체 봉이라 §4.1 의 창 이동 오염이 없고, 그래서 flow 를 곧이곧대로 읽을 수 있는 경우다.

## 5. 실측 (2026-09-15, 라이브)

### 5.1 데이터 품질 — 6종목(감시 목록 4 + 대장주 2), 18:20~19:15, 5분 간격 12회, bar entries 144

| confidence | 000660 | 005930 | 006800 | 034020 | 108490 | 277810 | 합계 |
|---|---|---|---|---|---|---|---|
| ok | 11 | 5 | 1 | 11 | 8 | 3 | 39 (27%) |
| thin | 12 | 12 | 7 | 12 | 10 | 9 | 62 (43%) |
| partial | 0 | 6 | 5 | 0 | 3 | 8 | 22 |
| missing_join | 0 | 0 | 5 | 0 | 0 | 0 | 5 |
| unavailable | 1 | 1 | 6 | 1 | 3 | 4 | 16 |

phenomena 빈도(ok 봉만 라벨이 붙는다): `sell_absorption` 39 · `ask_replenishment` 39 · `buy_absorption` 29 · `bid_replenishment` 29 ·
`buy_depletion` 15 · `bid_withdrawal` 10 · `sell_depletion` 7 · `ask_withdrawal` 0. `n` 중앙값 10~48(애프터마켓), 최대 1,470.
`avg_spread` ≈ 1틱(000660 5~6bp, 005930 19~20bp, 277810 4~34bp). flow null 비율 4%(대형주)~25%(006800).
**한계: 정규장 표본이 없다.** 위 비율은 애프터마켓의 것이다.

### 5.2 도달 경로 (rrr-gw)

rrr-gw 는 `/api/*` GET 을 rrr-api 로 블랭킷 프록시한다(`rrr-gw/src/rrr_gw/authz.py:38-48`, `caddyfile.py:160-162`, `docs/api-contract.md:39,90`).
`/api/stocks/{sym}/market-context` 는 이미 통과한다. MCP 도구 87개·`kiwoom-gw` 스킬은 rrr 호가를 다루지 않는다(별도 서버 kiwoom-sdk-mcp).

### 5.3 수집기 (rrr-ingestor, 0D on)

- 스로틀 검증: 60초 창에서 005930 0D 이벤트 291건 → raw `ob:` 쓰기 64건(4.6:1), 활동 종목 22개 전부 이벤트 > 쓰기. 정상 간격 median 정확히 1,000ms.
- 드문 이중 쓰기(150초에 2회, pending 과 즉시 쓰기의 경합, 같은 초 `as_of` 라 stale 가드 무력)를 발견해 수정함(rrr `redis_store.py`, 테스트 2건, **미커밋**).
- 부하: CPU 12~13%(애프터마켓 순간값), Redis ~316 ops/s, heartbeat 정상, `last_event_age_ms` 0. 병목 없음. 정규장 부하는 미측정.
- 시딩 19분12초(tradebkt 17분28초, 429 재시도 221건) — 재기동 비용의 실체.

## 6. 최소 변경 제안 (rrr-swing 쪽만, rrr·gw 변경 없음)

1. `bin/decision_packet.py:126` `plan` 에 `("market_context", f"/api/stocks/{symbol}/market-context?bars=2&market=none")` 추가.
   원문 보존 규칙 그대로(`local/packets/`). `source_quality` 에 `bars[-2].confidence`(직전 확정봉) 를 사실로 실어 `ok` 가 아니면
   `warnings` 에 `orderbook_confidence=<값>` 을 남긴다 — 판단 없이 사실만(`decision_packet.py:143` boundary 유지).
2. `docs/03-evidence_guide.md` 에 CK-8 행 추가(초안):
   `| CK-8 | 지지 봉 bid_flow>0(흡수)·저항 봉 ask_withdrawal | 지지 봉 bid_withdrawal·저항 봉 sell_absorption | confidence≠ok 이면 NA. 가격 정체 봉에서만 flow 를 곧이 읽는다(§4.1). 단독 근거 금지 |`
3. AGENTS.md 는 바꾸지 않는다. "라벨 직역 금지"·"결측은 0 이 아니다"·"확정봉" 이 이미 호가 축을 다루는 데 필요한 규칙이다.
4. SSE 다이제스트에는 싣지 않는다 — 이벤트는 포인터, 값은 pull 이 계약이다(rrr `contract/events.py` docstring, AGENTS.md §0).
5. 브로커 `kiwoom_market_quote` 는 그대로 **주문 직전 1스냅샷** 용도로 남긴다. rrr 봉 집계와 층위가 다르다(연속성·라벨 vs 즉시성·10단계).

## 7. 믿기 전에 할 검증 (정규장 데이터)

1. **표본**: 정규장 5거래일, 감시 목록 전 종목의 market-context 봉을 5분마다 저장(§5.1 샘플러 재사용 가능).
2. **조건부 검정**(방향 예측 아님): 존 이벤트 봉(지지 6종 각각)에서 `bid_flow` 부호별로 **다음 1·3봉**의 레벨 유지율을 비교.
   저항 봉은 `ask_flow` 부호별 돌파 유지율. 창 이동 오염을 피하려 `|return_bp|` 가 작은 봉으로 층화.
3. **thin 임계**: 정규장 n 분포로 `n<50` 이 적절한지 확인(NXT 와 정규장은 다르다).
4. **집행 지표**: `avg_spread`(bp)·`n` 과 실제 체결 슬리피지(원장 `bin/order.py` 기록) 대조.
5. 통과 기준을 미리 적어 둔다(예: 지지 봉 `bid_flow>0` 군의 3봉 유지율이 `<0` 군보다 유의하게 높을 것). 미달이면 CK-8 을 넣지 않는다.

## 8. 운영 전제와 남은 결정

- rrr `config.local.yaml` `roles.ingestor.orderbook_enabled: true` 유지 + launchd 서비스 복귀(지금은 수동 디버그 프로세스).
- rrr 이중 쓰기 수정분 커밋·반영(다음 06:05 재기동).
- 장중 재기동 금지 원칙은 호가 증거 때문에 더 강해진다(§4.2).
- 결정 필요: ① §6-1 패킷 확장 착수 여부 ② §7 검증 기간(5거래일 제안) ③ CK-8 채택은 검증 뒤.
