#!/usr/bin/env bash
# HANDOFF §5 in one go (run inside WSL 2 from the repo): setup, tests, production build, server smoke checks.
# Usage: bash scripts/verify_wsl.sh            -> writes a report to ~/.cache/captioning_pipeline/verify_wsl.log
set -uo pipefail
cd "$(dirname "$0")/.."
LOG="${CP_CACHE_DIR:-$HOME/.cache/captioning_pipeline}/verify_wsl.log"
mkdir -p "$(dirname "$LOG")"
: > "$LOG"
status=0
run() {
  local name="$1"; shift
  echo "== $name" | tee -a "$LOG"
  if "$@" >>"$LOG" 2>&1; then echo "   OK" | tee -a "$LOG"; else echo "   FAILED (see $LOG)" | tee -a "$LOG"; status=1; fi
}
grep -qi microsoft /proc/version 2>/dev/null || echo "warning: this does not look like WSL" | tee -a "$LOG"

run "setup (uv sync, Node, npm install)" bash scripts/setup.sh
run "python tests" bash scripts/test.sh -q
run "frontend specs" npx ng test --watch=false
run "production build" bash scripts/build_web.sh
run "skills mirror in sync" bash scripts/sync_skills.sh --check

echo "== server smoke test" | tee -a "$LOG"
source scripts/env.sh
uv run uvicorn pipeline.api:app --host 127.0.0.1 --port 8765 >>"$LOG" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
for _ in $(seq 1 60); do curl -sf -o /dev/null http://127.0.0.1:8765/api/settings && break; sleep 1; done
for path in / /api/videos /api/tags /api/rag/status /api/scoring/learned-weights; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8765$path")
  echo "   GET $path -> $code" | tee -a "$LOG"
  [ "$code" = "200" ] || status=1
done
echo "   videos indexed: $(curl -s http://127.0.0.1:8765/api/videos | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("videos", [])))')" | tee -a "$LOG"
uv run python scripts/audit_captions.py >>"$LOG" 2>&1 && echo "   caption audit written to the log" | tee -a "$LOG"

echo
if [ $status -eq 0 ]; then echo "ALL CHECKS PASSED - next: the manual golden path + image tagging (HANDOFF §5 items 2-4)"; else echo "SOME CHECKS FAILED - paste $LOG"; fi
exit $status
