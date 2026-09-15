#!/bin/bash
# rrr-swing 기동 — herdr pane 하나를 역할 세션(rs-lead / rs-exec)으로 바꾼다.
#
#   bin/start.sh --role lead              # rs-lead: 시장 판단·오케스트레이션. 인바운드 어댑터 + 런타임
#   bin/start.sh --role exec              # rs-exec: 기술적 대응·주문 집행. 런타임만(어댑터 0)
#   bin/start.sh --role exec --runtime agy # rs-exec 를 agy 로. 전제: agy 에 kiwoom-gw MCP·스킬
#                                          #   (docs/02-runbook.md §1-6 — 없으면 조용히 주문만 안 된다)
#   bin/start.sh --check-only             # config 값 점검만(기동 0, 네트워크 0). gw 도달은 실기동에서 본다
#   bin/start.sh --adapter-only           # 인바운드 어댑터만 띄우고 끝(관측용)
#   bin/start.sh --role exec --dry-launch # 점검·어댑터 없이 런타임 명령만 exec(스텁 테스트)
#
# 이 스크립트가 지키는 것:
# - 역할은 herdr 에이전트 이름으로 나눈다. 런타임은 껍데기이며 계약은 AGENTS.md 에 있다.
# - lead 는 claude 고정, exec 만 런타임을 고른다. lead 의 기억은 --append-system-prompt-file
#   로 들어가는데(docs/05-context.md) agy 에 대응 플래그가 없다 — 막지 않으면 기억 없는
#   lead 가 정상 기동한 얼굴로 뜬다.
# - 비밀(봇 토큰·gw 키)은 config 에서 읽어 세션 env 로만 넘긴다. 화면·로그에 남기지 않는다.
# - 인바운드 어댑터는 **하나만** 산다. 겹치면 같은 다이제스트가 두 번 배달된다.
# - 어댑터가 죽어 있으면 런타임을 띄우지 않는다. 눈이 없는 세션은 조용한 장과 구분되지 않는다.
# - 세션은 매번 새로 뜬다. 상태는 local/ 과 원장이 들고 있다.
#
# 흐름: ① 인자·config → ② herdr pane 확정 → ③ 점검 → ④ 어댑터(lead) → ⑤ 부트 프롬프트 → ⑥ exec
set -euo pipefail
cd "$(dirname "$0")/.."

# --- ① 인자와 config -----------------------------------------------------------
RS_TARGET="${RS_TARGET:-}"
RS_CONFIG="${RS_CONFIG:-config/config.json}"
RS_LOCAL="${RS_LOCAL:-local}"
ROLE="lead"; RUNTIME="claude"; ADAPTER_ONLY=0; DELIVER="herdr"; SOURCE=""; CHECK_ONLY=0; DRY_LAUNCH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --role) ROLE="$2"; shift 2 ;;
    --runtime) RUNTIME="$2"; shift 2 ;;      # claude(기본) | agy — exec 한정
    --adapter-only) ADAPTER_ONLY=1; DELIVER="file"; shift ;;
    --deliver) DELIVER="$2"; shift 2 ;;
    --target|--pane) RS_TARGET="$2"; shift 2 ;;   # 배달 대상 herdr 역할 이름
    --source) SOURCE="$2"; shift 2 ;;             # rrr_stream(유일)
    --check-only) CHECK_ONLY=1; shift ;;
    --dry-launch) DRY_LAUNCH=1; shift ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
