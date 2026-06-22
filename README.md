# MemoryBench Eval Workbench

Local-first memory evaluation workbench for `OpenViking` and `EchoMemory`.

This repository is used to:

- run real memory-backed benchmark flows
- inspect import / retrieval / answer / judge artifacts
- compare backend behavior on the same dataset protocol
- generate HTML reports that are shareable after redaction

Current active benchmark focus:

- `LoCoMo`
- `LongMemEval`
- `EvolvingEvents`

## Current Status

This repo is no longer just a LoCoMo demo shell.

Today it contains:

- a stable `OpenViking` service-backed path
- a stable `EchoMemory` local-SDK path
- dataset registry and UI pages for multiple benchmarks
- real run history and report export support
- platform-side diagnostics for long-running EchoMemory tasks

What is already verified with real runs:

- `EvolvingEvents sample` on `EchoMemory 0.1.0`
- `LongMemEval full` single-sample and subset runs on `EchoMemory 0.1.0`

What is still limited by external state or backend behavior:

- `EvolvingEvents full` is registered, but the real full dataset file is not bundled in this repo
- `LongMemEval full` long samples can stall in EchoMemory 0.1.0 post-import atom extraction / indexing

So the platform is real, but not every benchmark is already complete at full scale.

## Backend Model

There are only two memory backends in the current public surface:

- `OpenViking`
- `EchoMemory`

Their integration modes are different:

### OpenViking

- connected through a service URL and port
- the platform calls backend HTTP APIs

### EchoMemory

- not connected by service port
- the platform uses a local EchoMemory source tree plus SDK/runtime

EchoMemory integration depends on:

- `ECHOMEM_ROOT`
- `ECHOMEM_PYTHON` or `$ECHOMEM_ROOT/.venv/bin/python`
- workspace
- account
- user id
- agent id

The platform expects these SDK/runtime capabilities to remain compatible:

- `open_runtime(...)`
- `EchoMemSDK(...)`
- `create_session(...)`
- `add_message(...)`
- `commit_session(...)`
- `find(...)`
- `search(...)`

If a custom EchoMemory fork keeps those interfaces and returns normal evidence fields, external users usually only need to change configuration, not platform code.

## Dataset Support Matrix

### Bundled and directly runnable

- `dataset/locomo10.json`
- `dataset/longmemeval.sample.json`
- `dataset/evolvingevents.sample.json`

### Canonical full-data slots

- `dataset/full/locomo.json`
- `dataset/full/longmemeval_s_cleaned.json`
- `dataset/full/evolvingevents.json`

Current repo reality:

- `dataset/full/longmemeval_s_cleaned.json` exists
- `dataset/full/evolvingevents.json` does not exist yet
- if `dataset/full/locomo.json` exists, UI and server will prefer it automatically

## EchoMemory 0.1.0 Benchmark Notes

`EchoMemory 0.1.0` is verified from the local source tree metadata, not guessed.

Real benchmark evidence already generated in this repo:

- report:
  - `generated-reports/echomemory_v010_longmemeval_evolvingevents_20260615.html`
- EvolvingEvents sample successful run:
  - `runs/echomemory_generic_qa_20260615_155307_567cd6/`
- LongMemEval single full-sample run:
  - `runs/echomemory_generic_qa_20260615_130316_1b05ee/`
- LongMemEval long-sample bottleneck reproduction:
  - `runs/echomemory_generic_qa_20260615_155532_1df346/`

Judge alignment for those runs uses:

- question
- gold answer
- generated answer

and does not feed retrieved memory into the judge path.

## Quick Start

```bash
cd locomo-eval-web
cp env.echomem.example .env.local
# edit .env.local locally
source .env.local
./preflight.sh
./start.sh
```

Then open:

```text
http://127.0.0.1:${LOCOMO_EVAL_PORT:-19181}/
```

Recommended first route:

1. Open `README`
2. Open `系统配置`
3. Select backend and confirm model settings
4. Open the benchmark page you actually want to run
5. Validate dataset path
6. Run a small sample first
7. Judge current result
8. Export HTML report

## Realistic First Runs

### EchoMemory + LongMemEval

Start with:

- a tiny selected question set
- or one known sample

Do not start with all rows on first boot.

Reason:

- long samples can trigger slow `atom_extraction`
- long samples can stay in `commit:indexing`
- this is currently a backend-side bottleneck the platform now exposes more honestly

### EchoMemory + EvolvingEvents

Use the bundled sample first.

If you want a formal full run, place the converted file at:

```text
dataset/full/evolvingevents.json
```

The conversion helper is:

```bash
python3 scripts/prepare_evolvingevents_full.py \
  --chunks /path/to/chunks.json \
  --qa /path/to/qa_pairs.json \
  --out dataset/full/evolvingevents.json
```

## Reports and Runs

Each run writes artifacts under `runs/<run_id>/`.

Typical files:

- `manifest.json`
- `config_snapshot.json`
- `run.log`
- result CSV
- `judge_summary.json`
- `summary.json`

Generated HTML reports live under:

- `generated-reports/`

## Open-Source Handoff Rules

Do not share:

- `.env.local`
- `judge.conf`
- raw `runs/`
- private workspaces
- real API keys
- screenshots containing tokens

Do share:

- source code
- `env.example`
- `env.echomem.example`
- redacted demo reports
- this README
- `README_ECHOMEM_LOCOMO_HANDOFF.md`

## Useful Files

- `README_ECHOMEM_LOCOMO_HANDOFF.md`
- `docs/echomem_test_guide.md`
- `server.py`
- `scripts/echomemory_generic_qa.py`
- `scripts/prepare_evolvingevents_full.py`
- `scripts/render_echomemory_v010_benchmark_report.py`
- `memory/runs.py`
- `dataset/manifest.json`

## Verification

```bash
python3 -m py_compile server.py memory/runs.py scripts/echomemory_generic_qa.py scripts/prepare_evolvingevents_full.py
node --check web/static/app.js
./preflight.sh
```
