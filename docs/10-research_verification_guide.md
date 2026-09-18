# 연구 실험 검증 가이드 — 목적·내용·확인 방법

Scope: `docs/08-orderbook_regular_session_study.md`(호가 축)와 `docs/09-whale_investor_index_panel.md`(큰손·투자자·업종·프로그램·KOSPI 패널)의
실험을 **제3자가 확인**하는 절차. 모든 경로는 `rrr-swing/` 기준. 실행 산출물은 `local/research/orderbook/<date>/` 아래에 있고 커밋되지 않는다.

## 1. 목적

| 실험 | 묻는 것 | 답의 형태 |
|---|---|---|
| 08 호가 | rrr `market-context` 의 호가 파생값(`bid_flow`·`ask_flow`·`phenomena`·`confidence`)이 5분봉 타이밍에 정보를 주는가 | CK-8 채택/미채택. 정규장 표본의 조건부 집계 |
| 09 패널 | 큰손(체결 1건 1억 초과) 순매수가 가격을 앞서는가. 지수·투자자·업종·프로그램을 같은 봉에 맞추면 어느 정의가 살아남는가 | 사전등록 가설 H1~H5 의 5거래일 통과 여부 |

두 실험 모두 **판단 규칙을 만들지 않는다**. 세는 것은 "신호 봉 이후 k봉 수익률(또는 KOSPI 대비 초과수익률)의 부호 일치율과 평균"이며,
같은 봉의 수익률은 결과에 넣지 않는다(동행은 예측이 아니다). 신호는 사건 단위(같은 종목 연속 신호봉 = 1건)로 센다.

## 2. 내용 — 무엇이 돌고 무엇이 남는가

| 프로세스 | 명령(요지) | 주기 | 산출 |
|---|---|---|---|
| market-context+events 샘플러 | `ob_sampler.py --symbols … --since … --out <date>` | 300초 | `<HHMMSS>/<sym>.json`(bars=0 전체봉), `events.jsonl`, `sampler.log` |
| detail 샘플러 | `ob_sampler.py --mode detail --symbols … --out <date>/detail` | 300초 | `detail/<HHMMSS>/<sym>.json`(candles·trade_buckets·investor_flow·orderbook_bars) |
| 시장 축 샘플러 | `market_sampler.py --interval 60 --out <date>/market`(rrr venv, rrr 토큰 미러 읽기 전용) | 60초 | `market/<HHMMSS>/ka10051.json`, `market/ka90005_merged.json` |
| 1회 | `curl …/api/market-context?bars=0` | 마감 후 | `global_market_context.json` |
| 1회 | ka10099 프로브 | 최초 | `../sector_map_kospi16.json`(종목→업종명) |
| 집계 | `ob_analyze.py`, `whale_analyze.py`, `panel_analyze.py` | 마감 후 | stdout 표, `panel_<date>.jsonl` |
| 다일 채점 | `panel_multi.py --root local/research/orderbook --dates …` | 판정 시 | 09 §3 채점표(사건·일치율·대조군·판정) |
| 기동 | `start_daily.sh {start\|post\|schedule\|universe}` | 장 전/마감 후 | 위 수집·집계를 묶는다 |

종목 범위 = rrr 활성 유니버스(watch-groups 합집합). 패널은 그중 KOSPI 종목만. rs-lead·Redis·rrr-web 은 이 파일들을 읽지 않는다(09 §7·§8).

## 3. 확인 방법 — 싼 것부터

### 3.1 살아 있나 (30초)

```bash
pgrep -fl "ob_sampler.py|market_sampler.py|inbound_rrr.py"
tail -2 local/research/orderbook/<date>/sampler.log
tail -2 local/research/orderbook/<date>/detail/sampler.log
tail -2 local/research/orderbook/<date>/market/sampler.log
```
정상: 프로세스 4개(어댑터 1 + 샘플러 3), tick 줄에 `ok=<종목수> fail=0`, `ka10051=28 ka90005=100 (+N)`.
실패 모양: 프로세스가 빠져 있거나, `fail>0` 이 연속이거나, ka90005 의 `+0` 이 몇 분째 이어짐(새 1분 버킷을 못 받는 것).

### 3.2 원문이 맞나 — 다른 화면과 대조 (5분)

- 큰손: `detail/<HHMMSS>/<sym>.json` → `trade_buckets[ts]["tiers"]` 의 `5억이하`·`5억초과` 행을 rrr-web 체결금액대 패널의 같은 봉과 비교. 건수·금액이 같아야 한다(같은 rrr 원천).
- 시장 투자자: `market/<HHMMSS>/ka10051.json` 의 `종합(KOSPI)` 행 `frgnr_netprps`·`orgn_netprps`(억원) → 키움 HTS 투자자별 매매 당일 누적. 잠정치라 몇 분 차이는 정상, 부호와 규모는 같아야 한다.
- 프로그램: `market/ka90005_merged.json` 의 `all_netprps`(백만원, 음수는 `--`) → HTS 프로그램매매 추이.
- 지수: `global_market_context.json` 슬롯의 `markets.kospi.index.close` → HTS KOSPI 5분봉.

