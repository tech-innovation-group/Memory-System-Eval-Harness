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
- Do not test an EchoMem checkout left on the repository's default `main`
  branch. M1-M3 require the current `origin/develop`; a full M1-M6 run requires
  PR449 to contain the current `origin/develop` until PR449 is merged. Verify
  this with `git merge-base --is-ancestor origin/develop HEAD`, and stop as
  `BLOCKED` if it fails.
- Build `deploy/single-node/config.json` from the checked-out EchoMem version's
  root `configs/config.example.json`. Stop as `BLOCKED` when
  `engine.enabled` is empty; the single-node example with no enabled memory
  engines is not a valid real-memory stress target.
- Do not count HTTP 200 Search as a successful recall unless the expected fact is
  present. Do not count Commit 202 as completion; poll its terminal state.
- Keep failed, timed-out, rejected, provider-error, and pending samples in their
  original denominators. Never hide them to improve a result.
- Never print, persist in Git, or render API keys, tenant keys, passwords, or the
  test-control token. Refer to environment variable names only.
- Record the exact EchoMem and harness commits, config fingerprint, model names,
  container identity, and actual resource limits in every run.
- Treat model availability preflight and workload model usage as separate facts.
  `mock=false`, HTTP 200, or a configured model name is never call evidence. A
  report may claim workload model usage only when bounded service-stage logs or
  Provider metrics contain real samples; otherwise mark it unverified.
- Use only `performance.targets.echomem.observation_run` or
  `performance/targets/echomem/run_six_metrics.sh` for the current M1-M6 flow.
  `performance.run --target echomem` is the legacy O1-O7 flow. If an output has
  `objective-suite.html` but no `report.html`, stop and report `WRONG_ENTRYPOINT`;
  never present that legacy report as the current six-metric result.

## Interactive flow

1. Locate the harness and EchoMem repositories. Prefer paths supplied by the
   user. Show their current branch, commit, dirty state, and whether the harness
   contains `performance/targets/echomem/observation_run.py`. Fetch branch refs
   only with authorization, then verify the selected EchoMem revision against
   `origin/develop`; never infer the revision from the directory name.
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
   Before interpreting results, verify `<OUTPUT>/report.html` exists. The mere
   presence of `objective-suite.html` proves the legacy report renderer ran, not
   that the current M1-M6 report was generated.

## Product conversation

Guide the engineer through these states instead of presenting an undifferentiated
list of commands:

1. **Discover**: inspect local repositories, EchoMem readiness, Docker access,
   profile, tenant credentials, provider configuration, and protected endpoints.
2. **Configure**: confirm the exact LLM and Embedding names, metric scope,
   concurrency target, tenant count, duration, output directory, and whether the
   run may perform fault injection or restart a dedicated container.
3. **Preview**: show the exact target commits, selected metrics, real model names,
   load levels, destructive actions, and command. Stop on a model-name mismatch.
4. **Validate**: run provider and memory seed/recall preflights. A successful HTTP
   response without the expected fact is a failed recall preflight.
5. **Execute**: stream stage-level progress and maintain the original denominator.
   Do not shorten or auto-cap client load from EchoMem's internal queue or worker
   configuration; those limits are part of the measured result.
6. **Explain**: open the HTML report and distinguish measured, partial, blocked,
   and inconclusive evidence. State the responsible module and the exact rerun
   condition for every incomplete metric.

For a capacity or maximum-tenant request, follow the two-run procedure in
`performance/targets/echomem/README.md`: first preserve the target version's
default scheduling configuration, then run a separately fingerprinted tuned
configuration that raises local admission, model, embedding, provider-budget,
and queue limits. Never merge the two result directories or describe the tuned
number as the default deployment baseline. If the team already has recorded
8/16/32/64 provider-concurrency evidence for the exact account, endpoint, and
model, reuse that evidence and run only the single-call identity/dimension
preflight; do not spend quota repeating the provider sweep.

The default first-pass capacity ceiling is 32 tenants and 32 observed in-flight
requests. Pin the profile with:

```json
{
  "m1_tenant_levels": [1, 2, 4, 8, 16, 32],
  "m1_user_levels": [1, 2, 4, 8, 16, 32],
  "required_concurrency": 32,
  "required_embedding_model": "qwen3.7-text-embedding-flash"
}
```

Explain that 32 configured hot users and 32 observed simultaneous in-flight
requests are different facts. Report both. Users can append 64 and 128 levels
later. Record EchoMem concurrency and queue settings for diagnosis, but never
use them to lower the offered client load.

M3 must contain both equal-load fairness/priority evidence and a heterogeneous
tenant case. The default heterogeneous case applies Search weights `[8,4,2,1]`
and Commit weights `[1,2,4,8]` to four independent tenants, proving that tenants
can receive different Search and Commit intensities in one real run.

The final report must also expose three cross-metric audits:

- invalid-input coverage with every case and observed HTTP status;
- a required API ledger with method, path, exact/minimum call count, and missing
  endpoints kept visible;
- endpoint and service-returned module timing distributions. Never infer internal
  router, recall, scheduler, or engine timings by subtracting unrelated clocks.

For M1-M3, require EchoMem DEBUG JSON logging and a concrete
`resource_container`. Collect whitelisted `recall_stage_completed`,
`recall_engine_completed`, `dashscope_rerank_operation`,
`memory_extraction_completed`, and `atomic_pipeline_completed` events for the
bounded run window. Correlate response traces using hashed trace references and
report observations, P50/P95/P99, and queue-wait percentiles per stage. Independently
summarize the seven supported Prometheus histograms from window deltas and show
log/metric coverage side by side. A stage is unobservable only when neither source
contains a real sample; never derive stage time by subtracting end-to-end values.

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
