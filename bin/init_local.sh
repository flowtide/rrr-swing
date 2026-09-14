#!/bin/bash
# init_local — local/ 골격과 첫 원본(state·playbook) 준비. 멱등: 있는 파일·디렉터리는 건드리지 않는다. 값 0.
#   bin/init_local.sh [<local-dir>]     # 기본 local. 템플릿은 docs/templates/
#
# 기억은 날짜 층으로 쌓인다(docs/05-context.md): 원본 셋은 local/memory/{playbook,state,journal}/
# 이고, 기동 산출물은 local/system-prompts/ 로 따로 둔다 — 고치는 곳과 만들어지는 곳을 갈라
# 놓아야 산출물을 고치고 다음 기동에 잃지 않는다. 첫 원본만 오늘 날짜로 깔아 주고, 이후 날짜
# 파일은 **고치는 쪽이** 최신을 복사해 만든다 — 안 고친 날은 파일이 생기지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."
LOCAL="${1:-local}"
TODAY="${RS_TODAY:-$(date +%F)}"
created=0; kept=0
for d in memory/playbook memory/state memory/journal system-prompts stories meetings lessons consults packets reports; do
  if [ -d "$LOCAL/$d" ]; then kept=$((kept + 1)); else mkdir -p "$LOCAL/$d"; created=$((created + 1)); fi
done
seed_if_empty() {  # $1 템플릿, $2 디렉터리 — 그 층에 날짜 파일이 하나도 없을 때만 깐다
  if ls "$2"/[0-9]*.md >/dev/null 2>&1; then kept=$((kept + 1)); else cp "$1" "$2/$TODAY.md"; created=$((created + 1)); fi
}
seed_if_empty docs/templates/state.md "$LOCAL/memory/state"
seed_if_empty docs/templates/playbook.md "$LOCAL/memory/playbook"
echo "init_local ok dir=$LOCAL today=$TODAY created=$created kept=$kept (journal/story/meeting 템플릿은 docs/templates/ 를 복사해 쓴다)"
