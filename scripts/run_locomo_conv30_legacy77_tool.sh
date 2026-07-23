#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Run LoCoMo conv-30 with the legacy-77 profile and Agent memory tools enabled.

Usage:
  scripts/run_locomo_conv30_legacy77_tool.sh [output-directory]

Optional environment overrides:
  LOCOMO_DATASET, ECHOMEM_ROOT, ECHOMEM_WORKSPACE, ECHOMEM_BASE_URL,
  ECHOMEM_ACCOUNT, ECHOMEM_AUTH_FILE, ECHOMEM_AUTH_KEY,
  MODEL_BASE_URL, ANSWER_MODEL, JUDGE_MODEL, LOCOMO_JUDGE_TOKEN.

This is QA-only. It reuses existing memory and does not import or write memory.
EOF
  exit 0
fi

exec "$ROOT/scripts/_run_locomo_conv30_legacy77.sh" tool "${1:-}"
