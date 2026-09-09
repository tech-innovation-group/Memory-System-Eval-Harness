---
name: echomem-stress
description: >
  Interactively prepare, start, monitor, resume, and explain EchoMem real-model
  M1-M6 stress tests with Memory-System-Eval-Harness. Use when a user asks to
  stress test EchoMem, measure capacity, fairness, Search priority, tenant fault
  isolation, Commit recovery, observability, or generate the HTML report.
---

# EchoMem Stress

Run the repository's real HTTP six-metric suite as a guided product flow. Read
`references/interactive-workflow.md` before starting a run. Use
`performance/targets/echomem/README.md` as the canonical setup and CLI reference.

## Non-negotiable evidence rules

- Use a real EchoMem deployment, real LLM, real Embedding, and independent tenant
  credentials. Do not use mocks or reuse one key as multiple tenants.
- Do not count HTTP 200 Search as a successful recall unless the expected fact is
  present. Do not count Commit 202 as completion; poll its terminal state.
- Keep failed, timed-out, rejected, provider-error, and pending samples in their
  original denominators. Never hide them to improve a result.
- Never print, persist in Git, or render API keys, tenant keys, passwords, or the
  test-control token. Refer to environment variable names only.
- Record the exact EchoMem and harness commits, config fingerprint, model names,
  container identity, and actual resource limits in every run.

## Interactive flow

1. Locate the harness and EchoMem repositories. Prefer paths supplied by the
   user. Show their current branch, commit, dirty state, and whether the harness
   contains `performance/targets/echomem/observation_run.py`.
2. Inspect prerequisites and present a compact readiness summary: EchoMem ready,
   Docker/container, profile, tenant count, LLM preflight, Embedding preflight,
   protected test endpoints, output directory, and destructive-test safety.
3. If the requested scope is not already clear, offer: quick chain check;
   M1-M3; full M1-M6; one metric; resume; report-only. Recommend quick on a new
   machine and full only after it passes.
4. Preview the exact command, selected metrics, estimated destructive actions,
   and output directory. Obtain explicit user authorization before M4 fault
   injection, M5 kill/restart, root login, or use of remote/shared resources.
5. Run preflight, then execute the repository entry point. Keep the process
   attached and report meaningful stage changes. Do not leave a required process
   running without tracking it.
6. On completion, inspect `execution-manifest.json`, `summary.json`, `suite.json`,
   and `report.html`. Open the HTML report and summarize every selected metric's
   status, denominator, primary number, and responsible module.

## Commands

Run from the harness repository root with a profile and a secret env file that
are outside Git:

```bash
performance/targets/echomem/run_six_metrics.sh quick PROFILE OUTPUT ENV_FILE
performance/targets/echomem/run_six_metrics.sh full PROFILE OUTPUT ENV_FILE

.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE --metrics M1,M2,M3 --env-file ENV_FILE --out-dir OUTPUT

.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE --env-file ENV_FILE --out-dir OUTPUT --resume
```

Use the canonical numbering everywhere:

- M1: single-instance capacity, hot users, and DAU conversion
- M2: equal-tier multi-tenant fairness
- M3: Search priority during uniform and single-tenant Commit floods
- M4: bystander Search degradation during one tenant's delay/reject fault
- M5: accepted Commit replay, ordering, and idempotency after crash recovery
- M6: per-tenant, per-lane queue/wait/execute/reject observability

Do not silently fetch, switch, reset, or force-update branches. If the required
suite is only on a PR branch, explain that and wait for the user to choose it.
