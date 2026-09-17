# 호가 파생값 정규장 검증 — 07 §7 의 답

Scope: `docs/07-orderbook_evidence_study.md` §7 이 요구한 정규장 검증. rrr `market-context` 의 호가 축(`bid_flow`·`ask_flow`·`net_ofi`·
`l1_imbalance`·`exhaustion_delta`·`phenomena`·`confidence`)과 `/detail` 원본(`total_bid_delta`·`ask_bid_ratio`)이 5분봉 타이밍 판단에
정보를 주는지를 존 이벤트와 결합해 조건부로 센다. 판단·추천 규칙을 새로 만들지 않고, **CK-8 을 넣을지**만 답한다.
표본: 2026-09-16 정규장, rrr 활성 유니버스 18종목, 확정봉 `09:00~15:20`. 수집·집계 스크립트는 `scripts/research/ob_sampler.py`·`ob_analyze.py`.

## 0. 결론

- **CK-8(07 §6-2 초안)은 넣지 않는다.** `bid_flow`/`ask_flow` 의 부호와 `phenomena` 의 철수·재적층 라벨은 정규장에서 **가격 이동의 동어반복**이다.
  `bid_withdrawal` 이 붙은 봉의 86% 는 그 봉의 수익률이 양수이고, `ask_withdrawal` 의 84% 는 음수다(§3). 벽의 행동이 아니라 10호가 창이
  움직인 흔적이다. 07 §4.1 이 애프터마켓에서 본 함정은 정규장에서 더 강하다(net_ofi 부호 일치율 24.6% → 17%, |return_bp| 가 클수록 5% 까지).
- 07 §3 이 기대한 "가격이 정체한 봉에서 누가 버티는가" 는 **정규장에서 관측 기회가 거의 없다.** 1호가가 한 번도 안 움직인 봉은 12%(166/1386)뿐이고
  저활동 종목에 몰린다. 정체 봉의 Δ잔량·l1 부호는 다음 봉 방향과 무관하다(상승비 51~59%, 봉 단위 기준선 55% 안).
- 지지·저항 이벤트의 유지율은 **이벤트 종류의 기저율**이 결정한다(support_enter 1봉 유지 69%, support_return 100%, support_break 13%).
  호가 축 어느 값으로 갈라도 그 안에서 차이가 나지 않는다.
- **쓸 수 있는 것은 두 가지뿐이다.** ① `n`·`avg_spread` = 집행 참고(얇음·슬리피지). 정규장 n 은 최소 160, 중앙값 702 라 `thin(<50)` 은
  발화하지 않는다. ② `/detail` 의 `ask_bid_ratio`(전체 깊이 매도/매수 잔량비) = **종목 자세**. 하루 내내 종목별로 고정된 값(009150 4.2,
  277810 0.15)이라 봉 단위 타이밍이 아니라 종목 프로필로 읽는 것이며, 이것도 1일 표본이라 채택은 보류한다.
- rrr-swing 쪽 조치는 `decision_packet` 의 market-context 조회를 **유지**하되(비용 0, `confidence`·`n`·`avg_spread` 는 사실로 유용)
  evidence guide 에 "호가 phenomena 라벨과 flow 부호는 읽지 않는다" 를 적는 것이다(§6). 실매매(1주)는 이 검증에 필요 없어 하지 않았다.

## 1. 데이터

| 항목 | 값 |
|---|---|
| 종목 | 오늘 존 이벤트가 난 활성 유니버스 18종(대형주 005930·000660·005380 포함, 감시 목록 4종 포함) |
| 봉 | `GET /api/stocks/{sym}/market-context?bars=0&market=none` 5분 간격 스냅샷. `bars=0` 은 보존 창(2거래일) 전체를 돌려주므로 마지막 스냅샷이 곧 하루치 확정봉 |
| 원본 | `GET /api/stocks/{sym}/detail` 의 `orderbook_bars`(`total_bid_delta`·`total_ask_delta`·`ask_bid_ratio`·`ask_price_up_count`·`bid_price_down_count`) |
| 이벤트 | `GET /api/events?since=<오늘 0시 id>&kinds=zone` replay — 지지·저항 6종 |
| 봉 유효 | partial 아님 · `weakness` 비어 있음 · orderbook 있음 · `n>=50` · ts 09:00~15:20. **응답 단위 `confidence` 는 쓰지 않는다**(§5-1) |
| 결합 | 이벤트 `bar_ts` = 봉 `ts_start`. "다음 k봉" = `ts_start + 5k 분` |

## 2. 품질 — 07 §7-3 의 답

- 정규장 확정봉 n: 최소 160 · p10 350 · 중앙값 702 · p90 1,440 · 최대 1,499. `thin(n<50)` 0건. 07 §5.1 의 43% 는 애프터마켓 값이었다.
  → `N_THIN=50` 은 정규장에서 무의미하고, 애프터마켓·프리마켓 판별용으로만 남는다.
