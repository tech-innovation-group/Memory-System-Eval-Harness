#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${LOCOMO_ENV_FILE:-$ROOT/.env.local}"
RUN_PREFLIGHT="${RUN_PREFLIGHT:-1}"

cd "$ROOT"

if [[ -f "$ENV_FILE" ]]; then
  echo "Loading env: $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "Env file not found: $ENV_FILE"
  echo "Continuing with current shell environment."
fi

if [[ "$RUN_PREFLIGHT" == "1" ]]; then
  echo
  echo "== Preflight =="
  "$ROOT/preflight.sh"
fi

echo
echo "== Start =="
exec "$ROOT/start.sh"
