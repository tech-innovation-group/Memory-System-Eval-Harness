#!/bin/zsh
set -euo pipefail

ROOT="/Users/chx/locomo-eval-web"
ECHO_ROOT="/Users/chx/Code/echomemory/echo_memory_v010"
PY="$ECHO_ROOT/.venv/bin/python"
STAMP="$(date +%Y%m%d_%H%M%S)"

RUN_DIR="${1:-$ROOT/runs/echomemory_v010_conv30_segment_memory_fastwait_ruleintent_aligned_$STAMP}"
WORKSPACE="${2:-/private/tmp/echomemory_v010_conv30_segment_memory_fastwait_$STAMP}"
ACCOUNT="${3:-echomemory-v010-conv30-segment-memory-fastwait-$STAMP}"
WINDOW_TURNS="${4:-12}"
PENDING_TOKENS="${5:-3072}"
LABEL="${6:-segment-memory-fastwait-full81}"
MAX_SESSIONS="${7:-0}"
SEGMENT_WINDOW="${8:-4}"
SEGMENT_STRIDE="${9:-4}"
SEGMENT_MAX_CHARS="${10:-1400}"

DATASET="$ROOT/dataset/locomo10.json"
BASELINE_MANIFEST="$ROOT/runs/echomemory_v010_subset20_baseline_20260615/subset20_manifest.json"
IMPORT_LOG="$RUN_DIR/conv30_import.log"
EVAL_LOG="$RUN_DIR/conv30_wait_and_eval.log"

mkdir -p "$RUN_DIR"

TOKEN="$("$PY" - <<'PY'
import json
import os
from pathlib import Path

manifest = Path("/Users/chx/locomo-eval-web/runs/echomemory_v010_subset20_baseline_20260615/subset20_manifest.json")
data = json.loads(manifest.read_text(encoding="utf-8"))
eval_cmd = data.get("eval_cmd") or []
token = ""
for index, arg in enumerate(eval_cmd):
    if arg == "--answer-token" and index + 1 < len(eval_cmd):
        token = str(eval_cmd[index + 1] or "")
        break
print(token)
PY
)"

export RUN_DIR WORKSPACE ACCOUNT DATASET ECHO_ROOT PY BASELINE_MANIFEST TOKEN
export JUDGE_TOKEN="$TOKEN"
export LOCOMO_JUDGE_TOKEN="$TOKEN"
export DASHSCOPE_API_KEY="$TOKEN"
export JUDGE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export JUDGE_MODEL="deepseek-v4-flash"
export ECHOMEM_CHAT_API_KEY="$TOKEN"
export ECHOMEM_CHAT_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export ECHOMEM_CHAT_MODEL="deepseek-v4-flash"
export ECHOMEM_CHAT_PROVIDER="deepseek"

export ECHOMEM_SEARCH_INTENT_LLM_FIRST="false"
export ECHOMEM_SEARCH_INTENT_LLM_FALLBACK="false"
export ECHOMEM_SEARCH_INTENT_BACKEND="rule"
export ECHOMEM_ATOM_WINDOW_SIZE="$WINDOW_TURNS"
export ECHOMEM_ATOM_MAX_TOKENS="$PENDING_TOKENS"
export ECHOMEM_SESSION_SEGMENT_ENABLED="true"
export ECHOMEM_SESSION_SEGMENT_WINDOW_SIZE="$SEGMENT_WINDOW"
export ECHOMEM_SESSION_SEGMENT_STRIDE="$SEGMENT_STRIDE"
export ECHOMEM_SESSION_SEGMENT_MAX_CHARS="$SEGMENT_MAX_CHARS"

"$PY" - <<'PY'
import json
import os
from pathlib import Path
import sys

root = Path("/Users/chx/locomo-eval-web/scripts")
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from benchmark_adapter import locomo_jobs  # noqa: E402

