#!/bin/zsh
set -euo pipefail

ROOT="/Users/chx/locomo-eval-web"
ECHO_ROOT="/Users/chx/Code/echomemory/echo_memory_v010"
PY="$ECHO_ROOT/.venv/bin/python"
SOURCE_QA_DIR="${1:-$ROOT/runs/echomemory_v010_conv30_segment_memory_fastwait_full81_token3072_fixrerank_probe_20260619_161901/echomemory_qa_ruleintent_aligned}"
RUN_DIR="${2:-$ROOT/runs/echomemory_v010_conv30_full81_segmentlift_rule_refine_20260619}"
DATASET="$ROOT/dataset/locomo10.json"

mkdir -p "$RUN_DIR"
LOG_PATH="$RUN_DIR/full81_segmentlift.log"
exec >>"$LOG_PATH" 2>&1

export SOURCE_QA_DIR RUN_DIR DATASET ROOT ECHO_ROOT PY

EXTRACTED=("${(@f)$("$PY" - <<'PY'
import json
import os
from pathlib import Path

qa_dir = Path(os.environ["SOURCE_QA_DIR"])
status = json.loads((qa_dir / "auto_eval_status.json").read_text(encoding="utf-8"))
cmd = list(status.get("qa_cmd") or [])

def after(flag: str, default: str = "") -> str:
    for index, value in enumerate(cmd):
        if value == flag and index + 1 < len(cmd):
            return str(cmd[index + 1] or "")
    return default

workspace = after("--workspace")
account = after("--account")
token = after("--answer-token")
base_url = after("--answer-base-url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
model = after("--answer-model", "deepseek-v4-flash")

print(workspace)
print(account)
print(token)
print(base_url)
print(model)
PY
)}")

WORKSPACE="${EXTRACTED[1]:-}"
ACCOUNT="${EXTRACTED[2]:-}"
TOKEN="${EXTRACTED[3]:-}"
BASE_URL="${EXTRACTED[4]:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
MODEL="${EXTRACTED[5]:-deepseek-v4-flash}"

if [[ -z "$WORKSPACE" || -z "$ACCOUNT" || -z "$TOKEN" ]]; then
  echo "[fatal] failed to extract workspace/account/token from $SOURCE_QA_DIR/auto_eval_status.json"
  exit 2
fi

export WORKSPACE ACCOUNT TOKEN BASE_URL MODEL

export JUDGE_TOKEN="$TOKEN"
export LOCOMO_JUDGE_TOKEN="$TOKEN"
export DASHSCOPE_API_KEY="$TOKEN"
export ECHOMEM_CHAT_API_KEY="$TOKEN"
export JUDGE_BASE_URL="$BASE_URL"
export JUDGE_MODEL="$MODEL"
export ECHOMEM_CHAT_BASE_URL="$BASE_URL"
export ECHOMEM_CHAT_MODEL="$MODEL"

"$PY" - <<'PY'
import json
import os
from pathlib import Path

payload = {
    "source_qa_dir": os.environ["SOURCE_QA_DIR"],
    "run_dir": os.environ["RUN_DIR"],
    "dataset": os.environ["DATASET"],
    "workspace": os.environ["WORKSPACE"],
    "account": os.environ["ACCOUNT"],
    "notes": [
        "Full81 QA-only rerun on the already imported conv-30 workspace.",
        "Goal: increase final segment_memory coverage and reduce atom-dominated wrong answers.",
        "Changes under test: rule granularity router + compat_allow_local_evidence + local raw segments + segment readback + answer refinement.",
    ],
    "config": {
        "retrieval_mode": "search",
        "granularity_router": "rule",
        "compat_allow_local_evidence": True,
        "local_segments": True,
        "local_segment_mode": "raw",
        "segment_readback": True,
        "segment_readback_mode": "fine_only",
        "answer_refinement": True,
        "toolloop_rescue_on_toollike_answer": True,
    },
}
Path(os.environ["RUN_DIR"]).joinpath("manifest.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
PY

"$PY" "$ROOT/scripts/echomemory_memory_qa.py" \
  --dataset "$DATASET" \
  --out-dir "$RUN_DIR/echomemory_qa" \
  --sample conv-30 \
  --echomem-root "$ECHO_ROOT" \
  --workspace "$WORKSPACE" \
  --account "$ACCOUNT" \
  --user-id default \
  --agent-id default \
  --prompt-mode vikingboat_lite \
  --answer-base-url "$BASE_URL" \
  --answer-model "$MODEL" \
  --answer-token "$TOKEN" \
  --top-k 30 \
  --score-threshold 0.75 \
  --memory-budget-chars 6000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --retrieval-mode search \
  --retrieval-ranker score \
  --granularity-router rule \
  --compat-allow-local-evidence \
  --retrieval-uri-dedup \
  --search-overview-enrichment \
  --segment-readback \
  --segment-readback-mode fine_only \
  --segment-window 2 \
  --segment-session-limit 8 \
  --segment-max-hits 8 \
  --segment-hits-per-session 2 \
  --local-segments \
  --local-segment-max 24 \
  --local-segment-size 4 \
  --local-segment-stride 4 \
  --local-segment-mode raw \
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
  --fallback-to-one-shot \
  --answer-refinement \
  --toolloop-rescue-on-toollike-answer

"$PY" "$ROOT/scripts/local_judge.py" \
  --input "$RUN_DIR/echomemory_qa/echomemory_memory_qa_results.csv" \
  --base-url "$BASE_URL" \
  --model "$MODEL" \
  --token "$TOKEN" \
  --parallel 10 \
  --timeout-s 120 \
  --retries 5
