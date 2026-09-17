#!/bin/bash
# 연구 수집기 하루치 기동/마감 — docs/09 §5. 판단 0, 읽기 전용.
#   start_daily.sh            : 장 전 실행. 유니버스(gw watch-groups 합집합)를 받아 샘플러 3종을 detach 로 띄운다(15:45 자동 종료).
#   start_daily.sh post       : 마감 후 실행. 글로벌 market-context(bars=0)를 받고 집계 3종을 돌려 report_*.txt 를 남긴다.
#   start_daily.sh schedule   : 오늘 08:50 에 start, 15:50 에 post 를 실행하는 원샷 대기 프로세스 2개를 detach 로 띄운다(상시 크론 아님).
#   start_daily.sh universe   : 유니버스 종목 csv 출력(점검용).
set -u
RS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROVIDER_DIR="${PROVIDER_DIR:-$(dirname "$RS")/rrr}"
DATE=${DATE:-$(date +%F)}
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
    python3 bin/detach.py python3 scripts/research/ob_sampler.py --symbols "$SYMS" --interval 300 --until 15:45 --since "${SINCE}-0" --out "$OUT" >> "$OUT/sampler.out" 2>&1 &
    python3 bin/detach.py python3 scripts/research/ob_sampler.py --mode detail --symbols "$SYMS" --interval 300 --until 15:45 --out "$OUT/detail" >> "$OUT/detail/sampler.out" 2>&1 &
    cd "$PROVIDER_DIR" && ( set -a; . ./.env; set +a; python3 "$RS/bin/detach.py" ./.venv/bin/python "$RS/scripts/research/market_sampler.py" --interval 60 --until 15:45 --out "$OUT/market" >> "$OUT/market/sampler.out" 2>&1 & )
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
  schedule)
    mkdir -p "$OUT"
    now=$(date +%s)
    t_start=$(date -j -f "%Y-%m-%d %H:%M" "$DATE 08:50" +%s); t_post=$(date -j -f "%Y-%m-%d %H:%M" "$DATE 15:50" +%s)
    d1=$((t_start-now)); d2=$((t_post-now))
    [ "$d1" -gt 0 ] && python3 "$RS/bin/detach.py" bash -c "sleep $d1; DATE=$DATE $RS/scripts/research/start_daily.sh start" >> "$OUT/start_daily.log" 2>&1 &
    [ "$d2" -gt 0 ] && python3 "$RS/bin/detach.py" bash -c "sleep $d2; DATE=$DATE $RS/scripts/research/start_daily.sh post" >> "$OUT/start_daily.log" 2>&1 &
    echo "$(date +%T) scheduled start in ${d1}s, post in ${d2}s" | tee -a "$OUT/start_daily.log"
    ;;
  universe) universe;;
  *) echo "usage: start_daily.sh [start|post|schedule|universe]"; exit 2;;
esac