done
case "$ROLE" in lead|exec) ;; *) echo "ERROR: --role 은 lead|exec (현재: $ROLE)"; exit 2 ;; esac
# codex 는 목록에 없다 — 이 머신의 codex 에는 kiwoom-gw 가 등록돼 있지 않고, 동명
# kiwoom_order_* 가 kiwoom-sdk-mcp 로 해석되어 단일 주문 경로와 gw 키 비활성화
# 급정지를 함께 우회한다. 넣으려면 그 등록부터다.
case "$RUNTIME" in claude|agy) ;; *) echo "ERROR: --runtime 은 claude|agy (현재: $RUNTIME)"; exit 2 ;; esac
if [ "$RUNTIME" = "agy" ] && [ "$ROLE" != "exec" ]; then
  echo "ERROR: --runtime agy 는 --role exec 에서만 쓴다 (현재 role=$ROLE)"
  echo "       lead 의 기억은 --append-system-prompt-file 로 들어가는데 agy 에 대응 플래그가 없다."
  echo "       기억 없는 lead 를 조용히 띄우지 않기 위해 여기서 멈춘다(docs/05-context.md)."
  exit 2
fi

[ -f "$RS_CONFIG" ] || { echo "ERROR: $RS_CONFIG 없음 — cp config/config.example.json config/config.json 후 <YOUR_*> 를 채우시오"; exit 1; }
# config 값 읽기. <PLACEHOLDER> 는 빈 값으로 돌려 미설정과 같이 다룬다.
cfg() { python3 -c "
import json, sys
v = json.load(open(sys.argv[2]))
for k in sys.argv[1].split('.'):
    v = v.get(k, '') if isinstance(v, dict) else ''
print('' if isinstance(v, str) and v.startswith('<') and v.endswith('>') else v)" "$1" "$RS_CONFIG"; }

if [ -z "$RS_TARGET" ]; then
  if [ "$ROLE" = "exec" ]; then
    RS_TARGET="rs-exec"
  else
    RS_TARGET="$(cfg inbound.target)"
    [ -n "$RS_TARGET" ] || RS_TARGET="rs-lead"
  fi
fi

# --- ② herdr pane 확정 ----------------------------------------------------
# 이름이 이미 있기를 요구하면 순환이다: 그 이름이 붙을 에이전트를 만드는 것이 이 스크립트고,
# 에이전트가 없는 pane 은 `herdr agent list` 에 나오지 않는다. 그래서 이름은 ⑥ 이후에 붙인다.
#
# pane 은 포커스가 아니라 **실행 위치**로 정한다. `herdr pane current` 는 포커스된 pane 을
# 돌려줄 뿐이라, 기동 직후 포커스를 옮기면 역할 이름이 엉뚱한 pane 에 붙는다(라이브에서 났다).
# herdr 에 "내 pane" 질의가 없으므로 이 셸의 프로세스 조상으로 찾는다(bin/herdr_pane.py).
# 못 찾으면 포커스 pane 으로 대신하지 않고 멈춘다 — 조용히 틀린 pane 에 붙이는 것보다 낫다.
if [ "${FORCE_OUTSIDE_HERDR:-}" = "1" ]; then
  RS_PANE_ID=""
elif [ -n "${RS_PANE_ID:-}" ]; then
  :                                    # 운영자가 명시한 pane 을 그대로 쓴다
else
  HERDR_BIN="${HERDR_BIN:-herdr}"
  RS_PANE_ID="$(HERDR_BIN="$HERDR_BIN" python3 bin/herdr_pane.py $$ 2>/dev/null || true)"
  if [ -z "$RS_PANE_ID" ]; then
    echo "ERROR: 이 셸이 들어 있는 herdr pane 을 찾지 못했다 — rs 역할 세션은 herdr pane 에서 띄운다."
    echo "       herdr pane 안의 셸에서 실행하시오(포커스가 아니라 실행 위치가 기준이다)."
    echo "       pane 을 직접 지정: RS_PANE_ID=w3:p1 bin/start.sh --role $ROLE"
    echo "       우회(테스트 전용): FORCE_OUTSIDE_HERDR=1 bin/start.sh"
    exit 1
  fi
fi

# --- ③ 점검 --------------------------------------------------------------------
if [ -n "$SOURCE" ] && [ "$SOURCE" != "rrr_stream" ]; then
  echo "ERROR: 텔레그램 신호 소스는 폐기되었습니다. 이벤트 소스는 gw SSE(bin/inbound_rrr.py)뿐입니다 (현재: $SOURCE)"; exit 2
fi
SOURCE="rrr_stream"
OPCH="$(cfg operator_channel)"; [ -n "$OPCH" ] || OPCH="console"
[ -n "$(cfg account_id)" ] || { echo "ERROR: config.account_id 미설정"; exit 1; }
if [ "$OPCH" = "telegram" ]; then
  for k in telegram_bot_token operator_chat_id; do
    [ -n "$(cfg $k)" ] || { echo "ERROR: config.$k 미설정 (operator_channel=telegram)"; exit 1; }
  done
fi
MODE="$(cfg mode)"
case "$MODE" in dry_run|confirm) ;; *) echo "ERROR: config.mode 는 dry_run|confirm (현재: ${MODE:-empty})"; exit 1 ;; esac
if [ "$CHECK_ONLY" = "1" ]; then
  echo "check-only ok mode=$MODE role=$ROLE runtime=$RUNTIME source=$SOURCE operator_channel=$OPCH account=$(cfg account_id)"
  exit 0