### 3.3 패널 한 줄을 손으로 재구성 (핵심, 15분)

```bash
D=local/research/orderbook/<date>
grep '"sym": "336260", "ts": "<date>T11:00:00"' $D/panel_<date>.jsonl | python3 -m json.tool
```
그 줄의 필드를 원천에서 직접 계산해 맞춘다. 결합 규칙은 09 §1 표.

| 필드 | 직접 계산 |
|---|---|
| `whale_net` | 그 봉 `trade_buckets` 에서 `upper_amount_won > 1억`(또는 null) 구간의 `buy_amount − sell_amount` 합 |
| `whale_ratio` | `whale_net ÷ (모든 구간 buy_amount+sell_amount 합)` |
| `same_bp` | `close(ts) / close(ts−5분) − 1` × 1e4 (candles) |
| `idx_bp` | `global_market_context.json` 의 `slot_start == ts` 슬롯 `kospi.index.return_bp` |
| `same_ex` | `same_bp − idx_bp` |
| `frgn_cum` | `investor_flow` 에서 `as_of ≤ ts+5분` 인 **마지막** 샘플의 `frgnr_buy_eok + frgnr_sell_eok`(매도는 음수로 온다) |
| `mprog_cum` | `ka90005_merged.json` 에서 `cntr_tm ≤ (ts+5분의 HHMMSS)` 인 마지막 행 `all_netprps ÷ 100` |
| `sect_frgn_cum` | `market/*/ka10051.json` 중 `sampled_at ≤ ts+5분` 인 마지막 스냅샷의 해당 업종(`sector_map` 의 `up_name`) 행 `frgnr_netprps` |
| `fwdex6` | `(close(ts+30분)/close(ts) − 1) − (KOSPI(ts+30분)/KOSPI(ts) − 1)` × 1e4 |

확인 포인트: as-of 가 **봉 종료 시각(ts+5분)** 기준인가, 봉 시작(ts) 기준이 아닌가. 결과 `fwd*` 가 신호 봉 종가에서 시작하는가.

### 3.4 검정 로직에서 볼 세 곳 (`scripts/research/panel_analyze.py`)

1. `report()` — `fwd{k}`/`fwdex{k}` 만 결과로 쓴다. `same_bp`·`same_ex` 는 조건에만 쓰인다.
2. `episodes()` — 같은 종목의 연속 신호봉을 첫 봉으로 병합. 이 함수를 끄고 돌리면 사건 수가 크게 늘고 일치율은 비슷하게 남는다(한 종목의 추세 하루가 여러 번 세어지는 부풀림). 문서 숫자는 병합 후 값이다.
3. 정의 상수 및 가설 불일치 조건식 — 09 §2 와 일치해야 한다:
   - 큰손 경계: `WHALE_LOWER = 100_000_000` (1건 1억 원 초과), 개미 경계: `ANT_UPPER = 10_000_000` (1천만 원 이하).
   - **H1 (큰손 흡수)**: `whale_ratio ≥ 0.4` & `same_ex ≤ 0` (단일 5분봉 단위: 1억 초과 체결 40% 이상 집중 매수 & 해당 봉 초과수익 미상승).
   - **H4 (숨은 매집)**: `whale_cum_ratio ≥ 0.2` & `cum_ex < 0` (당일 누적 단위: 개장 후 큰손 누적 순매수 비율 20% 이상 집중 매수 & 주가는 지수 대비 마이너스 약세).
   - **H6 (체결강도 흡수 다이버전스)**: `buy_share ≥ 55%` (체결강도 122% 이상) & `same_ex ≤ 0` (단일 5분봉: 매수 체결 우위인데 주가는 지수 대비 미상승).
   - **H7 (스마트머니 체결 괴리)**: `whale_buy_share ≥ 60%` & `ant_buy_share ≤ 40%` (단일 5분봉: 큰손 매수 우위 vs 개미 매도 우위 양극화).

08 쪽은 `ob_analyze.py` 의 `valid()` — 응답 단위 `confidence` 를 쓰지 않고 봉 자체(weakness 비어 있음·n≥50·partial 아님)로 유효를 판정한다(08 §5-1 의 gap 함정).

### 3.5 문서 숫자를 재현 (마감 후)

