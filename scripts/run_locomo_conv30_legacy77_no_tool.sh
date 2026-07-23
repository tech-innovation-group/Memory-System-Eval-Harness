#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Run LoCoMo conv-30 with legacy-77 settings but no Agent memory tool calls.

Usage:
  scripts/run_locomo_conv30_legacy77_no_tool.sh [output-directory]

Optional environment overrides:
  LOCOMO_DATASET, ECHOMEM_ROOT, ECHOMEM_WORKSPACE, ECHOMEM_BASE_URL,
  ECHOMEM_ACCOUNT, ECHOMEM_AUTH_FILE, ECHOMEM_AUTH_KEY,
  MODEL_BASE_URL, ANSWER_MODEL, JUDGE_MODEL, LOCOMO_JUDGE_TOKEN.

This is QA-only. It reuses existing memory and does not import or write memory.
Initial EchoMemory HTTP retrieval remains enabled and is injected into the
answer prompt; only iterative Agent tool calls are disabled.
EOF
  exit 0
fi

exec "$ROOT/scripts/_run_locomo_conv30_legacy77.sh" no-tool "${1:-}"
