# LoCoMo Memory Eval Harness v2

> LoCoMo-first memory evaluation platform for OpenViking and EchoMem/EchoMemory.

## Current Scope

The current product direction is intentionally focused:

- LoCoMo dataset import, QA, Judge, report export, and run comparison.
- Account-scoped clean memory spaces to avoid history pollution.
- Memory backend adapters under `memory/adapters`.
- Active scope: OpenViking baseline plus EchoMem/EchoMemory integration only.
- Adapter contract gate: `/api/backends`, `/api/handoff-audit`, and `./preflight.sh` verify required capabilities and methods before a benchmark run.

## Architecture

```text
LoCoMo JSON
  -> Web task payload
  -> selected memory backend adapter
  -> commit_session / memory archive
  -> relevant-memory retrieval
  -> answer model
  -> judge model
  -> CSV + HTML report + run diff
```

## Backend Adapters

- `memory/adapters/openviking`: OpenViking session write, `commit_session`, relevant-memory search, import integrity, memory browser, and LoCoMo QA task building.
- `memory/adapters/echomemory`: EchoMem local SDK session write, `commit_session`, retrieval, evidence capture, and LoCoMo QA task building.
- `memory/adapters/contract.py`: shared contract for required capabilities, required methods, optional methods, and public contract status.
- `scripts/adapter_doctor.py`: command-line adapter doctor for local handoff and CI checks.

The frontend only chooses the current account's memory backend. LoCoMo import and QA task kinds are derived from that choice:

- OpenViking: `openviking_import`, `openviking_qa`
- EchoMem: `echomemory_import`, `echomemory_qa`

Command-line doctor:

```bash
python3 scripts/adapter_doctor.py --format markdown --strict
```

## What The UI Should Provide

- Top account bar: create/delete account, switch account, show isolation status.
- System config: select OpenViking baseline or EchoMem integration for the current account only.
- LoCoMo eval: validate dataset, import selected/all conversations, select QA items, run QA, run Judge, export HTML report.
- Run analysis: view history, compare reports, inspect evidence/context/judge details.
- Agent workbench: manual chat, relevant memory display, manual archive/commit.

## Safety Rules

- Do not write API keys into docs, reports, logs, or screenshots.
- Use a fresh account or fresh workspace for formal experiments.
- Keep import workspace/account/user/agent identical during QA.
- Treat missing Judge rows as pending, not as 0% accuracy.

## Quick Start

```bash
cd /Users/chx/locomo-eval-web
./start.sh
```

Open:

```text
http://127.0.0.1:19181/
```

Then use the UI:

1. System config: choose OpenViking baseline or EchoMem integration for the current account.
2. LoCoMo eval: validate `dataset/locomo10.json`.
3. Import memory: choose a conversation or all conversations.
4. Run QA: select questions or run full LoCoMo.
5. Judge and export report.