```bash
D=local/research/orderbook/<date>
python3 scripts/research/panel_analyze.py --out $D --date <date>
python3 scripts/research/ob_analyze.py    --out $D --date <date>
python3 scripts/research/whale_analyze.py --out $D --date <date>
```
출력이 09 §0·§4, 08 §2~§4 의 숫자와 같아야 한다. 다르면 (a) 스냅샷이 문서 작성 시점 이후 더 쌓였거나(정상, 표본 범위가 문서에 적힌 시각과 다름) (b) 코드가 바뀐 것이다. `git log -- scripts/research docs/08* docs/09*` 로 가른다.

### 3.6 독립 대조 — 착시·조작 검증

- `local/packets/<ts>-<sym>.json`: rs-lead 가 그 시각에 실제로 pull 한 market-context. 같은 봉의 큰손·캔들은 내 스냅샷과 **동일**해야 한다(같은 rrr). 투자자·confidence 는 조회 시각 차이만큼 다를 수 있다.
- `local/ledger.jsonl` 의 decision rationale: rs-lead 가 그 봉을 어떻게 읽었는지(체결 축만). 패널의 같은 봉 값과 서술이 모순되면 어느 한쪽의 결합이 틀린 것이다.

### 3.7 실패하면 무슨 출력인가 — 미리 적어 둔 것

| 검사 | 실패의 모양 |
|---|---|
| 3.1 | 프로세스 누락 / `fail>0` 연속 / `+0` 연속 |
| 3.2 | 큰손 건수·금액이 rrr-web 과 다름(원천 불일치 → 스냅샷 시각 또는 종목 혼동) |
| 3.3 | 필드 하나라도 직접 계산과 다름 → 결합 규칙 오류. 특히 as-of 를 봉 시작으로 잡으면 `frgn_cum`·`mprog_cum` 이 한 봉 앞선 값이 된다 |
| 3.4 | `episodes()` 없이도 사건 수가 같음 → 병합이 안 걸린 것 / 상수가 문서와 다름 |
| 3.5 | 같은 스냅샷인데 숫자가 다름 → 코드 변경 |
| 3.6 | 같은 봉 큰손이 패킷과 다름 → 종목·날짜 혼동 |

## 4. 알고 봐야 할 함정

- `confidence=gap` 은 응답 단위(rrr 계약)라 `bars=0` 에서 전날 결손 한 칸이 그 종목의 오늘 봉 전부를 `gap` 으로 만든다. 분석은 이 플래그를 쓰지 않는다(08 §5-1).
- 종목 투자자(ka10059)는 거래소 발표가 20분~2시간 간격이라 봉에 붙는 값이 그만큼 stale 하다. 당일 누적의 부호로만 쓴다.
- 업종·시장 투자자(ka10051)는 이력이 없는 스냅샷이라 수집 시작 이후만 있다.
- 장중 투자자 수치는 잠정치라 뒤로 가는 점프가 있을 수 있다. 패널은 "봉 끝 시점에 발표돼 있던 값"이다.
- 사건 수가 통과선(30)에 못 미치는 동안의 일치율은 모양이지 결론이 아니다.

## 5. 채점 체크리스트

날짜: ________  검사자: ________  표본 범위(첫 봉~마지막 봉): ________

각 항목은 PASS / FAIL / N/A 중 하나. FAIL 이 하나라도 있으면 그 날의 집계 숫자는 문서에 올리지 않는다.

### 5.1 수집 (3.1·3.2)

| # | 항목 | 기준 | 결과 | 비고 |
|---|---|---|---|---|
| C1 | 샘플러 3종이 장중 내내 살아 있었다(어댑터는 rs-lead 운영 항목이며 데이터 게이트가 아니다) | 각 `sampler.log` 의 tick 이 주기대로 끊김 없음 | | |
| C2 | detail 스냅샷이 유니버스 전 종목을 담았다 | 마지막 tick `ok=<유니버스 수> fail=0` | | |
| C3 | ka10051 스냅샷이 1분 간격으로 있다 | `market/<HHMMSS>` 디렉터리 간격 ≈60초, 각 28행 | | |
| C4 | ka90005 병합이 09:00~15:30 을 빈 분 없이 덮는다 | `ka90005_merged.json` 키 수 ≥ 390, 연속 분 결손 0 | | |
| C5 | 큰손 원문이 rrr-web 과 같다 | 임의 2봉의 5억이하·5억초과 건수·금액 일치 | | |
| C6 | 시장 투자자·프로그램 원문이 HTS 와 같다 | 부호·규모 일치(잠정치 몇 분 차이는 허용) | | |
| C7 | global_market_context.json 이 마감 후 받아졌다 | `bars=0`, 09:00~15:30 슬롯 존재 | | |

### 5.2 결합 (3.3)