fi

# 어댑터가 죽어 있으면 런타임을 띄우지 않는다. ④ 직후와 ⑥ 직전 두 번 부른다 — 그 사이에
# 부트 프롬프트 생성 등이 끼어 있어 그동안 죽을 수 있다. lead 가 아니면 아무것도 하지 않는다.
abort_if_adapter_dead() {
  [ "$ROLE" = "lead" ] || return 0
  [ -n "${RS_ADAPTER_PID:-}" ] || return 0
  kill -0 "$RS_ADAPTER_PID" 2>/dev/null && return 0
  echo "ERROR: 인바운드 어댑터가 $1 종료됐다 — 눈이 없는 세션은 띄우지 않는다."
  echo "--- $RS_LOCAL/inbound.log 마지막 5줄 ---"
  tail -n 5 "$RS_LOCAL/inbound.log" 2>/dev/null || true
  rm -f "$RS_LOCAL/inbound.pid"
  exit 1
}

if [ "$DRY_LAUNCH" != "1" ]; then   # ↓ --dry-launch 는 ③ 후반과 ④ 를 통째로 건너뛴다

# 판단 0 도구들의 selftest. 하나라도 깨지면 세션을 띄우지 않는다 —
# 원장·마크의 무결성이 복기의 분모다.
python3 bin/inbound_core.py --selftest >/dev/null || { echo "ERROR: inbound(core) selftest FAILED"; exit 1; }
python3 bin/inbound_rrr.py --selftest >/dev/null || { echo "ERROR: inbound(rrr) selftest FAILED"; exit 1; }
python3 bin/watchlist.py show >/dev/null || { echo "ERROR: watchlist show FAILED"; exit 1; }
python3 scripts/ledger.py selftest >/dev/null || { echo "ERROR: ledger selftest FAILED"; exit 1; }
python3 scripts/rt_calc.py selftest >/dev/null || { echo "ERROR: rt_calc selftest FAILED"; exit 1; }
python3 scripts/marks.py --selftest >/dev/null || { echo "ERROR: marks selftest FAILED"; exit 1; }
python3 scripts/flags.py --selftest >/dev/null || { echo "ERROR: flags selftest FAILED"; exit 1; }
python3 scripts/review.py --selftest >/dev/null || { echo "ERROR: review selftest FAILED"; exit 1; }
# gw 점검은 이 한 번이다 — 설정 해석(base_url·api_key)과 /api/health 도달을 함께 본다.
python3 bin/inbound_core.py --config "$RS_CONFIG" --check-gw-health || exit 1

