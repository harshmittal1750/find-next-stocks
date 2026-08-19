#!/usr/bin/env bash
# Entry point launchd runs every Saturday morning. Logs to logs/weekly_<date>.log.
# Run manually any time:  bash scripts/run_weekly.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJ="$(cd "$HERE/.." && pwd)"
cd "$PROJ" || exit 1

mkdir -p logs
TS="$(date +%Y-%m-%d)"
LOG="logs/weekly_${TS}.log"

export PYTHONUNBUFFERED=1   # stream child-script prints live instead of buffering to the end
echo "==== run_weekly $(date) ====" | tee -a "$LOG"
# caffeinate -i keeps the Mac awake during the 1-2h fetch (idle-sleep would interrupt it).
# tee -> you watch progress live in the terminal AND it's saved to the log (launchd has no TTY, harmless there).
caffeinate -i "$PROJ/.venv/bin/python" scripts/weekly_update.py "$@" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
echo "==== exit $rc @ $(date) ====" | tee -a "$LOG"
exit "$rc"
