# `local/` — 이 계좌가 남기는 것

코드와 계약은 저장소에 있고, **한 계좌의 기억·장부·증거는 전부 `local/` 에 있다.** `.gitignore` 대상이라 어떤 git 에도 들어가지 않는다(AGENTS.md §1-4). 같은 저장소를 여럿이 clone 해 같은 계약으로 쓰되 서로 섞이지 않는 이유가 이것이다.

**백업은 운영자 책임이다.** 코드가 백업하지 않는다.

골격은 `bin/init_local.sh` 가 만든다(멱등 — 있는 것은 건드리지 않는다).

## 네 구역

한 트리지만 손대는 규칙은 구역마다 다르다. **어느 구역인지가 곧 권한이다.**

```
local/
  memory/            ① 기억      고친다
    playbook/  state/  journal/
  stories/           ① 기억      고친다
  lessons/  meetings/ ① 기억      고친다

  ledger.jsonl       ② 장부      추가만 한다. 고치지 않는다

  packets/           ③ 증거      코드가 남긴다. 읽기만 한다
  consults/  reports/ ③ 증거

  system-prompts/    ④ 코드 소유  손대지 않는다
  watchlist.json     ② 세션 소유 — 감시 종목. 어댑터가 이벤트마다 읽는다
  inbound.log  inbound.pid  delivery.jsonl
```

| | 구역 | 쓰는 쪽 | 사람·세션이 고쳐도 되나 |
|---|---|---|---|
| ① | 기억 | 사람 · 에이전트 · 세션 | **그렇다.** 여기가 고치는 자리다 |
| ② | 장부 | `scripts/ledger.py` 만 | **아니다.** append-only. 고치면 진실이 깨진다 |
| ③ | 증거 | 코드가 남긴 원문·기록 | **아니다.** 읽기만 한다 |
| ④ | 코드 소유 | 런타임·어댑터 | **아니다.** 지우면 동작이 바뀐다(아래 표) |

> **`local/memory/` 안은 전부 고쳐도 되고, 그 밖은 아니다.** 기억의 편집 규칙과 날짜 층 구조는 `docs/05-context.md` 가 소유한다 — 여기서 되풀이하지 않는다.

## 무엇이 어디에

| 경로 | 무엇 | 쓰는 쪽 |
|---|---|---|
| `memory/playbook/<날짜>.md` | 규칙 — 한도·손실 정책·진입·청산 기준 | 트레이더(코드는 읽지 않는다) |
| `memory/state/<날짜>.md` | 상태 — 장세·보유·관심·활성 가설. `## 운영자 지시` 절 포함 | 세션(지시 절은 운영자·에이전트) |
| `memory/journal/<날짜>.md` | 일지 — 무엇을 보고 무엇을 했고 무엇이 어긋났나 | 세션(마감 후) |
| `stories/<symbol>.md` | 가설 — thesis·가격 범주·무효화 조건·기간·상태. 깨진 가설도 지우지 않는다 | 세션 / `scripts/flags.py` 가 읽는다 |
| `lessons/` · `meetings/<날짜>.md` | 교훈 · 주간 미팅 기록(전략 변경 합의) | 세션 · 운영자 |
| `ledger.jsonl` | **원장** — 계좌별 append-only JSONL. 결정·집행·협의·회계가 전부 여기 | `scripts/ledger.py append` 만 |
| `positions.jsonl` · `frozen_book.json` | 전 포지션 마크 · 기준선 동결북 | `scripts/marks.py`(운영자 크론) |
| `packets/<ts>-<sym>.json` | 결정 시점 `/context`·`/flow`·`/market-context`(호가, 보조 증거) **원문** | `bin/decision_packet.py` |
| `consults/<id>.json` | 손절·청산 협의 | `bin/consult.py` |
| `reports/<날짜>.md` | 운영자 보고(console 채널) | `bin/report.py` · `bin/order.py` |
| `system-prompts/<날짜>.md` | 기동 컨텍스트 산출물 | `bin/context_load.py`(매 기동 덮어쓴다) |
| `watchlist.json` | 감시 종목 — 어댑터의 유일한 필터. **이벤트마다** 읽으므로 고치면 바로 듣는다 | `bin/watchlist.py` |
| `delivery.jsonl` | 배달 시도 기록(다이제스트·market-check·실패) | 인바운드 어댑터 |
| `inbound.log` · `inbound.pid` | 어댑터 로그 · pid | `bin/start.sh` |

## 지울 때

보존 기간 규칙은 없다. 정리는 **사람이 판단해서** 한다. 지우기 전에 아래를 본다.

| 지우면 | 무슨 일이 |
|---|---|
| `ledger.jsonl` | 성과·결정 이력이 사라진다. **지우지 않는다** |
| `positions.jsonl` · `frozen_book.json` | 성과 기준선이 사라진다. 마크는 과거로 소급되지 않는다 |
| `packets/` | 원장 `decision_ref` 가 없는 파일을 가리킨다 — 원장은 깨지지 않지만(존재를 검사하지 않는다) 복기 때 **원문 대조가 불가능**해진다 |
| `consults/` | 원장 `consult_ref` 가 같은 방식으로 끊긴다 |
| `stories/` | `scripts/flags.py` 가 만료·재검토를 판정할 근거를 잃는다 |
| `watchlist.json` | 목록이 비어 종목 이벤트가 배달되지 않는다(macro 는 온다). 세션이 다시 적어야 한다 |
| `memory/` 의 과거 날짜 파일 | 그날 세션이 무엇을 들고 무엇을 보았는지가 사라진다 |
| `system-prompts/` | 다음 기동에 재생성된다. 그날의 기록만 잃는다 |
| `inbound.log` · `inbound.pid` · 빈 골격 디렉터리 | 동작에 영향 없다. 골격은 `bin/init_local.sh` 가 다시 만든다 |

**크기**: `packets/` 가 가장 빨리 큰다 — 조회 1건에 약 100KB, 활발한 하루에 2MB 안팎. 나머지는 전부 합쳐도 하루 100KB 수준이다. 용량이 문제가 되면 임의로 지우는 대신 보존 기간을 정해 회계 절차(`docs/02-runbook.md` §3)에 넣는다.

## 더 읽을 것

| 무엇 | 어디 |
|---|---|
| 기억이 세션에 들어가는 두 경로 · 날짜 층의 편집 규칙 | `docs/05-context.md` |
| 어디에 무엇을 기록하는가(활동별) | `AGENTS.md` §1 다섯 활동과 산출물 · §7 학습과 기억 |
| 골격 생성·기동 절차 | `docs/02-runbook.md` §1 |