if [ "$ROLE" = "lead" ]; then
  # --- ④ 인바운드 어댑터 ---------------------------------------------------------
  # 고아 어댑터만 표적 정리. 대상은 **내가 관리하는 어댑터**뿐이라 두 가지로 좁힌다.
  #   (a) 스크립트가 실행 파일 자리(argv[0]|argv[1])에 있을 것. `pgrep -f` 는 명령줄 어디에든
  #       문자열이 있으면 잡는데, 부트 프롬프트에 어댑터 명령이 적혀 있어 살아 있는
  #       rs-lead 세션까지 대상이 됐다(라이브 확인).
  #   (b) 같은 $RS_LOCAL 을 볼 것. cwd 만 보면 같은 저장소의 **다른 배치**까지 죽인다 —
  #       테스트가 운영 어댑터를 SIGTERM 했다(실제로 그랬다).
  HERE="$(pwd -P)"
  RS_WATCH_ARG="--watchlist $RS_LOCAL/watchlist.json"
  for pid in $(ps -axo pid=,args= 2>/dev/null | awk '($2 ~ /bin\/inbound_rrr\.py$/ || $3 ~ /bin\/inbound_rrr\.py$/) {print $1}'); do
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
    case "$(ps -o args= -p "$pid" 2>/dev/null)" in *"$RS_WATCH_ARG"*) same_local=1 ;; *) same_local=0 ;; esac
    if [ "$cwd" = "$HERE" ] && [ "$same_local" = "1" ]; then
      echo "cleanup: 이전 인바운드 어댑터 종료 pid=$pid"
      kill "$pid" 2>/dev/null || true
      # 죽을 때까지 기다린다. 겹치면 같은 다이제스트가 두 번 들어간다.
      for _ in $(seq 1 "${RS_CLEANUP_TRIES:-50}"); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
      if kill -0 "$pid" 2>/dev/null; then
        echo "ERROR: 이전 인바운드 어댑터(pid=$pid)가 종료되지 않았다 — 중복 배달을 막기 위해 기동을 중단한다."
        echo "       확인: ps -p $pid ; 필요하면 kill -9 $pid 뒤 다시 실행하시오."
        exit 1
      fi
    fi
  done
  mkdir -p "$RS_LOCAL"

  # --- 인바운드 어댑터 백그라운드 기동 (배달만) --------------------------------------
  # 경로는 전부 $RS_LOCAL 을 따른다. 어댑터 기본값은 저장소의 local/ 로 하드코딩돼 있어,
  # 넘기지 않으면 RS_LOCAL 을 옮겨도 커서와 배달 로그만 진짜 파일에 쓴다(테스트가 운영 로그를
  # 오염시킨다 — 실제로 그랬다).
  ADAPTER="bin/inbound_rrr.py"
  PATHS="--watchlist $RS_LOCAL/watchlist.json --delivery-log $RS_LOCAL/delivery.jsonl --inbox $RS_LOCAL/inbox.jsonl"
  if [ "$DELIVER" = "herdr" ]; then
    nohup python3 "$ADAPTER" --deliver herdr --target "$RS_TARGET" $PATHS >> "$RS_LOCAL/inbound.log" 2>&1 &
  else
    nohup python3 "$ADAPTER" --deliver "$DELIVER" $PATHS >> "$RS_LOCAL/inbound.log" 2>&1 &
  fi
  RS_ADAPTER_PID=$!
  echo "$RS_ADAPTER_PID" > "$RS_LOCAL/inbound.pid"
  echo "adapter=$ADAPTER pid=$RS_ADAPTER_PID source=$SOURCE operator_channel=$OPCH deliver=$DELIVER target=$RS_TARGET mode=$MODE account=$(cfg account_id)"

  # 설정·인증 오류는 즉시 죽는다. 짧게 지켜본 뒤 죽어 있으면 런타임을 띄우지 않는다.
  sleep "${RS_ADAPTER_GRACE_SEC:-2}"
  abort_if_adapter_dead "기동 직후"
  [ "$ADAPTER_ONLY" = "1" ] && exit 0
fi

fi  # ↑ DRY_LAUNCH

