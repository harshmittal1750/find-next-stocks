#!/usr/bin/env bash
# Chain stages 2-5 over the >=1000cr universe. Run after build_universe.py (stage 1).
# Each stage's scripts save incrementally; re-run any stage on its own if rate-limited.
set -u
cd "$(dirname "$0")"
PY="$(cd .. && pwd)/.venv/bin/python"
log(){ echo "[$(date '+%H:%M:%S')] $*"; }

log "STAGE 2/5 — fundamentals over >=1000cr universe"
"$PY" screen_universe.py || log "stage2 returned non-zero (continuing)"

log "STAGE 3/5 — ownership layer (delivery % + deals + FPI)"
"$PY" ownership_layer.py || log "stage3 returned non-zero (continuing)"

log "STAGE 4/5 — shareholding layer (promoter/institutional %)"
"$PY" shareholding_layer.py || log "stage4 returned non-zero (continuing)"

log "STAGE 5/5 — final weighted ranking"
"$PY" rank_all.py --top 30 || log "stage5 returned non-zero"

log "PIPELINE DONE"
