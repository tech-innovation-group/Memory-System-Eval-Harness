# EchoMemory-MM LoCoMo conv-30 Formal Subset-20 Commands

Date: 2026-06-14

## Purpose

This file gives copyable command templates for the frozen `conv-30 subset-20` path.

It assumes:

- the benchmark protocol freeze is authoritative
- import and QA use the current EchoMemory scripts
- the question subset is fixed by:
  - `/Users/chx/locomo-eval-web/configs/echomemory_mm_locomo_conv30_formal_subset20_20260614.json`

---

## 1. Shared variables

```bash
RUN_DIR=/Users/chx/locomo-eval-web/runs/echomemory_mm_conv30_subset20_20260614
DATASET=/Users/chx/locomo-eval-web/dataset/locomo10.json
ECHOMEM_ROOT=/Users/chx/Code/echomemory/echo_memory
WORKSPACE=/private/tmp/echomemory_mm_conv30_subset20_20260614
ACCOUNT=echomemory-mm-conv30-subset20
USER_ID=default
AGENT_ID=default
QUESTIONS=conv-30_qa0,conv-30_qa1,conv-30_qa3,conv-30_qa4,conv-30_qa7,conv-30_qa8,conv-30_qa13,conv-30_qa14,conv-30_qa17,conv-30_qa23,conv-30_qa24,conv-30_qa25,conv-30_qa29,conv-30_qa31,conv-30_qa32,conv-30_qa36,conv-30_qa43,conv-30_qa46,conv-30_qa58,conv-30_qa71
mkdir -p "$RUN_DIR"
```

---

## 2. Import

```bash
python3 /Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py \
  --dataset "$DATASET" \
  --out-dir "$RUN_DIR/echomemory_import" \
  --echomem-root "$ECHOMEM_ROOT" \
  --workspace "$WORKSPACE" \
  --account "$ACCOUNT" \
  --user-id "$USER_ID" \
  --agent-id "$AGENT_ID" \
  --sample conv-30 \
  --session-mode locomo \
  --import-wait-mode full \
  --commit-wait-s 300 \
  --commit-call-timeout-s 300 \
  --flush-call-timeout-s 600 \
  --flush-attempts 3
```

---

## 3. Wait, stabilize, QA, judge

```bash
python3 /Users/chx/locomo-eval-web/scripts/echomemory_wait_and_eval.py \
  --import-summary "$RUN_DIR/echomemory_import/echomemory_import_summary.json" \
  --dataset "$DATASET" \
  --echomem-root "$ECHOMEM_ROOT" \
  --workspace "$WORKSPACE" \
  --account "$ACCOUNT" \
  --user-id "$USER_ID" \
  --agent-id "$AGENT_ID" \
  --qa-out-dir "$RUN_DIR/echomemory_qa" \
  --sample conv-30 \
  --questions "$QUESTIONS" \
  --settle-seconds 180 \
  --stabilize-timeout-seconds 300 \
  --stability-polls 3 \
  --poll-seconds 30 \
  --repair-before-qa \
  --repair-flush-call-timeout-s 600 \
  --repair-flush-attempts 2 \
  --repair-commit-wait-s 300 \
  --answer-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --answer-model deepseek-v4-flash \
  --answer-token "$DASHSCOPE_API_KEY" \
  --judge-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --judge-model deepseek-v4-flash \
  --judge-token "$DASHSCOPE_API_KEY" \
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
  --vikingboat-tool-loop \
  --no-vikingboat-compat \
  --initial-tool-prefetch \
  --fallback-to-one-shot
```

---

## 4. Direct QA-only rerun on the same imported workspace

Use this only if import is already complete and memory is already ready:

```bash
python3 /Users/chx/locomo-eval-web/scripts/echomemory_memory_qa.py \
  --dataset "$DATASET" \
  --out-dir "$RUN_DIR/echomemory_qa_rerun" \
  --sample conv-30 \
  --questions "$QUESTIONS" \
  --echomem-root "$ECHOMEM_ROOT" \
  --workspace "$WORKSPACE" \
  --account "$ACCOUNT" \
  --user-id "$USER_ID" \
  --agent-id "$AGENT_ID" \
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
  --prefetch-read-count 4 \
  --prefetch-context-chars 5000 \
  --max-iterations 8 \
  --answer-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --answer-model deepseek-v4-flash \
  --answer-token "$DASHSCOPE_API_KEY" \
  --model-retries 5 \
  --timeout-s 180 \
  --question-timeout-s 300 \
  --vikingboat-tool-loop \
  --no-vikingboat-compat \
  --initial-tool-prefetch \
  --fallback-to-one-shot \
  --no-local-session-summaries \
  --no-local-atoms \
  --no-local-messages \
  --no-local-timeline-hints \
  --no-local-memory-artifacts
```

---

## 5. Notes

1. The orchestration path is still the paper-facing canonical path.
2. The direct QA rerun is for efficient iteration after import has stabilized.
3. Record both the frozen protocol file and the subset JSON in any paper-facing report.