- `avg_spread` 중앙값 12.8bp, p90 22bp(종가 대비). 대형주 1틱.
- `missing_join`(체결 없는 봉) 0건. 정규장 활성 종목은 매 봉 체결이 있다.
- `confidence=gap` 304/1386(22%), `unavailable` 18. gap 은 전부 봉 자체 weakness 가 비어 있고 4종목(005930·034020·000660·005380)의 오늘 봉 **전부**에 붙었다.
  원인은 전날(09-15) 프리마켓 08:45→09:00 한 칸 결손. §5-1 참조.

## 3. flow 부호와 라벨은 가격 이동의 동어반복이다 — 07 §4.1 의 정규장 판

같은 봉 `net_ofi` 부호 vs `return_bp` 부호 일치율(둘 다 0 아닌 봉):

| \|return_bp\| | 일치 |
|---|---|
| 1~20 | 135/571 = 24% |
| 21~50 | 41/410 = 10% |
| >50 | 6/112 = 5% |
| 전체 | 182/1093 = 17% (애프터마켓 09-15: 24.6%) |

가격이 많이 움직일수록 반대로 나온다. `exhaustion_delta` 부호는 수익률 부호와 77% 일치(동어반복, 07 §4.1 과 같다).

라벨별로 같은 봉 수익률 부호를 세면:

| 라벨 | n | ret>0 | ret=0 | ret<0 |
|---|---|---|---|---|
| bid_withdrawal | 188 | **162** | 16 | 10 |
| ask_withdrawal | 114 | 4 | 14 | **96** |
| bid_replenishment | 876 | 281 | 199 | 396 |
| ask_replenishment | 950 | 439 | 201 | 310 |

`bid_withdrawal` = "이 봉에서 가격이 올랐다", `ask_withdrawal` = "내렸다". 07 §3 의 "지지 봉 bid_withdrawal → 철수, 한 봉 더 기다린다" 는
정규장에서 **되돌림 봉(가격이 오른 봉)마다 발화**한다. support_return 10건 중 6건이 bid_withdrawal(=가격이 오른 봉)이었고 6건 전부 다음 봉에 레벨을 유지했다(336260 11:00 +130bp 등).

산식이 그렇게 만든다. `bid_flow = (last_tot_bid − first_tot_bid) + 매도체결량`(rrr `contract/orderbook_bars.py`). 매도체결량은 항상 크고 양수라
부호는 거의 언제나 양수(지지 이벤트 봉 43/49, 저항 30/32)이고, 음수가 되는 유일한 길은 Δ총잔량이 체결량보다 더 크게 줄어드는 것 — 가격이
올라 아래쪽 호가단이 창 밖으로 나갈 때다. 라벨 8종 중 `buy/sell_absorption`·`bid/ask_replenishment` 는 거의 모든 봉에 함께 붙어(876~950/1386)
구분력이 없다.

## 4. 조건부 검정 — 07 §7-2 의 답

### 4.1 정체 봉에서 다음 봉

|return_bp| ≤ 10 인 봉의 호가 축 → 다음 봉 return_bp(상승비 = 상승/(상승+하락)):

| 조건 | n | 다음 봉 평균 | 상승비 |
|---|---|---|---|
| Δbid>0 (벽 커짐) | 291 | +1.6bp | 53% |
| Δbid<0 (벽 줄어듦) | 160 | +2.4bp | 56% |
| Δask>0 | 282 | +0.5bp | 51% |
| Δask<0 | 169 | +4.2bp | 58% |
| bid↑ask↓ (매수 우위 책) | 107 | +4.2bp | 59% |
| bid↓ask↑ | 98 | +1.2bp | 55% |
| l1_imbalance > 0.55 | 135 | +2.8bp | 57% |
| l1_imbalance < 0.45 | 230 | +0.6bp | 52% |

봉 단위 기준선(모든 봉 다음 6봉 상승비 55%, 오후 상승 표류) 안에 전부 들어온다. 방향이 있다면 벽이 커지는 쪽으로 가야 하는데 그 차이가 없다. Δbid = `bid_flow − 매도체결량`, Δask = `ask_flow − 매수체결량` 으로
market-context 만으로 복원한 값이며 `/detail` 의 `total_*_delta` 와 같다.

1호가가 한 번도 안 움직인 봉(`ask_price_up_count + bid_price_down_count = 0`)은 166/1386(12%)이고 저활동 종목에 몰린다.
정규장 봉의 57% 는 5회 이상 움직인다. "창이 안 움직인 봉만 곧이 읽는다" 는 07 §4.1 의 처방은 정규장에서 **적용할 봉이 없다.**

### 4.2 존 이벤트 봉

이벤트 88건 중 봉 유효 81건(프리마켓 7건은 09:00 이전이라 제외).

