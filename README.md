# LoCoMo Memory Eval Workbench

Local-first memory evaluation workbench for **LoCoMo + OpenViking** and
**LoCoMo + EchoMem/EchoMemory**.

It imports conversations into a memory backend, verifies `commit_session`,
runs memory-grounded QA, judges answers, and exports evidence-rich HTML
reports. The goal is not just to show an accuracy number, but to explain
whether storage, retrieval, context construction, model generation, or Judge
caused the result.

## Why This Exists

Memory-system evaluation is often hard to reproduce because the important
state is scattered across scripts, workspaces, logs, model calls, and local
memory files. This workbench makes the full chain inspectable:

- dataset validation and category counts
- OpenViking or EchoMem account/workspace isolation
- conversation import and commit integrity
- relevant-memory evidence for every QA row
- answer model output, Judge output, token usage, and runtime
- retryable failures, pending Judge rows, wrong-answer clusters, and run diff
- safe handoff checks before sharing the project with another tester

The product roadmap is available at
[`web/static/product-roadmap.html`](web/static/product-roadmap.html).

## Current Scope

This release intentionally keeps the memory-backend surface small. The active
handoff target is MemoryBench Agent with OpenViking as the reproducible
baseline and EchoMem/EchoMemory as the external memory-system integration path.
No additional backend is required for the current LoCoMo handoff.

1. **OpenViking baseline**
   - HTTP service integration
   - workspace/account/session isolation
   - `commit_session` import path
   - relevant-memory retrieval and memory browsing

2. **EchoMem / EchoMemory**
   - local SDK/runtime integration
   - account-scoped workspace
   - `create_session`, `add_message`, `commit_session`
   - `find` / `search` retrieval evidence
   - external fork and graph-memory module contract checks

Other benchmark pages exist as dataset registry or future-runner surfaces, but
the polished end-to-end flow is Custom Agent + LoCoMo first.

## Public Surface Contract

The public UI surface is governed by `web/ui_contract.json`. For the current
handoff, the only public static files are:

- `web/static/index.html`
- `web/static/app.js`
- `web/static/styles.css`
- `web/static/product-roadmap.html`

Other HTML files under `web/static/` are experiment history or generated
reports. They are useful for internal analysis, but they are not product
entrypoints and should not be presented as shipped features.

## 5-Minute Smoke Test

```bash
cd locomo-eval-web
# Choose one template: env.example for OpenViking baseline, env.echomem.example for EchoMem.
cp env.example .env.local
# Edit .env.local with your own local paths and API keys.
source .env.local
./preflight.sh
./start.sh
```

Open the UI:

```text
http://127.0.0.1:19181/
```

Recommended first route:

1. Open `README / 交付说明`.
2. Run `交付驾驶舱`, `GitHub Launch Kit`, and `外部验收矩阵`.
3. Open `系统配置` and confirm OpenViking baseline or EchoMem integration status.
4. Open `LoCoMo评测`.
5. Validate the bundled `dataset/locomo10.json`.
6. Import one conversation, such as `conv-30`.
7. Run one QA, then 10 QA, then Judge.
8. Generate and inspect the HTML report.

## Configuration

Use `.env.local` for local secrets and paths. Do not commit or share it.

Important variables:

- `LOCOMO_EVAL_HOST` / `LOCOMO_EVAL_PORT`
- `LOCOMO_DATA`
- `OPENVIKING_SOURCE`
- `LOCOMO_EVAL_OPENVIKING_URL`
- `LOCOMO_EVAL_OPENVIKING_WORKSPACE`
- `ECHOMEM_ROOT`
- `ECHOMEM_WORKSPACE`
- `ECHOMEM_ACCOUNT`
- `ECHOMEM_USER_ID`
- `ECHOMEM_AGENT_ID`
- `ECHOMEM_CHAT_BASE_URL`
- `ECHOMEM_CHAT_MODEL`
- `ECHOMEM_CHAT_API_KEY`
- `JUDGE_BASE_URL`
- `JUDGE_MODEL`
- `JUDGE_TOKEN`

The repository includes `env.echomem.example` and `env.example` with
placeholder values only.

LoCoMo dataset defaults:

- bundled smoke dataset: `dataset/locomo10.json`
- canonical full dataset slot: `dataset/full/locomo.json`

If `dataset/full/locomo.json` exists, the server and UI prefer it automatically.
Otherwise they fall back to `dataset/locomo10.json`.

## EchoMem Fork Integration

If you are testing a custom EchoMem fork or adding a graph-memory module, keep
the platform-facing contract stable. The platform should not need dataset-flow
changes for graph memory; graph retrieval should surface through the same
evidence structure.

- `open_runtime(config_path)`
- `EchoMemSDK.create_session(...)`
- `EchoMemSDK.add_message(...)`
- `EchoMemSDK.commit_session(...)`
- `EchoMemSDK.find(query, ctx=...)`
- `EchoMemSDK.search(query, ctx=..., budget={"max_results": top_k})`

Evidence returned by retrieval should include:

