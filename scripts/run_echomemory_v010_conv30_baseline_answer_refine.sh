#!/bin/zsh
set -euo pipefail

ROOT="/Users/chx/locomo-eval-web"
ECHO_ROOT="/Users/chx/Code/echomemory/echo_memory_v010"
PY="$ECHO_ROOT/.venv/bin/python"
RUN_DIR="${1:-$ROOT/runs/echomemory_v010_conv30_baseline_answer_refine_$(date +%Y%m%d_%H%M%S)}"
WORKSPACE="${2:-/private/tmp/echomemory_v010_trigger_full81_window12_token2304_rerun_20260617_173609}"
ACCOUNT="${3:-echomemory-v010-trigger-full81-window12-token2304-rerun-20260617_173609}"
DATASET="$ROOT/dataset/locomo10.json"
BASELINE_MANIFEST="$ROOT/runs/echomemory_v010_subset20_baseline_20260615/subset20_manifest.json"

mkdir -p "$RUN_DIR"
LOG_PATH="$RUN_DIR/conv30_answer_refine.log"
exec >>"$LOG_PATH" 2>&1
export RUN_DIR WORKSPACE ACCOUNT DATASET ECHO_ROOT PY BASELINE_MANIFEST

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
    "reused_import_workspace": True,
    "notes": [
        "Full conv-30 QA-only experiment on the current recommended 12/2304 full81 imported workspace.",
        "Single change from the recommended baseline: enable --answer-refinement.",
        "No local segment fallback, no extra tool loop, no import-side changes.",
        "This isolates the RMM-lite hypothesis: can one evidence-based revision improve answer synthesis on the same retrieval results?",
    ],
}
(run_dir / "conv30_manifest.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
PY

"$PY" "$ROOT/scripts/echomemory_memory_qa.py" \
  --dataset "$DATASET" \
  --out-dir "$RUN_DIR/echomemory_qa_answer_refine" \
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
  --answer-refinement \
  --fallback-to-one-shot

"$PY" "$ROOT/scripts/local_judge.py" \
  --input "$RUN_DIR/echomemory_qa_answer_refine/echomemory_memory_qa_results.csv" \
  --base-url "https://dashscope.aliyuncs.com/compatible-mode/v1" \
  --model "deepseek-v4-flash" \
  --token "$TOKEN" \
  --parallel 10 \
  --timeout-s 120 \
  --retries 5
