# EchoMemory Formal Benchmark Runbook

Date: 2026-06-13

> Update note (2026-06-14):
> This runbook predates the explicit protocol freeze.
> For paper-facing runs, the authoritative protocol is now:
> `/Users/chx/locomo-eval-web/docs/echomemory_mm_benchmark_protocol_freeze_20260614.md`
>
> In particular:
> - do not treat the `fast` import path below as the final benchmark protocol
> - do not treat bare script defaults as paper-facing canonical settings
> - use the frozen protocol and subset specs for new EchoMemory-MM benchmark runs

This runbook turns the current EchoMemory-TG package into a runnable formal benchmark plan.

## 1. Core takeaway

The current repository already has the main command surface needed for formal TG evaluation:

- LoCoMo import
- EchoMemory QA
- wait-for-stability + QA + judge wrapper
- LongMemEval official-style evaluator
- benchmark adapter
- plugin task builders

The remaining work is not “find the scripts”; it is:

1. freeze one clean run configuration
2. run benchmark-scale LoCoMo and LongMemEval
3. fill the paper tables

## 2. LoCoMo formal TG path

### 2.1 Import

Use:

```bash
python3 scripts/echomemory_locomo_import.py \
  --dataset <LOCOMO_DATASET> \
  --out-dir <RUN_DIR>/echomemory_import \
  --echomem-root <ECHOMEM_ROOT> \
  --workspace <WORKSPACE> \
  --account <ACCOUNT> \
  --user-id <USER_ID> \
  --agent-id <AGENT_ID> \
  --sample conv-30 \
  --session-mode locomo \
  --import-wait-mode fast \
  --commit-wait-s 8 \
  --flush-call-timeout-s 15 \
  --flush-attempts 0
```

Notes:

- `fast` import is useful for iteration, but it is not enough by itself for a final paper result.
- For the final run, prefer a stable import / settle path, not just the fastest path.
- As of 2026-06-14, the explicit paper-facing protocol freeze uses `import_wait_mode=full`.

### 2.2 Wait for stability + QA + judge

Use:

```bash
python3 scripts/echomemory_wait_and_eval.py \
  --import-summary <RUN_DIR>/echomemory_import/echomemory_import_summary.json \
  --dataset <LOCOMO_DATASET> \
  --echomem-root <ECHOMEM_ROOT> \
  --workspace <WORKSPACE> \
  --account <ACCOUNT> \
  --user-id <USER_ID> \
  --agent-id <AGENT_ID> \
  --qa-out-dir <RUN_DIR>/echomemory_qa \
  --sample conv-30 \
  --settle-seconds 180 \
  --stabilize-timeout-seconds 300 \
  --stability-polls 3 \
  --poll-seconds 30 \
  --answer-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --answer-model deepseek-v4-flash \
  --answer-token <ANSWER_TOKEN> \
  --judge-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --judge-model deepseek-v4-flash \
  --judge-token <JUDGE_TOKEN> \
  --prompt-mode vikingboat_lite \
  --tool-set vikingboat_default \
  --top-k 30 \
  --score-threshold 0.1 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000
```

### 2.3 Expected outputs

- `echomemory_memory_qa_results.csv`
- `longmemeval_official_summary.json` only for LongMemEval, not LoCoMo
- judge / report artifacts in the run directory

## 3. LongMemEval formal TG path

### 3.1 Adaptation and QA

Use the adapter layer to normalize the dataset:

```bash
python3 scripts/benchmark_adapter.py \
  --dataset <LONGMEM_DATASET> \
  --format longmemeval \
  --out-dir <RUN_DIR>/adapter \
  --memory-safety-mode read_only_recommended \
  --namespace <RUN_NAMESPACE> \
  --mode dry-run
```

Then run the answer path that emits LongMemEval QA CSV.

### 3.2 Official-style evaluation

Use:

```bash
python3 scripts/longmemeval_official_eval.py \
  --csv <RUN_DIR>/openviking_generic_qa_results.csv \
  --reference <LONGMEM_REFERENCE> \
  --out-dir <RUN_DIR>/longmemeval_eval \
  --base-url https://ark.cn-beijing.volces.com/api/v3 \
  --model <JUDGE_MODEL> \
  --token <JUDGE_TOKEN> \
  --parallel 10
```

## 4. Plugin-level wiring

If you run from the web UI / task system, the relevant code path is:

- `memory/plugins/echomemory/tasks.py`
- `memory/plugins/echomemory/plugin.py`
- `server.py`

This means the UI task form is not a separate benchmark system; it is a wrapper over these command templates.

## 5. Submission tables to fill

Use:

- `/Users/chx/locomo-eval-web/docs/echomemory_benchmark_tables_template_20260613.md`

Fill these tables first:

1. LoCoMo main benchmark table
2. LongMemEval main benchmark table
3. ablation table
4. readiness diagnostics

## 6. What is already enough to claim

Already supported by local evidence:

- stream-to-structure architecture
- planner-guided retrieval
- event-time handling
- readiness gating
- nano temporal / readiness / multimodal intuition

## 7. What is still missing for a submission-grade result

Still missing:

1. benchmark-scale LoCoMo TG result on the exact current planner path
2. benchmark-scale LongMemEval result on the current answer path
3. readiness-gate rate in formal runs
4. clean paper tables filled with those numbers
5. if CVPR remains the target, real multimodal ingest on the main code path