- `content`
- `uri` or `source_uri`
- `score` or `confidence`
- `memory_type`
- `evidence_uri`
- `trace`

Run the contract checks before a benchmark:

```bash
python3 scripts/adapter_doctor.py --format markdown --strict
curl -s http://127.0.0.1:19181/api/echomem-contract | python3 -m json.tool | head -120
```

## OpenViking Baseline

OpenViking is treated as the baseline memory backend. The platform calls the
backend service rather than reimplementing memory extraction or retrieval.
EchoMem/EchoMemory is the external system under test for handoff runs.

The important reproducibility fields are:

- OpenViking URL
- workspace
- account
- user id
- agent id
- session id
- top-k
- prompt mode
- answer model
- Judge model

Use a fresh workspace or account for formal runs to avoid memory pollution.

## Report Artifacts

Each run writes artifacts under `runs/<run_id>/`. Do not publish raw run
folders without reviewing them first.

Typical artifacts:

- `manifest.json`
- `config_snapshot.json`
- `run.log`
- result CSV
- `relevant_memory.json`
- `summary.json`
- `judge_summary.json`
- `report.html`

The HTML report should make these questions answerable:

- Was memory imported completely?
- Which memory evidence was retrieved?
- Did the answer model see useful context?
- Was Judge complete or pending?
- Are failures caused by storage, retrieval, context, model/API, or Judge?
- How does this run differ from a previous run?

## Public Handoff Safety

Before sending this project to another tester or publishing it:

```bash
./preflight.sh
curl -s http://127.0.0.1:19181/api/handoff-audit | python3 -m json.tool | head -160
curl -s http://127.0.0.1:19181/api/github-launch-kit | python3 -m json.tool | head -120
```

Do not share:

- files ignored by `.gitignore` or `export-ignore` rules in `.gitattributes`
- `.env.local`
- `judge.conf`
- real API keys or screenshots containing tokens
- raw `runs/`
- OpenViking or EchoMem workspaces
- private logs, model responses, or unredacted reports
- historical static reports such as `web/static/*.html` other than
  `index.html` and `product-roadmap.html`
- `dist/`, `outputs/`, or old local packages

Share:

- source code
- `.gitignore` and `.gitattributes`, so generated artifacts and local secrets stay outside public handoff
- `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and `PUBLICATION_CHECKLIST.md`
- core static UI files: `web/static/index.html`, `web/static/app.js`,
  `web/static/styles.css`, and `web/static/product-roadmap.html`
- `dataset/locomo10.json`
- `env.echomem.example`
- redacted demo reports
- Issue templates
- this README and the handoff README

## Developer Checks

```bash
python3 -m py_compile server.py memory/runs.py memory/report_export.py memory/reports.py
node --check web/static/app.js
node --check static/app.js
./preflight.sh
```

After changing the core files in `web/static`, mirror them to `static`:

```bash
cp web/static/index.html static/index.html
cp web/static/app.js static/app.js
cp web/static/styles.css static/styles.css
cp web/static/product-roadmap.html static/product-roadmap.html
```

## Open Source Collaboration

- Use [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening PRs.
- Use [`SECURITY.md`](SECURITY.md) for vulnerability or secret-leak reports.
- Use [`PUBLICATION_CHECKLIST.md`](PUBLICATION_CHECKLIST.md) before publishing or sending the project to another tester.
- Pull requests use [`.github/pull_request_template.md`](.github/pull_request_template.md) and CI runs [`.github/workflows/preflight.yml`](.github/workflows/preflight.yml).
- The current license is [`MIT`](LICENSE).

## Useful Files

- [`server.py`](server.py)
- [`preflight.sh`](preflight.sh)
- [`.gitignore`](.gitignore)
- [`.gitattributes`](.gitattributes)
- [`LICENSE`](LICENSE)
- [`CONTRIBUTING.md`](CONTRIBUTING.md)
- [`SECURITY.md`](SECURITY.md)
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
- [`PUBLICATION_CHECKLIST.md`](PUBLICATION_CHECKLIST.md)
- [`.github/pull_request_template.md`](.github/pull_request_template.md)
- [`.github/workflows/preflight.yml`](.github/workflows/preflight.yml)
- [`HARNESS_SPEC.md`](HARNESS_SPEC.md)
- [`README_ECHOMEM_LOCOMO_HANDOFF.md`](README_ECHOMEM_LOCOMO_HANDOFF.md)
- [`memory/adapters/contract.py`](memory/adapters/contract.py)
- [`scripts/openviking_locomo_import.py`](scripts/openviking_locomo_import.py)
- [`scripts/openviking_memory_qa.py`](scripts/openviking_memory_qa.py)
- [`scripts/echomemory_locomo_import.py`](scripts/echomemory_locomo_import.py)
- [`scripts/echomemory_memory_qa.py`](scripts/echomemory_memory_qa.py)
- [`scripts/local_judge.py`](scripts/local_judge.py)
- [`scripts/generate_html_report.py`](scripts/generate_html_report.py)
