#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
  cat <<'EOF'
EchoMemory formal TG benchmark helper.

Usage:
  scripts/echomemory_formal_tg_benchmark.sh locomo-import
  scripts/echomemory_formal_tg_benchmark.sh locomo-eval
  scripts/echomemory_formal_tg_benchmark.sh longmemeval-official-eval
  scripts/echomemory_formal_tg_benchmark.sh print-runbook

Required environment variables for LoCoMo:
  ECHOMEM_ROOT
  ECHOMEM_WORKSPACE
  ECHOMEM_ACCOUNT
  ECHOMEM_USER_ID
  ECHOMEM_AGENT_ID
  LOCOMO_DATASET
  RUN_DIR
  ANSWER_BASE_URL
  ANSWER_MODEL
  ANSWER_TOKEN
  JUDGE_BASE_URL
  JUDGE_MODEL
  JUDGE_TOKEN

Required environment variables for LongMemEval official eval:
  LONGMEM_CSV
  LONGMEM_REFERENCE
  RUN_DIR
  JUDGE_BASE_URL
  JUDGE_MODEL
  JUDGE_TOKEN

Optional:
  SAMPLE=conv-30
  TOP_K=30
  SCORE_THRESHOLD=0.1
  USER_MEMORY_BUDGET_CHARS=4000
  AGENT_MEMORY_BUDGET_CHARS=2000
  PROMPT_MODE=vikingboat_lite
  TOOL_SET=vikingboat_default
EOF
}

need_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "[bench] missing required env: $name" >&2
    exit 2
  fi
}

locomo_import() {
  need_env ECHOMEM_ROOT
  need_env ECHOMEM_WORKSPACE
  need_env ECHOMEM_ACCOUNT
  need_env ECHOMEM_USER_ID
  need_env ECHOMEM_AGENT_ID
  need_env LOCOMO_DATASET
  need_env RUN_DIR

  mkdir -p "$RUN_DIR"

  python3 "$ROOT/scripts/echomemory_locomo_import.py" \
    --dataset "$LOCOMO_DATASET" \
    --out-dir "$RUN_DIR/echomemory_import" \
    --echomem-root "$ECHOMEM_ROOT" \
    --workspace "$ECHOMEM_WORKSPACE" \
    --account "$ECHOMEM_ACCOUNT" \
    --user-id "$ECHOMEM_USER_ID" \
    --agent-id "$ECHOMEM_AGENT_ID" \
    --sample "${SAMPLE:-conv-30}" \
    --session-mode locomo \
    --import-wait-mode "${IMPORT_WAIT_MODE:-fast}" \
    --commit-wait-s "${COMMIT_WAIT_S:-8}" \
    --flush-call-timeout-s "${FLUSH_CALL_TIMEOUT_S:-15}" \
    --flush-attempts "${FLUSH_ATTEMPTS:-0}"
}

locomo_eval() {
  need_env ECHOMEM_ROOT
  need_env ECHOMEM_WORKSPACE
  need_env ECHOMEM_ACCOUNT
  need_env ECHOMEM_USER_ID
  need_env ECHOMEM_AGENT_ID
  need_env LOCOMO_DATASET
  need_env RUN_DIR
  need_env ANSWER_BASE_URL
  need_env ANSWER_MODEL
  need_env ANSWER_TOKEN
  need_env JUDGE_BASE_URL
  need_env JUDGE_MODEL
  need_env JUDGE_TOKEN

  python3 "$ROOT/scripts/echomemory_wait_and_eval.py" \
    --import-summary "$RUN_DIR/echomemory_import/echomemory_import_summary.json" \
    --dataset "$LOCOMO_DATASET" \
    --echomem-root "$ECHOMEM_ROOT" \
    --workspace "$ECHOMEM_WORKSPACE" \
    --account "$ECHOMEM_ACCOUNT" \
    --user-id "$ECHOMEM_USER_ID" \
    --agent-id "$ECHOMEM_AGENT_ID" \
    --qa-out-dir "$RUN_DIR/echomemory_qa" \
    --sample "${SAMPLE:-conv-30}" \
    --settle-seconds "${SETTLE_SECONDS:-180}" \
    --stabilize-timeout-seconds "${STABILIZE_TIMEOUT_SECONDS:-300}" \
    --stability-polls "${STABILITY_POLLS:-3}" \
    --poll-seconds "${POLL_SECONDS:-30}" \
    --answer-base-url "$ANSWER_BASE_URL" \
    --answer-model "$ANSWER_MODEL" \
    --answer-token "$ANSWER_TOKEN" \
    --judge-base-url "$JUDGE_BASE_URL" \
    --judge-model "$JUDGE_MODEL" \
    --judge-token "$JUDGE_TOKEN" \
    --prompt-mode "${PROMPT_MODE:-vikingboat_lite}" \
    --tool-set "${TOOL_SET:-vikingboat_default}" \
    --top-k "${TOP_K:-30}" \
    --score-threshold "${SCORE_THRESHOLD:-0.1}" \
    --user-memory-budget-chars "${USER_MEMORY_BUDGET_CHARS:-4000}" \
    --agent-memory-budget-chars "${AGENT_MEMORY_BUDGET_CHARS:-2000}"
}

longmemeval_official_eval() {
  need_env LONGMEM_CSV
  need_env LONGMEM_REFERENCE
  need_env RUN_DIR
  need_env JUDGE_BASE_URL
  need_env JUDGE_MODEL
  need_env JUDGE_TOKEN

  python3 "$ROOT/scripts/longmemeval_official_eval.py" \
    --csv "$LONGMEM_CSV" \
    --reference "$LONGMEM_REFERENCE" \
    --out-dir "$RUN_DIR/longmemeval_eval" \
    --base-url "$JUDGE_BASE_URL" \
    --model "$JUDGE_MODEL" \
    --token "$JUDGE_TOKEN" \
    --parallel "${JUDGE_PARALLEL:-10}"
}

print_runbook() {
  cat "$ROOT/docs/echomemory_formal_benchmark_runbook_20260613.md"
}

cmd="${1:-}"
case "$cmd" in
  locomo-import)
    locomo_import
    ;;
  locomo-eval)
    locomo_eval
    ;;
  longmemeval-official-eval)
    longmemeval_official_eval
    ;;
  print-runbook)
    print_runbook
    ;;
  -h|--help|"")
    usage
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage
    exit 2
    ;;
esac
