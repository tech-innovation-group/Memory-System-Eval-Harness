#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 5 ]]; then
  echo "usage: $0 RUN_DIR CSV TITLE INTERVAL_SECONDS MIRROR_OUTPUT [MIRROR_OUTPUT ...]" >&2
  exit 2
fi

RUN_DIR="$1"
CSV_PATH="$2"
TITLE="$3"
INTERVAL="$4"
shift 4
MIRRORS=("$@")

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RENDER_SCRIPT="$SCRIPT_DIR/render_generic_benchmark_live_report.py"
REPORT_PATH="$RUN_DIR/report.html"

while true; do
  python3 "$RENDER_SCRIPT" \
    --run-dir "$RUN_DIR" \
    --csv "$CSV_PATH" \
    --title "$TITLE"

  if [[ -f "$REPORT_PATH" ]]; then
    for target in "${MIRRORS[@]}"; do
      mkdir -p "$(dirname "$target")"
      cp "$REPORT_PATH" "$target"
    done
  fi

  sleep "$INTERVAL"
done
