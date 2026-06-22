#!/bin/zsh
set -euo pipefail

ROOT="/Users/chx/locomo-eval-web"
ECHO_ROOT="/Users/chx/Code/echomemory/echo_memory_v010"
PY="$ECHO_ROOT/.venv/bin/python"
RUN_DIR="${1:?run_dir required}"
WORKSPACE="${2:?workspace required}"
ACCOUNT="${3:?account required}"
WINDOW_TURNS="${4:?window_turns required}"
PENDING_TOKENS="${5:?pending_token_threshold required}"
LABEL="${6:-windowbudget-full81}"
MAX_SESSIONS="${7:-0}"
DATASET="$ROOT/dataset/locomo10.json"
BASELINE_MANIFEST="$ROOT/runs/echomemory_v010_subset20_baseline_20260615/subset20_manifest.json"

mkdir -p "$RUN_DIR"
LOG_PATH="$RUN_DIR/conv30_import.log"
exec >>"$LOG_PATH" 2>&1
export RUN_DIR WORKSPACE ACCOUNT DATASET ECHO_ROOT PY BASELINE_MANIFEST WINDOW_TURNS PENDING_TOKENS LABEL MAX_SESSIONS

TOKEN="$("$PY" - <<'PY'
import json
import os
from pathlib import Path

manifest = Path(os.environ["BASELINE_MANIFEST"])
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

export JUDGE_TOKEN="$TOKEN"
export LOCOMO_JUDGE_TOKEN="$TOKEN"
export DASHSCOPE_API_KEY="$TOKEN"
export JUDGE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export JUDGE_MODEL="deepseek-v4-flash"
export ECHOMEM_CHAT_API_KEY="$TOKEN"
export ECHOMEM_CHAT_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export ECHOMEM_CHAT_MODEL="deepseek-v4-flash"
export ECHOMEM_CHAT_PROVIDER="deepseek"

# Keep the aligned QA chain fixed; only change the extraction window + pending token budget.
export ECHOMEM_SEARCH_INTENT_LLM_FIRST="false"
export ECHOMEM_SEARCH_INTENT_LLM_FALLBACK="false"
export ECHOMEM_SEARCH_INTENT_BACKEND="rule"
export ECHOMEM_ATOM_WINDOW_SIZE="$WINDOW_TURNS"
export ECHOMEM_ATOM_MAX_TOKENS="$PENDING_TOKENS"

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
    },
    "max_sessions": int(os.environ["MAX_SESSIONS"] or 0),
    "notes": [
        "Formal full-conv30 experiment: run the full eligible 81 LoCoMo conv-30 questions.",
        "Eligibility is defined by benchmark_adapter.locomo_jobs(...), which excludes category 5 questions from the 105 raw QA pairs.",
        "Single-change experiment: trigger atom extraction by atom window size + pending token budget.",
        "message.persisted extraction is always enabled; there is no separate auto-flush switch in this experiment.",
        "Import-side flush timeout/retry only waits for async extraction to finish; it is not a trigger condition.",
        "Keep rule-only search intent and aligned QA config fixed.",
        f"Current threshold group: {os.environ['LABEL']} (atom_window_size={os.environ['WINDOW_TURNS']}, atom_max_tokens={os.environ['PENDING_TOKENS']}).",
    ],
}
(run_dir / "conv30_manifest.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
PY

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
  --import-wait-mode full \
  --commit-wait-s 900 \
  --commit-call-timeout-s 900 \
  --flush-call-timeout-s 1800 \
  --flush-attempts 3 \
  --continue-on-session-error

"$PY" "$ROOT/scripts/echomemory_memory_qa.py" \
  --dataset "$DATASET" \
  --out-dir "$RUN_DIR/echomemory_qa_ruleintent_aligned" \
  --sample conv-30 \
  --echomem-root "$ECHO_ROOT" \
  --workspace "$WORKSPACE" \
  --account "$ACCOUNT" \
  --user-id default \
  --agent-id default \
  --prompt-mode vikingboat_lite \
  --answer-base-url "https://dashscope.aliyuncs.com/compatible-mode/v1" \
  --answer-model "deepseek-v4-flash" \
  --answer-token "$TOKEN" \
  --top-k 30 \
  --score-threshold 0.75 \
  --memory-budget-chars 6000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --retrieval-mode search \
  --retrieval-ranker score \
  --retrieval-uri-dedup \
  --search-overview-enrichment \
  --tool-set search_read \
  --tool-search-limit 20 \
  --tool-min-score 0.35 \
  --tool-log-chars 1200 \
  --prefetch-read-count 4 \
  --prefetch-context-chars 5000 \
  --max-iterations 50 \
  --no-vikingboat-tool-loop \
  --no-vikingboat-compat \
  --no-initial-tool-prefetch \
  --fallback-to-one-shot

"$PY" "$ROOT/scripts/local_judge.py" \
  --input "$RUN_DIR/echomemory_qa_ruleintent_aligned/echomemory_memory_qa_results.csv" \
  --base-url "https://dashscope.aliyuncs.com/compatible-mode/v1" \
  --model "deepseek-v4-flash" \
  --token "$TOKEN" \
  --parallel 10 \
  --timeout-s 120 \
  --retries 5

"$PY" "$ROOT/scripts/refresh_trigger_windowbudget_summary.py" \
  --run-dir "$RUN_DIR"
