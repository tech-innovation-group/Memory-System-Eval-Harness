# MemoryBench Harness Shape

This project is memory-backend driven and benchmark-normalized. It follows the
same benchmark harness shape across `LoCoMo`, `LongMemEval`, `HotpotQA`, and
other structured datasets so backend comparisons stay aligned.

## Current Chain

- Dataset registry: `/api/datasets` exposes benchmark presets and `/api/dataset` summarizes samples, categories, and question counts.
- Account isolation: each account owns its memory backend selection and workspace path.
- Memory import: dataset-specific source memory is written into the selected backend and archived with `commit_session` or the backend-equivalent finalization step.
- Agent QA: selected benchmark questions retrieve relevant memory and call the answer model.
- Judge stage: existing result CSVs can be graded without rerunning memory import or QA.
- Artifact tracking: every task writes a run directory under `runs/`, plus `manifest.json`, `config_snapshot.json`, logs, result CSV, summaries, evidence, and HTML reports.
- Result review: the Web UI reads CSV summaries, pending Judge counts, token usage, relevant memory, failure clustering, diff, and report export.

## Dataset-to-memory mapping

- `LoCoMo`: inject multi-session conversation turns as memory.
- `LongMemEval`: inject haystack sessions as memory documents / session messages.
- `HotpotQA`: inject each sample's `context` paragraphs as benchmark memory documents, then ask one multi-hop question against that injected context.

`HotpotQA` therefore uses the same lifecycle as the other benchmarks, but its
"memory" is question-scoped source context rather than long-lived user history.

## Active Backends

- OpenViking: session write, archive, relevant-memory search, memory browser, import integrity, and LoCoMo QA.
- EchoMem/EchoMemory: local SDK session write, archive, relevant-memory search, evidence capture, and LoCoMo QA.

The Web UI talks to memory backend adapters through `memory/adapters`. It should not hardcode a benchmark backend in LoCoMo buttons; import and QA task kinds are derived from the current account's selected memory backend.

Adapter contract gate:

- `/api/backends` returns each backend's `contract.status`, missing required capabilities, and missing required methods.
- `/api/handoff-audit` fails if OpenViking or EchoMem cannot satisfy the LoCoMo-required adapter contract.
- `./preflight.sh` checks the same gate before handoff or formal runs.
- `python3 scripts/adapter_doctor.py --format json --strict` provides the same gate for local CLI and CI.

## Verified Smoke Target

- Dataset: LoCoMo `dataset/locomo10.json`
- Scope: one selected conversation or all conversations
- Import gate: selected backend reports submitted messages and archive/commit status
- QA gate: at least 10 selected questions complete with answer, evidence/context, token usage, and output CSV
- Judge gate: pending rows remain pending, not falsely counted as 0% accuracy
- Required artifacts: result CSV, summary JSON, `manifest.json`, `config_snapshot.json`, task log, and report HTML

## Official-flow alignment

`OpenViking v0.4.4` exposes two official benchmark families that matter here:

- memory benchmarks with explicit stage directories:
  - `benchmark/locomo/openviking/`
  - `benchmark/longmemeval/openviking/`
- generic knowledge-base QA benchmarks:
  - `benchmark/RAG/`

The memory benchmark sequence is:

1. import
2. eval
3. judge
4. stat / summary

The generic RAG benchmark sequence is:

1. ingest / generation
2. eval
3. optional deletion / cleanup

`HotpotQA` is described in the `v0.4.4` README as `Knowledge Base QA`, so it
is closer to the generic RAG family than to LoCoMo conversation memory.

`locomo-eval-web` still mirrors the same high-level shape through:

1. dataset adapter normalization
2. backend-specific import
3. retrieval plus QA
4. judge
5. optional dataset-specific official scorer

For `HotpotQA`, this means:

- source `context` paragraphs are normalized into temporary memory documents
- both backends run the same `import -> QA -> judge` chain
- an extra dataset-specific scorer is applied after QA

The optional dataset-specific scorer is:

- `scripts/hotpotqa_answer_eval.py`

Current metric scope:

- answer-only `EM`
- answer-only `F1`

Current non-goals:

- supporting-fact metrics
- joint answer plus support metrics

Those would require the QA result CSV to emit explicit supporting sentence
predictions.

## Readiness gate for formal benchmark runs

Formal benchmark datasets must not enter QA before memory import is stable.

`EchoMemory` benchmark runs for:

- `hotpotqa`
- `longmemeval`
- `evolvingevents`
- `proagentbench`
- `tau2bench`

now force strict readiness before QA. The old `fast wait` and
`defer artifact wait` shortcut is not allowed for these benchmark flows.
