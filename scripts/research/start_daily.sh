#!/bin/bash
# 연구 수집기 하루치 기동/마감 — docs/09 §5. 판단 0, 읽기 전용.
#   start_daily.sh            : 장 전 실행(NXT 08:00 개장 전 07:55). 유니버스를 받아 샘플러 3종을 detach 로 띄운다(20:05 자동 종료).
#   start_daily.sh post       : KRX 정규장 마감 후 실행(15:50). 글로벌 market-context(bars=0)를 받고 1차 패널/집계를 돌려 report_*.txt 를 남긴다.
#   start_daily.sh post_nxt   : NXT 마감 후 실행(20:05). 20:00 NXT 캔들을 게이트웨이에서 사후 동기화하고 패널을 재집계한다.
#   start_daily.sh schedule   : 07:55 start, 15:50 post, 20:05 post_nxt 를 실행하는 원샷 대기 프로세스 3개를 detach 로 띄운다.
#   start_daily.sh universe   : 유니버스 종목 csv 출력(점검용).
set -u
RS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROVIDER_DIR="${PROVIDER_DIR:-$(dirname "$RS")/rrr}"

if [ -z "${DATE:-}" ]; then
  now=$(date +%s)
  t_today_nxt=$(date -j -f "%Y-%m-%d %H:%M" "$(date +%F) 20:05" +%s)
  if [ "${1:-}" = "schedule" ] && [ "$now" -gt "$t_today_nxt" ]; then
    DATE=$(date -v+1d +%F)
  else
    DATE=$(date +%F)
  fi
fi

OUT=$RS/local/research/orderbook/$DATE
GW=$(python3 -c "import json;print(json.load(open('$RS/config/config.json'))['gw']['base_url'])")
KEY=$(python3 -c "import json;print(json.load(open('$RS/config/config.json'))['gw']['api_key'])")

universe() {
  python3 - "$GW" "$KEY" <<'EOF'
import json, sys, urllib.request
gw, key = sys.argv[1], sys.argv[2]
def get(p):
    return json.load(urllib.request.urlopen(urllib.request.Request(gw + p, headers={"X-API-Key": key}), timeout=10))
syms = set()
for g in get("/api/watch-groups"):
    for s in get(f"/api/watch-groups/{g['group_id']}/stocks"):
        syms.add(s.get("symbol") or s.get("code"))
print(",".join(sorted(x for x in syms if x)))
EOF
}

case "${1:-start}" in
  start)
    mkdir -p "$OUT/detail" "$OUT/market"
    SYMS=$(universe)
    SINCE=$(python3 -c "from datetime import datetime,timezone,timedelta; y,m,d=map(int,'$DATE'.split('-')); print(int(datetime(y,m,d,tzinfo=timezone(timedelta(hours=9))).timestamp()*1000))")
    echo "$(date +%T) start date=$DATE symbols=$SYMS" >> "$OUT/start_daily.log"
    cd "$RS"
    python3 bin/detach.py python3 scripts/research/ob_sampler.py --symbols "$SYMS" --interval 300 --until 20:05 --since "${SINCE}-0" --out "$OUT" < /dev/null >> "$OUT/sampler.out" 2>&1 &
    python3 bin/detach.py python3 scripts/research/ob_sampler.py --mode detail --symbols "$SYMS" --interval 300 --until 20:05 --out "$OUT/detail" < /dev/null >> "$OUT/detail/sampler.out" 2>&1 &
    cd "$PROVIDER_DIR" && ( set -a; . ./.env; set +a; python3 "$RS/bin/detach.py" ./.venv/bin/python "$RS/scripts/research/market_sampler.py" --interval 60 --until 20:05 --out "$OUT/market" < /dev/null >> "$OUT/market/sampler.out" 2>&1 & )
    sleep 15; echo "$(date +%T) alive: $(pgrep -fl 'ob_sampler.py|market_sampler.py' | wc -l | tr -d ' ') procs" >> "$OUT/start_daily.log"
    ;;
  post)
    cd "$RS"
    curl -s -m 20 -H "X-API-Key: $KEY" "$GW/api/market-context?bars=0" > "$OUT/global_market_context.json"
    python3 scripts/research/panel_analyze.py --out "$OUT" --date "$DATE" > "$OUT/report_panel.txt" 2>&1
    python3 scripts/research/ob_analyze.py --out "$OUT" --date "$DATE" > "$OUT/report_ob.txt" 2>&1
    python3 scripts/research/whale_analyze.py --out "$OUT" --date "$DATE" > "$OUT/report_whale.txt" 2>&1
    echo "$(date +%T) post done: $(head -1 "$OUT/report_panel.txt")" >> "$OUT/start_daily.log"
    ;;
  post_nxt)
    cd "$RS"
    python3 scripts/research/patch_nxt_candles.py --date "$DATE" --out "$OUT" < /dev/null >> "$OUT/start_daily.log" 2>&1
    python3 scripts/research/panel_analyze.py --out "$OUT" --date "$DATE" > "$OUT/report_panel.txt" 2>&1
    echo "$(date +%T) post_nxt done: $(head -1 "$OUT/report_panel.txt")" >> "$OUT/start_daily.log"
    ;;
  schedule)
    mkdir -p "$OUT"
    now=$(date +%s)
    t_start=$(date -j -f "%Y-%m-%d %H:%M" "$DATE 07:55" +%s)
    t_post=$(date -j -f "%Y-%m-%d %H:%M" "$DATE 15:50" +%s)
    t_nxt=$(date -j -f "%Y-%m-%d %H:%M" "$DATE 20:05" +%s)
    d1=$((t_start-now)); d2=$((t_post-now)); d3=$((t_nxt-now))
    [ "$d1" -gt 0 ] && python3 "$RS/bin/detach.py" bash -c "sleep $d1; DATE=$DATE $RS/scripts/research/start_daily.sh start" < /dev/null >> "$OUT/start_daily.log" 2>&1 &
    [ "$d2" -gt 0 ] && python3 "$RS/bin/detach.py" bash -c "sleep $d2; DATE=$DATE $RS/scripts/research/start_daily.sh post" < /dev/null >> "$OUT/start_daily.log" 2>&1 &
    [ "$d3" -gt 0 ] && python3 "$RS/bin/detach.py" bash -c "sleep $d3; DATE=$DATE $RS/scripts/research/start_daily.sh post_nxt" < /dev/null >> "$OUT/start_daily.log" 2>&1 &
    echo "$(date +%T) scheduled target=$DATE start in ${d1}s, post in ${d2}s, post_nxt in ${d3}s" | tee -a "$OUT/start_daily.log"
    ;;
  universe) universe;;
  *) echo "usage: start_daily.sh [start|post|post_nxt|schedule|universe]"; exit 2;;
esac
