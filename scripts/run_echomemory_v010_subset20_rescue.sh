#!/bin/zsh
set -euo pipefail

ROOT="/Users/chx/locomo-eval-web"
ECHO_ROOT="/Users/chx/Code/echomemory/echo_memory_v010"
PY="$ECHO_ROOT/.venv/bin/python"
RUN_DIR="${1:-$ROOT/runs/echomemory_v010_subset20_rescue_20260615}"
WORKSPACE="${2:-/private/tmp/echomemory_v010_subset20_rescue_20260615}"
ACCOUNT="${3:-echomemory-v010-subset20-rescue}"
DATASET="$ROOT/dataset/locomo10.json"
SUBSET="$ROOT/configs/echomemory_mm_locomo_conv30_formal_subset20_20260614.json"
PROTOCOL="$ROOT/configs/echomemory_mm_benchmark_protocol_rescue_20260615.json"
BASELINE_MANIFEST="$ROOT/runs/echomemory_v010_subset20_baseline_20260615/subset20_manifest.json"

mkdir -p "$RUN_DIR"
LOG_PATH="$RUN_DIR/subset20_import.log"
exec >>"$LOG_PATH" 2>&1
export RUN_DIR WORKSPACE ACCOUNT DATASET SUBSET PROTOCOL ECHO_ROOT PY BASELINE_MANIFEST

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

"$PY" - <<'PY'
import json
import os
from pathlib import Path

run_dir = Path(os.environ["RUN_DIR"])
payload = {
    "protocol": os.environ["PROTOCOL"],
    "subset": os.environ["SUBSET"],
    "dataset": os.environ["DATASET"],
    "run_dir": os.environ["RUN_DIR"],
    "workspace": os.environ["WORKSPACE"],
    "account": os.environ["ACCOUNT"],
    "user_id": "default",
    "agent_id": "default",
    "resolved_python_bin": os.environ["PY"],
    "notes": [
        "Engineering rescue run for EchoMemory 0.1.0 after the frozen subset20 baseline timed out.",
        "Tokens are injected through environment variables instead of command-line flags to avoid leaking them into new QA logs.",
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

"$PY" "$ROOT/scripts/echomemory_wait_and_eval.py" \
  --import-summary "$RUN_DIR/echomemory_import/echomemory_import_summary.json" \
  --dataset "$DATASET" \
  --echomem-root "$ECHO_ROOT" \
  --workspace "$WORKSPACE" \
  --account "$ACCOUNT" \
  --user-id default \
  --agent-id default \
  --qa-out-dir "$RUN_DIR/echomemory_qa" \
  --python-bin "$PY" \
  --sample conv-30 \
  --questions "$QUESTIONS" \
  --settle-seconds 180 \
  --stabilize-timeout-seconds 900 \
  --stability-polls 3 \
  --poll-seconds 30 \
  --repair-flush-call-timeout-s 1800 \
  --repair-flush-attempts 2 \
  --repair-commit-wait-s 900 \
  --answer-base-url "https://dashscope.aliyuncs.com/compatible-mode/v1" \
  --answer-model "deepseek-v4-flash" \
  --judge-base-url "https://dashscope.aliyuncs.com/compatible-mode/v1" \
  --judge-model "deepseek-v4-flash" \
  --prompt-mode vikingboat_lite \
  --top-k 30 \
  --score-threshold 0.1 \
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
  --max-iterations 8 \
  --repair-before-qa \
  --vikingboat-tool-loop \
  --no-vikingboat-compat \
  --initial-tool-prefetch \
  --fallback-to-one-shot
