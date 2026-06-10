# LoCoMo Harness Shape

This project is currently LoCoMo-first and memory-backend driven. It follows the common benchmark harness shape used by LangSmith-style evals, W&B/Weave-style run tracking, and MemoryAgentBench-style memory agent evaluation.

## Current Chain

- Dataset registry: `/api/datasets` exposes LoCoMo presets and `/api/dataset` summarizes samples, categories, and question counts.
- Account isolation: each account owns its memory backend selection and workspace path.
- Memory import: LoCoMo conversations are written into the selected backend and archived with `commit_session`.
- Agent QA: selected LoCoMo questions retrieve relevant memory and call the answer model.
- Judge stage: existing result CSVs can be graded without rerunning memory import or QA.
- Artifact tracking: every task writes a run directory under `runs/`, plus `manifest.json`, `config_snapshot.json`, logs, result CSV, summaries, evidence, and HTML reports.
- Result review: the Web UI reads CSV summaries, pending Judge counts, token usage, relevant memory, failure clustering, diff, and report export.

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
