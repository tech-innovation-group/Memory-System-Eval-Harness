# Contributing

Thanks for helping improve LoCoMo Memory Eval Workbench. This project is
currently scoped to OpenViking and EchoMem/EchoMemory memory backends, with
LoCoMo as the first polished benchmark flow.

## Before Opening a PR

Run the local checks:

```bash
python3 -m py_compile server.py memory/runs.py memory/report_export.py memory/reports.py
node --check web/static/app.js
node --check static/app.js
./preflight.sh
```

After editing files in `web/static`, mirror the core entry files:

```bash
cp web/static/index.html static/index.html
cp web/static/app.js static/app.js
cp web/static/styles.css static/styles.css
cp web/static/product-roadmap.html static/product-roadmap.html
```

## Contribution Areas

- LoCoMo import, commit integrity, QA, Judge, and report reliability
- OpenViking adapter behavior and diagnostics
- EchoMem/EchoMemory adapter contract, evidence shape, and fork onboarding
- HTML report readability, error attribution, run diff, and safe export
- UI clarity for dataset validation, memory import, QA progress, and results

## Safety Rules

Do not commit or attach:

- `.env`, `.env.local`, `judge.conf`, or real API keys
- raw `runs/`, `dist/`, `outputs/`, or private reports
- OpenViking or EchoMem workspaces
- screenshots containing tokens, private paths, or private model outputs
- full private datasets unless they are explicitly licensed for sharing

Use placeholders such as `<your-api-key>` in docs and examples.

## Backend Contract

New memory-backend work should fit the adapter contract rather than adding
backend-specific UI branches. The platform expects:

- account-scoped workspace isolation
- session write and `commit_session` semantics
- retrieval with relevant-memory evidence
- evidence fields such as `content`, `uri` or `source_uri`, `score`, `memory_type`, `evidence_uri`, and `trace`
- import integrity and report artifacts that can be inspected without secrets

## Pull Request Checklist

- Scope stays OpenViking + EchoMem/EchoMemory unless a maintainer explicitly expands it.
- `./preflight.sh` passes.
- The GitHub Actions preflight workflow passes on the PR.
- UI changes are mirrored from `web/static` to `static`.
- New generated artifacts are ignored or redacted.
- Reports and screenshots contain no real API keys.
- README or handoff docs are updated when user-facing behavior changes.
