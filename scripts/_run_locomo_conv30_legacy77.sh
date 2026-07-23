#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
MODE="${1:?internal usage: $0 tool|no-tool [OUT_DIR]}"
OUT_DIR="${2:-}"

case "$MODE" in
  tool)
    TOOL_LOOP_FLAG="--vikingboat-tool-loop"
    RUN_NAME="locomo_conv30_legacy77_tool"
    ;;
  no-tool)
    TOOL_LOOP_FLAG="--no-vikingboat-tool-loop"
    RUN_NAME="locomo_conv30_legacy77_no_tool"
    ;;
  *)
    echo "unsupported mode: $MODE" >&2
    exit 2
    ;;
esac

if [[ -z "$OUT_DIR" ]]; then
  OUT_DIR="$ROOT/runs/${RUN_NAME}_$(date +%Y%m%d_%H%M%S)"
fi

# These defaults reproduce the existing PR159 conv-30 workspace. Override the
# environment variables when running against another EchoMemory checkout.
ECHOMEM_ROOT="${ECHOMEM_ROOT:-$HOME/Code/echomemory/EchoMem_refactor_pr159_20260723}"
ECHOMEM_WORKSPACE="${ECHOMEM_WORKSPACE:-$ECHOMEM_ROOT/echo_workspace_locomo_conv30_legacy77}"
LOCOMO_DATASET="${LOCOMO_DATASET:-$HOME/Workspace-Groups/LoCoMo/locomo-eval-web/dataset/locomo10.json}"
ECHOMEM_BASE_URL="${ECHOMEM_BASE_URL:-http://127.0.0.1:18091}"
ECHOMEM_ACCOUNT="${ECHOMEM_ACCOUNT:-default}"
ECHOMEM_AUTH_FILE="${ECHOMEM_AUTH_FILE:-$ECHOMEM_WORKSPACE/.echomem_http_auth_keys.json}"
ECHOMEM_CONFIG="${ECHOMEM_CONFIG:-$ECHOMEM_WORKSPACE/config.json}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

require_file() {
  local path="$1"
  local label="$2"
  if [[ ! -f "$path" ]]; then
    echo "$label not found: $path" >&2
    exit 2
  fi
}

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  echo "Python executable not found: $PYTHON_BIN" >&2
  exit 2
}
command -v jq >/dev/null 2>&1 || {
  echo "jq is required to read EchoMemory configuration." >&2
  exit 2
}

require_file "$LOCOMO_DATASET" "LoCoMo dataset"
require_file "$ECHOMEM_CONFIG" "EchoMemory config"
require_file "$ECHOMEM_AUTH_FILE" "EchoMemory HTTP auth file"

if [[ ! -d "$ECHOMEM_ROOT" ]]; then
  echo "EchoMemory repository not found: $ECHOMEM_ROOT" >&2
  exit 2
fi

if [[ -z "${ECHOMEM_AUTH_KEY:-}" ]]; then
  ECHOMEM_AUTH_KEY="$(
    jq -er --arg account "$ECHOMEM_ACCOUNT" \
      '[.entries[] | select(.account == $account)][0].auth_key' \
      "$ECHOMEM_AUTH_FILE"
  )"
fi

MODEL_BASE_URL="${MODEL_BASE_URL:-$(jq -er '.model.llm.api_base' "$ECHOMEM_CONFIG")}"
ANSWER_MODEL="${ANSWER_MODEL:-$(jq -er '.model.llm.model' "$ECHOMEM_CONFIG")}"
JUDGE_MODEL="${JUDGE_MODEL:-$ANSWER_MODEL}"

export ECHOMEM_AUTH_KEY
export JUDGE_BASE_URL="$MODEL_BASE_URL"
export JUDGE_MODEL
if [[ -z "${LOCOMO_JUDGE_TOKEN:-}" ]]; then
  model_credential="$(jq -er '.model.llm.api_key' "$ECHOMEM_CONFIG")"
  LOCOMO_JUDGE_TOKEN=$model_credential
fi
export LOCOMO_JUDGE_TOKEN

mkdir -p "$OUT_DIR"

echo "LoCoMo conv-30 evaluation"
echo "  profile:    legacy-77"
echo "  tool calls: $([[ "$MODE" == "tool" ]] && echo enabled || echo disabled)"
echo "  dataset:    $LOCOMO_DATASET"
echo "  workspace:  $ECHOMEM_WORKSPACE"
echo "  server:     $ECHOMEM_BASE_URL"
echo "  output:     $OUT_DIR"
echo "  note:       QA-only; existing memory is reused and no memory is written"

exec "$PYTHON_BIN" "$ROOT/scripts/echomemory_memory_qa.py" \
  --evaluation-profile legacy-77 \
  "$TOOL_LOOP_FLAG" \
  --dataset "$LOCOMO_DATASET" \
  --out-dir "$OUT_DIR" \
  --sample conv-30 \
  --echomem-root "$ECHOMEM_ROOT" \
  --echomem-transport http \
  --echomem-base-url "$ECHOMEM_BASE_URL" \
  --workspace "$ECHOMEM_WORKSPACE" \
  --account "$ECHOMEM_ACCOUNT" \
  --user-id default \
  --agent-id default \
  --identity-mode fixed \
  --prompt-mode vikingbot_agent_aligned \
  --evidence-policy blackbox \
  --retrieval-source-mode echo_http_native \
  --no-initial-tool-prefetch \
  --no-toolloop-rescue-on-toollike-answer \
  --no-answer-refinement \
  --qa-memory-injection \
  --qa-parallelism "${LOCOMO_QA_PARALLELISM:-4}" \
  --judge-every 81 \
  --judge-parallel "${LOCOMO_JUDGE_PARALLEL:-4}" \
  --timeout-s 180 \
  --question-timeout-s 600 \
  --judge-timeout-s 180 \
  --model-retries 5 \
  --judge-retries 5
