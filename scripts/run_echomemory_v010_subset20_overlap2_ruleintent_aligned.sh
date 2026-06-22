#!/bin/zsh
set -euo pipefail

ROOT="/Users/chx/locomo-eval-web"
ECHO_ROOT="/Users/chx/Code/echomemory/echo_memory_v010"
PY="$ECHO_ROOT/.venv/bin/python"
RUN_DIR="${1:-$ROOT/runs/echomemory_v010_subset20_overlap2_ruleintent_aligned_20260617}"
WORKSPACE="${2:-/private/tmp/echomemory_v010_subset20_overlap2_ruleintent_aligned_20260617}"
ACCOUNT="${3:-echomemory-v010-subset20-overlap2-ruleintent-aligned}"
DATASET="$ROOT/dataset/locomo10.json"
SUBSET="$ROOT/configs/echomemory_mm_locomo_conv30_formal_subset20_20260614.json"
BASELINE_MANIFEST="$ROOT/runs/echomemory_v010_subset20_baseline_20260615/subset20_manifest.json"

mkdir -p "$RUN_DIR"
LOG_PATH="$RUN_DIR/subset20_import.log"
exec >>"$LOG_PATH" 2>&1
export RUN_DIR WORKSPACE ACCOUNT DATASET SUBSET ECHO_ROOT PY BASELINE_MANIFEST

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

QUESTIONS="$("$PY" - <<'PY'
import json
import os
from pathlib import Path

subset = Path(os.environ["SUBSET"])
data = json.loads(subset.read_text(encoding="utf-8"))
print(",".join(data.get("question_ids") or []))
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

# Current best chain, kept fixed:
export ECHOMEM_AUTO_FLUSH_ON_MESSAGE_PERSISTED="false"
export ECHOMEM_SEARCH_INTENT_LLM_FIRST="false"
export ECHOMEM_SEARCH_INTENT_LLM_FALLBACK="false"
export ECHOMEM_SEARCH_INTENT_BACKEND="rule"

"$PY" - <<'PY'
import json
import os
from pathlib import Path

run_dir = Path(os.environ["RUN_DIR"])
payload = {
    "dataset": os.environ["DATASET"],
    "subset": os.environ["SUBSET"],
    "run_dir": os.environ["RUN_DIR"],
    "workspace": os.environ["WORKSPACE"],
    "account": os.environ["ACCOUNT"],
    "user_id": "default",
    "agent_id": "default",
    "resolved_python_bin": os.environ["PY"],
    "env": {
        "ECHOMEM_AUTO_FLUSH_ON_MESSAGE_PERSISTED": os.environ["ECHOMEM_AUTO_FLUSH_ON_MESSAGE_PERSISTED"],
        "ECHOMEM_SEARCH_INTENT_LLM_FIRST": os.environ["ECHOMEM_SEARCH_INTENT_LLM_FIRST"],
        "ECHOMEM_SEARCH_INTENT_LLM_FALLBACK": os.environ["ECHOMEM_SEARCH_INTENT_LLM_FALLBACK"],
        "ECHOMEM_SEARCH_INTENT_BACKEND": os.environ["ECHOMEM_SEARCH_INTENT_BACKEND"],
    },
    "notes": [
        "Single-change experiment from the current best chain.",
        "Keep auto_flush_off + rule-only search intent + aligned QA config fixed.",
        "Only change under test: RawAtomExtractor _SUBSESSION_OVERLAP = 2.",
    ],
}
(run_dir / "subset20_manifest.json").write_text(
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
  --questions "$QUESTIONS" \
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
  --score-threshold 0.1 \
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