| # | 항목 | 기준 | 결과 | 비고 |
|---|---|---|---|---|
| J1 | 큰손 필드가 직접 계산과 같다 | `whale_net`·`whale_ratio` 임의 2줄 일치 | | |
| J2 | 가격·지수 필드가 직접 계산과 같다 | `same_bp`·`idx_bp`·`same_ex` 임의 2줄 일치 | | |
| J3 | 투자자 as-of 가 봉 종료 기준이다 | `frgn_cum` = `as_of ≤ ts+5분` 마지막 샘플 | | |
| J4 | 프로그램 as-of 가 봉 종료 기준이다 | `mprog_cum` = `cntr_tm ≤ ts+5분` 마지막 행 ÷100 | | |
| J5 | 업종 as-of 가 봉 종료 기준이고 업종명이 맞다 | `sect_frgn_cum` = 해당 `up_name` 행 | | |
| J6 | 결과가 신호 봉 종가에서 시작한다 | `fwd6` = close(ts+30분)/close(ts) − 1 | | |
| J7 | 초과수익률이 KOSPI 로 계산됐다 | `fwdex6` = `fwd6` − KOSPI 같은 구간 수익률 | | |
| J8 | KRX 정규장 종가 초과수익률이 맞다 | `fwdex_krx` = close(15:30)/close(ts) − 1 − KOSPI 마감수익률 | | |
| J9 | NXT 야간 종가 초과수익률이 맞다 | `fwdex_nxt` = close(20:00)/close(ts) − 1 − KOSPI 마감수익률 | | |

### 5.3 검정 로직 (3.4)

| # | 항목 | 기준 | 결과 | 비고 |
|---|---|---|---|---|
| L1 | 같은 봉 수익률이 결과에 없다 | `report()` 가 `fwd*` 만 읽음 | | |
| L2 | 사건 병합이 작동한다 | `episodes()` 를 끄면 사건 수가 늘어남을 확인 | | |
| L3 | 정의 상수가 문서 §2 와 같다 | 0.4 / 0.2 / 1억 / 1천만 / 대조군 분위수 매칭 | | |
| L4 | 08 의 봉 유효 판정이 응답단위 gap 을 무시한다 | `valid()` 가 weakness·n·partial 만 봄 | | |

### 5.4 재현·대조 (3.5·3.6)

| # | 항목 | 기준 | 결과 | 비고 |
|---|---|---|---|---|
| R1 | 집계 스크립트 재실행 값이 문서 숫자와 같다 | 09 §0·§4, 08 §2~§4 | | 표본 범위가 다르면 비고에 적고 N/A |
| R2 | rs-lead 패킷의 같은 봉 큰손·캔들이 스냅샷과 같다 | `local/packets` 임의 1건 | | |
| R3 | 원장 rationale 과 패널 값이 모순되지 않는다 | 같은 봉 1건 | | |

### 5.5 가설 채점 (09 §3 — 5거래일 누적 후에만)

| 가설 | 사건 수 | 6봉 일치율(bp) | 12봉 일치율(bp) | KRX 종가(bp) | NXT 종가(bp) | 개미 대조군 | 판정(통과/미달/보류) |
|---|---|---|---|---|---|---|---|
| H1 ① 큰손 흡수 (봉단위: R≥0.4 & same_ex≤0) | | | | | | | |
| H2 큰손 흡수 & 외국인 반대 (H1 & frgn_cum<0) | | | | | | | |
| H3 큰손 흡수 & 기관 동행 (H1 & orgn_cum>0) | | | | | | | |
| H4 ② 숨은 매집 (당일누적: cumR≥0.2 & cum_ex<0) | | | | | | | |
| H5 이례도 되돌림 (\|whale_z\|≥1.5, 대형/중형) | | | | | | | |
| H6 체결강도 흡수 다이버전스 (buy_share≥55% & same_ex≤0) | | | | | | - | |
| H7 스마트머니 체결 괴리 (whale≥60% & ant≤40%) | | | | | | - | |
| H0 축 단독 | | | | | | | |

통과 조건은 09 §3. 사건 수가 통과선에 못 미치면 판정은 "보류"이고 일치율은 적지 않는다(모양을 결론으로 읽지 않기 위해).
`panel_multi.py` 가 이 표 및 시총 규모별(대형주 vs 중형주) 층화 채점표를 찍는다. H1 의 대조군 기준은 09 §3.1 에서 고른 것을 쓴다.

### 5.6 종합

- [ ] 5.1~5.4 전부 PASS 또는 N/A → 그 날 숫자를 09 §4 / 08 에 올려도 된다
- [ ] 5.5 판정이 난 가설이 있다 → 승격 여부는 09 §7-3 의 절차로, 운영자가 결정
- [ ] FAIL 항목 → 원인·조치·재검 날짜: ________