# --- ⑤ 부트 프롬프트과 기동 컨텍스트 ------------------------------------------------
# 기억은 $BOOT 에 싣지 않는다. 세 원본(playbook·state·journal)의 최신을 머지해 시스템
# 프롬프트로 넣는다(docs/05-context.md) — 사용자 메시지는 대화가 길어지면 밀려나지만
# 시스템 프롬프트는 압축 대상이 아니다. $BOOT 에는 기동 순서·도구·오늘 경로만 남는다.
# lead 만 기억을 받는다. exec 는 rs-lead 의 지시로 움직인다.
REPORT_TOOL="bin/report.py"; [ "$OPCH" = "telegram" ] && REPORT_TOOL="bin/tg_send.py"
RS_SYSPROMPT=""
if [ "$ROLE" = "lead" ]; then
  TODAY="${RS_TODAY:-$(date +%F)}"
  RS_SYSPROMPT="$RS_LOCAL/system-prompts/$TODAY.md"
  python3 bin/context_load.py --local "$RS_LOCAL" --config "$RS_CONFIG" --out "$RS_SYSPROMPT" >/dev/null || {
    echo "ERROR: 기동 컨텍스트를 만들지 못했다 — 기억 원본을 확인하시오($RS_LOCAL/memory/{playbook,state,journal})."
    echo "       최초 설치라면: bin/init_local.sh"
    exit 1
  }
  MODE_NOTE="mode=$MODE"
  [ "$MODE" = "dry_run" ] && MODE_NOTE="mode=dry_run — 결정과 원장 기록만 하고 주문은 내지 않는다(제출 0). 모드 전환은 운영자가 config.mode 를 바꾸고 재기동할 때만"
  [ "$MODE" = "confirm" ] && MODE_NOTE="mode=confirm — 주문은 gw MCP 주문 도구로 집행하고, 집행 결과는 bin/order.py 로 원장에 기록한다"
  BOOT="세션 기동(role=lead, runtime=$RUNTIME). AGENTS.md 계약을 적용하라. $MODE_NOTE.
