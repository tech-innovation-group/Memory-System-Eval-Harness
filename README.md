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
- `HotpotQA`

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

## Run EchoMemory on LoCoMo

For someone new to this repo, the shortest working path is:

1. clone an EchoMemory source tree locally
2. create its Python environment and install it with `pip install -e .`
3. copy `env.echomem.example` to `.env.local`
4. fill `ECHOMEM_ROOT`, `ECHOMEM_WORKSPACE`, model endpoints, and judge config
5. start this harness with `source .env.local && ./preflight.sh && ./start.sh`
6. open `http://127.0.0.1:19181/`
7. in `系统配置`, select `EchoMemory`
8. in `LoCoMo 评测`, run `conv-30` import first, then a small QA sample, then judge, then export report

Minimal local setup:

```bash
git clone <their-echo-memory-repo> /absolute/path/to/echo_memory
cd /absolute/path/to/echo_memory
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .

cd /path/to/locomo-eval-web
cp env.echomem.example .env.local
# edit .env.local locally
source .env.local
./preflight.sh
./start.sh
```

The most important variables are:

- `ECHOMEM_ROOT`
- `ECHOMEM_WORKSPACE`
- `ECHOMEM_ACCOUNT`
- `ECHOMEM_USER_ID`
- `ECHOMEM_AGENT_ID`
- `DASHSCOPE_API_KEY`
- `ECHOMEM_CHAT_API_KEY`
- `JUDGE_BASE_URL`
- `JUDGE_MODEL`
- `JUDGE_TOKEN`

Recommended first run:

- dataset: `conv-30`
- import selected conversation(s)
- run `检查导入完整性`
- answer 5 to 10 questions first
- run judge on the current CSV
- export the HTML report

Detailed Chinese handoff notes live in:

- `README_ECHOMEM_LOCOMO_HANDOFF.md`

## Dataset Support Matrix

### Bundled and directly runnable

- `dataset/locomo10.json`
- `dataset/longmemeval.sample.json`
- `dataset/evolvingevents.sample.json`

### Canonical full-data slots

- `dataset/full/locomo.json`
- `dataset/full/longmemeval_s_cleaned.json`
- `dataset/full/evolvingevents.json`
- `dataset/full/hotpotqa_dev_distractor.json`

Current repo reality:

- `dataset/full/longmemeval_s_cleaned.json` exists
- `dataset/full/evolvingevents.json` does not exist yet
- `dataset/full/hotpotqa_dev_distractor.json` is the canonical full HotpotQA slot in this repo
- if `dataset/full/locomo.json` exists, UI and server will prefer it automatically

## Unified Benchmark Flow

This repo now treats `HotpotQA` as part of the same benchmark lifecycle used by
`LoCoMo` and `LongMemEval`, instead of as a one-off special path.

### OpenViking official benchmark shape

OpenViking `v0.4.4` ships the same high-level lifecycle in its official
benchmark directories:

- `benchmark/locomo/openviking/`
- `benchmark/longmemeval/openviking/`

The official sequence is:

1. `import_to_ov.py`
2. `run_eval.py`
3. `judge.py`
4. `stat_judge_result.py`

That means the backend is always evaluated as:

- import memory first
- run retrieval plus answer generation second
- judge after answers are written
- summarize from stable artifacts

### locomo-eval-web unified shape

`locomo-eval-web` maps all supported benchmarks onto the same lifecycle through
`generic_qa` task builders:

1. dataset adapter normalizes each row into a `job` plus an injection plan
2. backend-specific import writes memory into an isolated namespace / user scope
3. QA runs only after import readiness checks pass
4. judge runs from the result CSV
5. optional official-style answer-only eval runs for datasets that support it

Relevant code paths:

- `scripts/benchmark_adapter.py`
- `scripts/openviking_generic_qa.py`
- `scripts/echomemory_generic_qa.py`
- `memory/plugins/openviking/tasks.py`
- `memory/plugins/echomemory/tasks.py`

### What HotpotQA means in this harness

`HotpotQA` is not a conversation-memory benchmark like `LoCoMo`.

In this harness, each HotpotQA sample is converted into:

- one QA row
- a set of source documents built from `context`
- a temporary memory namespace containing those source documents

So the evaluation target is:

- document injection integrity
- retrieval quality under distractors
- multi-hop answer composition

It is not a test of long-lived user conversation memory.

### Why HotpotQA is still comparable here

Even though the source material is different, the task lifecycle is now the
same across backends:

- inject benchmark memory
- wait for memory to become ready
- answer from retrieved memory
- judge on the same output CSV

For `EchoMemory`, benchmark datasets now force strict readiness gating before
QA. `HotpotQA`, `LongMemEval`, `EvolvingEvents`, `proAgentBench`, and
`tau2-bench` do not allow the old `fast wait` / `defer artifact wait` shortcut
to enter QA early.

### HotpotQA artifact shape

For formal runs, expect these artifacts under the run directory:

- `echomemory_generic_qa_results.csv` or `openviking_generic_qa_results.csv`
- `summary.json`
- `judge_summary.json`
- `hotpotqa_answer_summary.json`

The HotpotQA scorer in this repo is answer-only:

- `scripts/hotpotqa_answer_eval.py`

It computes:

- `answer_em`
- `answer_f1`

It does not compute supporting-fact or joint metrics because the current result
CSV does not emit supporting sentence predictions.

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
