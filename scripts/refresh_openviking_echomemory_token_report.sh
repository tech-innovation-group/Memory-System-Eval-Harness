#!/bin/zsh
set -euo pipefail

ROOT="/Users/chx/locomo-eval-web"
LOG="$ROOT/runs/openviking_echomemory_token_report_refresh.log"
PYTHON_BIN="/usr/bin/python3"

mkdir -p "$ROOT/runs"

{
  printf '[%s] refresh start\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  "$PYTHON_BIN" "$ROOT/scripts/render_echomemory_openviking_token_design_report.py"
  printf '[%s] refresh done\n' "$(date '+%Y-%m-%d %H:%M:%S')"
} >>"$LOG" 2>&1
