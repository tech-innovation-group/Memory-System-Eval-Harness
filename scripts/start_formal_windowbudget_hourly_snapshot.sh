#!/bin/zsh
set -euo pipefail

ROOT="/Users/chx/locomo-eval-web"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SOURCE_HTML="${1:-$ROOT/runs/echomemory_v010_conv30_formal_windowbudget_protocol_20260617_1645.html}"
OUTPUT_DIR="${2:-$ROOT/runs}"
LOG_PATH="${3:-$ROOT/runs/formal_windowbudget_hourly_snapshot.log}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-3600}"

mkdir -p "$OUTPUT_DIR"
touch "$LOG_PATH"

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] hourly snapshot loop started source=$SOURCE_HTML output_dir=$OUTPUT_DIR interval=${INTERVAL_SECONDS}s" >>"$LOG_PATH"

while true; do
  {
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] snapshot begin"
    "$PYTHON_BIN" "$ROOT/scripts/snapshot_trigger_windowbudget_report.py" \
      --source "$SOURCE_HTML" \
      --output-dir "$OUTPUT_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] snapshot ok"
  } >>"$LOG_PATH" 2>&1
  sleep "$INTERVAL_SECONDS"
done