| 이벤트 | n | 1봉 유지 | 3봉 유지 | 호가 축으로 가른 결과 |
|---|---|---|---|---|
| support_enter | 16 | 69% | 73% | flow>0 전부 — 가를 것이 없음 |
| support_return | 10 | 100% | 100% | flow<0 6건(되돌림 봉이라 §3 의 산물)·>0 4건 전부 유지 |
| support_break | 23 | 13% | 9% | 어느 값으로도 회복을 가르지 못함 |
| resistance 3종 | 32 | 37% | 45% | flow>0 30건 / <0 2건(0%) — 비교 불성립 |

유지 = 다음 봉 종가가 레벨 이상(지지) / 초과(저항). 07 §7-5 가 미리 적어 둔 통과 기준(지지 봉 `bid_flow>0` 군의 3봉 유지율이 `<0` 군보다
유의하게 높을 것)은 **비교 자체가 성립하지 않는다** — `<0` 군이 n=6 이고 그 6건이 모두 유지했다(되돌림 봉의 산물).

### 4.3 종목 자세 — `ask_bid_ratio`

`/detail` 의 `ask_bid_ratio`(평균 총매도잔량 / 평균 총매수잔량)는 봉이 아니라 종목의 값이다: 정규장 확정봉 중앙값이 009150 4.24, 000660 2.94, 066570 1.87 … 034020 0.24, 277810 0.15 로 종목마다 다르고 하루 내내 유지된다.
정체 봉에서 `>1.5` 군이 보인 다음 봉 하락은 그 종목들의 하루 흐름이라 일반화할 수 없다. 여러 날 표본에서 종목 프로필로 재검토할 후보이지 타이밍 지표가 아니다.

## 5. rrr 계약에 대한 관찰 (공급자 결정 사항, 이 문서는 바꾸지 않는다)

1. **`confidence=gap` 은 응답 단위다.** `_has_gap(ts_values)` 를 반환 창 전체에 한 번 적용해 모든 봉에 붙인다(rrr 의 `market_context_bars_handoff.md`
   198행에 명시). `bars=2`(rs-lead 의 패킷)에서는 국소적이지만 `bars=0` 에서는 전날 결손 한 칸이 종목의 이틀치 봉을 전부 `gap` 으로 만들어
   confidence 를 쓸 수 없게 한다. 봉 단위 gap(직전 봉이 5분 전이 아니면 그 봉만)으로 바꾸면 `bars=N` 소비자에게도 더 정확하다. rrr-web 이
   `bar.confidence` 를 표시하므로 계약 변경이다.
2. **flow 라벨은 창 이동을 세고 있다.** 10단계 사다리를 저장하지 않는 한(07 §4.2) 보정할 수 없다. 최소 조치는 `phenomena` 를
   `ask_price_up_count + bid_price_down_count` 가 작을 때만 붙이거나, 문서에 "가격이 움직인 봉의 withdrawal/replenishment 는 창 이동" 을
   명시하는 것이다.
3. `/detail` 은 market-context 에 없는 원본(`total_*_delta`·`ask_bid_ratio`·이동 횟수)을 이미 준다. 연구는 이쪽을 쓴다.

## 6. rrr-swing 조치

- `bin/decision_packet.py` 의 market-context 조회는 유지한다. `bars[-2].confidence`·`orderbook.n`·`avg_spread` 는 집행·결측 판단의 사실이다.
- `docs/03-evidence_guide.md` 에 CK-8 을 넣지 않는다. 대신 호가 축 읽기 규칙 한 줄: "`phenomena` 라벨과 `bid_flow`/`ask_flow`/`net_ofi` 부호는
  가격 이동의 동어반복이라 읽지 않는다. `n`·`avg_spread` 만 집행 참고." (채택은 운영자 결정)
- rs-lead 의 오늘 판단은 호가 축 없이 체결 축만으로 이루어졌고(11:05 336260 rationale), 이 검증은 그것이 손실이 아니었음을 보인다.

## 7. 재현

```bash
# 장중 5분 간격 수집(둘 다 detach, 20:10 종료)
python3 bin/detach.py python3 scripts/research/ob_sampler.py --symbols <csv> --since <오늘 0시 ms>-0 --out local/research/orderbook/<date>
python3 bin/detach.py python3 scripts/research/ob_sampler.py --mode detail --symbols <csv> --out local/research/orderbook/<date>/detail
# 집계
python3 scripts/research/ob_analyze.py --out local/research/orderbook/<date> --date <date>
```

## 8. 한계

- 1거래일·18종목. 이벤트 종류별 n 이 8~23 이라 유지율은 기저율 관찰이지 검정이 아니다. 여러 날을 모아도 §3 의 구조적 결론(라벨 = 가격 이동)은 바뀌지 않는다.
- `ask_bid_ratio` 종목 프로필은 여러 날 표본이 있어야 한다.
- 집행 지표(07 §7-4, `avg_spread` vs 실제 슬리피지)는 주문이 없어 검증하지 않았다. 1주 주문의 슬리피지는 스프레드 1틱과 같아 표본이 되지 않는다.
