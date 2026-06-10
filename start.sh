#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${LOCOMO_EVAL_HOST:-127.0.0.1}"
PORT="${LOCOMO_EVAL_PORT:-19181}"

cd "$ROOT"

DEFAULT_ECHOMEM_ROOT="$HOME/Code/echomemory/echo_memory_v006"
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -n "${PYTHON_BIN}" ]]; then
  :
elif [[ -n "${ECHOMEM_PYTHON:-}" ]]; then
  PYTHON_BIN="${ECHOMEM_PYTHON}"
elif [[ -n "${ECHOMEMORY_PYTHON:-}" ]]; then
  PYTHON_BIN="${ECHOMEMORY_PYTHON}"
elif [[ -n "${ECHOMEM_ROOT:-}" && -x "${ECHOMEM_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${ECHOMEM_ROOT}/.venv/bin/python"
elif [[ -x "$DEFAULT_ECHOMEM_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$DEFAULT_ECHOMEM_ROOT/.venv/bin/python"
elif [[ -x "$HOME/openviking-env/bin/python" ]]; then
  PYTHON_BIN="$HOME/openviking-env/bin/python"
else
  PYTHON_BIN="python3"
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use. Set LOCOMO_EVAL_PORT to another port." >&2
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2 || true
  exit 1
fi

echo "LoCoMo Eval Harness"
echo "  URL: http://$HOST:$PORT"
echo "  Root: $ROOT"
echo "  Python: $PYTHON_BIN"
echo
"$PYTHON_BIN" "$ROOT/server.py" --host "$HOST" --port "$PORT"