run_dir = Path(os.environ["RUN_DIR"])
data = json.loads(Path(os.environ["DATASET"]).read_text(encoding="utf-8"))
jobs, _plans = locomo_jobs(data, None, "conv-30", None)
question_ids = [job.question_id for job in jobs]
payload = {
    "dataset": os.environ["DATASET"],
    "run_dir": os.environ["RUN_DIR"],
    "workspace": os.environ["WORKSPACE"],
    "account": os.environ["ACCOUNT"],
    "user_id": "default",
    "agent_id": "default",
    "resolved_python_bin": os.environ["PY"],
    "sample_id": "conv-30",
    "question_count": len(question_ids),
    "question_ids": question_ids,
    "question_selection_rule": "benchmark_adapter.locomo_jobs(sample='conv-30', question_filter=None), which excludes category == 5 and yields the formal 81 questions.",
    "env": {
        "ECHOMEM_SEARCH_INTENT_LLM_FIRST": os.environ["ECHOMEM_SEARCH_INTENT_LLM_FIRST"],
        "ECHOMEM_SEARCH_INTENT_LLM_FALLBACK": os.environ["ECHOMEM_SEARCH_INTENT_LLM_FALLBACK"],
        "ECHOMEM_SEARCH_INTENT_BACKEND": os.environ["ECHOMEM_SEARCH_INTENT_BACKEND"],
        "ECHOMEM_ATOM_WINDOW_SIZE": os.environ["ECHOMEM_ATOM_WINDOW_SIZE"],
        "ECHOMEM_ATOM_MAX_TOKENS": os.environ["ECHOMEM_ATOM_MAX_TOKENS"],
        "ECHOMEM_SESSION_SEGMENT_ENABLED": os.environ["ECHOMEM_SESSION_SEGMENT_ENABLED"],
        "ECHOMEM_SESSION_SEGMENT_WINDOW_SIZE": os.environ["ECHOMEM_SESSION_SEGMENT_WINDOW_SIZE"],
        "ECHOMEM_SESSION_SEGMENT_STRIDE": os.environ["ECHOMEM_SESSION_SEGMENT_STRIDE"],
        "ECHOMEM_SESSION_SEGMENT_MAX_CHARS": os.environ["ECHOMEM_SESSION_SEGMENT_MAX_CHARS"],
    },
    "max_sessions": int(os.environ.get("MAX_SESSIONS", "0") or 0),
    "notes": [
        "Formal full-conv30 experiment: run the full eligible 81 LoCoMo conv-30 questions.",
        "Eligibility is defined by benchmark_adapter.locomo_jobs(...), which excludes category 5 questions from the 105 raw QA pairs.",
        "Segment-memory experiment wrapper: enable session.segment generation inside SessionService.commit().",
        f"Current threshold group: {os.environ.get('ECHOMEM_ATOM_WINDOW_SIZE')}/{os.environ.get('ECHOMEM_ATOM_MAX_TOKENS')}.",
        f"Segment params: window_size={os.environ.get('ECHOMEM_SESSION_SEGMENT_WINDOW_SIZE')}, stride={os.environ.get('ECHOMEM_SESSION_SEGMENT_STRIDE')}, max_chars={os.environ.get('ECHOMEM_SESSION_SEGMENT_MAX_CHARS')}.",
        "Orchestration uses fast import plus wait_and_eval so QA/judge can proceed after async settling, instead of blocking inside strict full import wait.",
    ],
}
(run_dir / "conv30_manifest.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
PY

{
  echo "$ $PY $ROOT/scripts/echomemory_locomo_import.py --dataset $DATASET --out-dir $RUN_DIR/echomemory_import --echomem-root $ECHO_ROOT --workspace $WORKSPACE --account $ACCOUNT --user-id default --agent-id default --sample conv-30 --session-mode locomo --max-sessions $MAX_SESSIONS --import-wait-mode fast --commit-wait-s 8 --commit-call-timeout-s 300 --flush-call-timeout-s 30 --flush-attempts 0 --defer-artifact-wait --continue-on-session-error"
  "$PY" "$ROOT/scripts/echomemory_locomo_import.py" \
    --dataset "$DATASET" \
    --out-dir "$RUN_DIR/echomemory_import" \
    --echomem-root "$ECHO_ROOT" \
    --workspace "$WORKSPACE" \
    --account "$ACCOUNT" \
    --user-id default \
    --agent-id default \
    --sample conv-30 \
    --session-mode locomo \
    --max-sessions "$MAX_SESSIONS" \
    --import-wait-mode fast \
    --commit-wait-s 8 \
    --commit-call-timeout-s 300 \
    --flush-call-timeout-s 30 \
    --flush-attempts 0 \
    --defer-artifact-wait \
    --continue-on-session-error
} >"$IMPORT_LOG" 2>&1

{
  echo "$ $PY $ROOT/scripts/echomemory_wait_and_eval.py --import-summary $RUN_DIR/echomemory_import/echomemory_import_summary.json --dataset $DATASET --echomem-root $ECHO_ROOT --workspace $WORKSPACE --account $ACCOUNT --user-id default --agent-id default --qa-out-dir $RUN_DIR/echomemory_qa_ruleintent_aligned --sample conv-30 --settle-seconds 180 --stabilize-timeout-seconds 300 --stability-polls 3 --poll-seconds 30 --repair-flush-call-timeout-s 600 --repair-flush-attempts 2 --repair-commit-wait-s 300 --answer-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --answer-model deepseek-v4-flash --answer-token *** --judge-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --judge-model deepseek-v4-flash --judge-token *** --prompt-mode vikingboat_lite --top-k 30 --score-threshold 0.75 --memory-budget-chars 6000 --user-memory-budget-chars 4000 --agent-memory-budget-chars 2000 --retrieval-mode search --retrieval-ranker score --tool-set search_read --tool-search-limit 20 --tool-min-score 0.35 --tool-log-chars 1200 --prefetch-read-count 4 --prefetch-context-chars 5000 --max-iterations 50 --retrieval-uri-dedup --search-overview-enrichment --no-vikingboat-tool-loop --no-vikingboat-compat --no-initial-tool-prefetch --fallback-to-one-shot"
  "$PY" "$ROOT/scripts/echomemory_wait_and_eval.py" \
    --import-summary "$RUN_DIR/echomemory_import/echomemory_import_summary.json" \
    --dataset "$DATASET" \
    --echomem-root "$ECHO_ROOT" \
    --workspace "$WORKSPACE" \
    --account "$ACCOUNT" \
    --user-id default \
    --agent-id default \
    --qa-out-dir "$RUN_DIR/echomemory_qa_ruleintent_aligned" \
    --sample conv-30 \
    --settle-seconds 180 \
    --stabilize-timeout-seconds 300 \
    --stability-polls 3 \
    --poll-seconds 30 \
    --repair-flush-call-timeout-s 600 \
    --repair-flush-attempts 2 \
    --repair-commit-wait-s 300 \
    --answer-base-url "https://dashscope.aliyuncs.com/compatible-mode/v1" \
    --answer-model "deepseek-v4-flash" \
    --answer-token "$TOKEN" \
    --judge-base-url "https://dashscope.aliyuncs.com/compatible-mode/v1" \
    --judge-model "deepseek-v4-flash" \
    --judge-token "$TOKEN" \
    --prompt-mode vikingboat_lite \
    --top-k 30 \
    --score-threshold 0.75 \
    --memory-budget-chars 6000 \
    --user-memory-budget-chars 4000 \
    --agent-memory-budget-chars 2000 \
    --retrieval-mode search \
    --retrieval-ranker score \
    --tool-set search_read \
    --tool-search-limit 20 \
    --tool-min-score 0.35 \
    --tool-log-chars 1200 \
    --prefetch-read-count 4 \
    --prefetch-context-chars 5000 \
    --max-iterations 50 \
    --retrieval-uri-dedup \
    --search-overview-enrichment \
    --no-vikingboat-tool-loop \
    --no-vikingboat-compat \
    --no-initial-tool-prefetch \
    --fallback-to-one-shot
} >"$EVAL_LOG" 2>&1

"$PY" "$ROOT/scripts/refresh_trigger_windowbudget_summary.py" \
  --run-dir "$RUN_DIR" >>"$EVAL_LOG" 2>&1