기억: 규칙·상태·최근 일지는 시스템 프롬프트에 이미 실려 있다. 오늘 원본은 $RS_LOCAL/memory/{playbook,state,journal}/$TODAY.md 이며(기동 산출물 $RS_LOCAL/system-prompts/ 는 고치지 않는다 — 다음 기동에 덮어써진다), 고치려는 날짜 파일이 없으면 그 층의 최신을 그 이름으로 복사한 뒤 고친다. 상태의 '## 운영자 지시' 절은 운영자·에이전트의 것이다 — 읽고 따르되 지우거나 고치지 않는다.
기동 순서(AGENTS.md §10): ① kiwoom_list_tools 로 브로커 도구 확인 ② 브로커 전제 확인(헬스 체크 + 시세 1건 실조회) ③ 계좌·원장 대사(python3 scripts/ledger.py validate; python3 scripts/ledger.py tail -n 20) ④ 상태 확인(시스템 프롬프트) ⑤ 감시 목록 확인(python3 bin/watchlist.py show — **매번 오늘 봐야 할 종목과 대조하라**. 목록에 없는 종목의 이벤트는 오지 않는다. 고치면 어댑터가 다음 이벤트부터 바로 듣는다) ⑥ 운영자 보고($REPORT_TOOL --text …).
도구: 집행 결과 기록·보고 = python3 bin/order.py --order-ref <dup_key> · 손절 협의 = python3 bin/consult.py propose|pre|agree|decline|status|list · 원장 = python3 scripts/ledger.py append <evt> --json '<obj>' (decision 은 action·event_label·rationale·근거 요약, hold·no_action 도 기록) · 산술 = python3 scripts/rt_calc.py · 결정 패킷 = python3 bin/decision_packet.py <sym> --bar-ts <bar_ts> [--with-momentum] (GET 전용) · 감시 목록 = python3 bin/watchlist.py set|add|drop|show|clear (add·drop 은 나머지를 보존한다) · 보고 = $REPORT_TOOL --text. 거래소는 SOR 고정(dmst_stex_tp=\"SOR\")이며 5요소 중 거래소를 매번 고르지 않는다. 장 종료 시 자동 소멸하므로 마감 전 취소 작업을 두지 않는다. 실제 주문은 gw MCP 주문 도구(kiwoom_order_*)로 내며 집행은 rs-exec 가 맡는다. 회계(marks·flags·review)는 운영자의 크론이 돌리며 이 세션은 읽기만 한다.
이후 입력: 매 턴 앞에 '[턴 컨텍스트]' 한 묶음(mode·운영자 지시·태도·열린 협의 — 상태 파일에서 그때그때 읽는다), '[digest …]' 다이제스트(소스=$SOURCE), '[market-check ts=HH:MM]' 30분 시장 체크(시각뿐 — 시장은 직접 본다), '[watchlist-problem …]' 감시 목록 이상(그 동안 종목 이벤트가 전부 버려진다 — 즉시 bin/watchlist.py 로 고쳐라), 운영자 지시(채널=$OPCH; console 이면 이 pane 직접 입력, telegram 이면 텔레그램 채널), 스케줄, 회계 flag."
else
  MODE_NOTE="mode=$MODE"
  [ "$MODE" = "dry_run" ] && MODE_NOTE="mode=dry_run — 결정과 원장 기록만 하고 주문은 내지 않는다(제출 0)"
  [ "$MODE" = "confirm" ] && MODE_NOTE="mode=confirm — gw MCP 주문 도구로 집행한다"
  BOOT="세션 기동(role=exec, runtime=$RUNTIME). $MODE_NOTE.
역할: 기술적 대응 · 하위 분석 · 주문 집행 (전체 시장 판단은 하지 않는다).
입력: rs-lead 의 집행 지시만 받는다 (다이제스트를 직접 받지 않는다).
주문: gw MCP kiwoom_order_* 로 집행한다. rs-exec 는 사람 채널을 갖지 않으며 운영자와의 대화는 rs-lead 가 맡는다. 거래소는 SOR 고정(dmst_stex_tp=\"SOR\")이며 5요소 중 거래소를 매번 고르지 않는다. 장 종료 시 자동 소멸하므로 마감 전 취소 작업을 두지 않는다.
5요소가 모호하면 기본값으로 채우지 말고 rs-lead 에 되묻는다.
절차: kiwoom-gw skill 절차 준수, 3소스 대사(미체결·체결·상세)를 생략하지 않는다.
원장: 집행 결과는 python3 bin/order.py --order-ref <dup_key> 로 원장에 기록한다."
fi

# --- ⑥ 런타임 기동 --------------------------------------------------------------
# 세션 env. `exec` 는 이 셸을 런타임으로 바꿀 뿐 PID 를 바꾸지 않으므로 env 가 그대로 흐른다.
export RS_ROLE="$ROLE" RS_RUNTIME="$RUNTIME"

# 텔레그램 채널은 claude 플러그인이 맡는다. 봇 토큰은 세션 env 로 넘긴다 — 공유
# ~/.claude/channels/telegram/.env 에 쓰면 다른 시스템의 폴러를 가로챈다(플러그인은 실제
# env 를 그 파일보다 우선한다). 봇 토큰당 폴러는 1개이며 이 봇은 rrr-swing 전용이다.
EXTRA_ARGS=()
if [ "$ROLE" = "lead" ] && [ "$OPCH" = "telegram" ]; then
  export TELEGRAM_BOT_TOKEN="$(cfg telegram_bot_token)"
  EXTRA_ARGS=(--channels plugin:telegram@claude-plugins-official)
fi

# 역할 이름은 **뒤늦게** 붙인다. `herdr agent rename` 은 에이전트가 감지된 뒤에만 먹는데
# (`agent_not_found`), 그 에이전트를 만드는 것이 아래 exec 자신이다. 그래서 백그라운드가
# 에이전트가 뜨기를 기다렸다 붙인다. 어댑터는 봉 마감에 배달하므로 몇 초의 무명 구간은
# 무해하다. 실패해도 기동을 막지 않되 조용히 지나가지 않도록 로그에 남긴다.
if [ -n "$RS_PANE_ID" ]; then
  (
    for _ in $(seq 1 60); do
      if "${HERDR_BIN:-herdr}" agent rename "$RS_PANE_ID" "$RS_TARGET" >/dev/null 2>&1; then
        echo "$(date '+%F %T') herdr agent rename $RS_PANE_ID $RS_TARGET ok" >> "$RS_LOCAL/inbound.log"
        exit 0
      fi
      sleep 1
    done
    echo "$(date '+%F %T') ERROR herdr agent rename $RS_PANE_ID $RS_TARGET 실패 — 어댑터 배달 불가" >> "$RS_LOCAL/inbound.log"
  ) &
fi

# 기억은 시스템 프롬프트로 들어간다(lead 만) — 압축 대상이 아니라 대화가 길어져도 남는다.
[ -n "$RS_SYSPROMPT" ] && EXTRA_ARGS+=(--append-system-prompt-file "$RS_SYSPROMPT")

# MCP 는 운영자가 런타임별로 등록한 것을 그대로 쓴다 — 이 저장소는 MCP 설정을 만들지도
# 좁히지도 않는다. claude 는 user scope(`claude mcp add`), agy 는 ~/.gemini/config/mcp_config.json.
# agy 쪽에 kiwoom-gw 가 없으면 rs-exec 는 **정상 기동한 얼굴로** 뜨고 주문 도구만 없다 —
# 에러가 나지 않는 침묵 실패라 운영자가 등록을 미리 확인해야 한다(docs/02-runbook.md §1-6).
#
# 명령은 어댑터 확인 **전에** 확정한다. 확인과 exec 사이에 분기를 두면 그 창에서 어댑터가
# 죽어도 알 수 없다 — 런타임이 몇 개든 셸을 넘기는 자리는 한 곳이다(test_inbound_exit.py).
#
# macOS 의 /bin/bash 는 3.2 다 — set -u 에서 빈 배열 전개가 unbound 로 죽는다(4.4+ 에서 고쳐진 동작).
# ${A[@]+"${A[@]}"} 는 3.2 에서도 안전하다.
if [ "$RUNTIME" = "agy" ]; then
  # `-i`(--prompt-interactive) 가 claude 의 위치 인자와 동치다: 초기 프롬프트를 실행한 뒤
  # 세션을 유지한다. 프롬프트가 **플래그 값**이라 `--` 구분자가 필요 없다.
  #
  # `--dangerously-skip-permissions`: rs-exec 는 사람이 없는 세션이라 승인 프롬프트가 뜨면
  # 조용히 멈춘다. agy 의 settings.json 이 이미 mcp(*)·command(*) 와일드카드 allow 라
  # 이 플래그가 넓히는 권한은 없고, 막는 것은 정지다.
  #
  # EXTRA_ARGS 를 넘기지 않는다 — 두 생산자(텔레그램 채널·시스템 프롬프트)가 모두
  # `$ROLE = lead` 게이트 안에 있고 agy 는 exec 전용이라 여기서는 항상 비어 있다.
  # exec 쪽에 인자가 생기면 그 인자의 agy 대응물을 여기에 함께 적어야 한다.
  LAUNCH=(agy --dangerously-skip-permissions -i "$BOOT")
else
  # `--` 가 옵션 파싱을 끝내 프롬프트를 위치 인자로 고정한다.
  LAUNCH=(claude ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} -- "$BOOT")
fi

# 마지막 확인 — 이 줄을 지나면 셸이 런타임으로 바뀌어 되돌릴 수 없다.
abort_if_adapter_dead "기동 직전"

exec "${LAUNCH[@]}"
