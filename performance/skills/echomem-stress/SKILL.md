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

## Portable chart-report workflow

For Kimi, Codex, or another local coding assistant, read
`references/chart-report-workflow.md` before executing or regenerating a report.
These are repository-relative instructions, not Codex-tool dependencies. The
assistant needs local file and shell access; the assistant model is independent
of the LLM/Embedding used by the EchoMem service under test. A report-only request
must not start a new paid workload. Select the generator by evidence type:
M1-M6 uses the canonical suite; the bounded Commit diagnostic uses
`scripts.build_commit_diagnostic_report`, never as a substitute for six metrics.
Deliver the generated HTML path, evidence scope, and verification result, not
just a prose summary or raw JSON.

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
  If an output has no `report.html`, stop and report `WRONG_ENTRYPOINT`; never
  present another HTML artifact as the current six-metric result.
- Default to M1, M2, and M3 when the user asks for a stress test without naming
  a scope. Do not run M4, M5, or M6 unless the user explicitly requests those
  metrics or a full M1-M6 run. The `full` wrapper remains an explicit M1-M6 run.
- Treat `<OUTPUT>/report.html` as a live artifact. Update or regenerate that same
  file after preflight, memory seeding, every completed M1 level, every M2/M3
  scenario, every M4 fault phase, every M5 recovery sample, and final M6
  collection. Clearly label unfinished metrics as running, partial, blocked, or
  not selected; never wait until the entire run ends before publishing the first
  report. After each checkpoint, tell the user the report path, update time, and
  newest measured denominator without flooding chat with per-request messages.
- When one run selects M1 together with M2/M3, reuse M1 cross-tenant actors whose
  Commit and recall validation completed. The shared seed must report
  `seed_source=validated-identity-cache`; treating the same identities as a new
  seed is a harness orchestration defect, not useful additional coverage.
- Treat the repository report generator as the single implementation of visual
  layout. Do not hand-build a second HTML report in chat or with an ad-hoc
  script. Read `references/interactive-workflow.md#8-report-display-contract`
  before presenting results. If persisted evidence exists but a required card,
  chart, denominator, failure class, or raw-evidence link is absent, classify it
  as `REPORT_CONTRACT_GAP` and fix the generator; never fill the gap with an
  inferred value or a separate report file.

## Interactive flow

1. Locate the harness and EchoMem repositories. Prefer paths supplied by the
   user. Show their current branch, commit, dirty state, and whether the harness
   contains `performance/targets/echomem/observation_run.py`. Fetch branch refs
   only with authorization, then verify the selected EchoMem revision against
   `origin/develop`; never infer the revision from the directory name.
2. Inspect prerequisites and present a compact readiness summary: EchoMem ready,
   Docker/container, profile, tenant count, LLM preflight, Embedding preflight,
   protected test endpoints, output directory, and destructive-test safety.
3. If the requested scope is not already clear, default to M1-M3. Use full
   M1-M6, one metric, resume, or report-only only when the user explicitly
   selects that scope.
4. Preview the exact command, selected metrics, estimated destructive actions,
   and output directory. Obtain explicit user authorization before M4 fault
   injection, M5 kill/restart, root login, or use of remote/shared resources.
5. Run preflight, then execute the repository entry point. Keep the process
   attached and report meaningful stage changes. Do not leave a required process
   running without tracking it.
6. On completion, inspect `execution-manifest.json`, `summary.json`, `suite.json`,
   and `report.html`. Open the HTML report and summarize every selected metric's
   status, denominator, primary number, and responsible module.
   Before interpreting results, verify `<OUTPUT>/report.html` exists. No other
   HTML filename is a valid substitute for the current M1-M6 report.

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

### High-concurrency EchoMem configuration reminder

Before a PR33-style comparison (M1 `C=1` baseline followed by `C=64`, or a
larger client target), explicitly show the resolved EchoMem service settings
and remind the user which server-side gates must be audited. The harness must
never lower the offered client load because one of these gates is small, and it
must not silently change the service configuration. Keep the default and
tuned deployments in separate output directories and configuration
fingerprints; tuning is for a dedicated test deployment only.

Audit at least the following fields against the target version's schema and
startup validation:

- HTTP and Retrieval admission: `scheduling.http.max_workers` and
  `scheduling.retrieval.admission_permits` (the previous 4:1 C=64 starting
  check was 256 HTTP workers for 64 admission permits; a service-side target
  of 600 used 2400/600 in the PR33 test and is a separate contract).
- Recall's outer gate: `recall.max_inflight` and
  `ECHOMEM_RECALL_MAX_INFLIGHT`; verify environment overrides do not replace
  the JSON value unexpectedly.
- Tenant limits: `scheduling.tenant.concurrency` and
  `scheduling.tenant.qps`, including the single-hot-tenant case.
- Recall stage pools and queues: `recall.concurrency.engine`,
  `intent_llm`, `query_embedding`, and `rerank` `max_concurrent`,
  `queue_capacity`, and `max_queued_per_tenant`.
- Fanout and model pools: `scheduling.fanout.executor_workers` and
  `engine_max_inflight`; LLM/Embedding total pools, stage shares, and
  `provider_budget_llm`/`provider_budget_embed`. Preserve the version's
  executor-to-inflight relationship; do not blindly set every worker to 600.
- Commit: `scheduling.commit.executor_workers`, `gate_workers`, `queue_max`,
  `tenant_quota`, `tenant_inflight_max`, plus
  `commit_pipeline.queue_max` and `tenant_quota`. A 202 response still needs
  terminal polling.
- Tenant cache and host limits: `scheduling.tenant_cache.max_cached_tenants`,
  `hard_cap`, container CPU/memory, file descriptors, and connection pools.

After changing a dedicated deployment, restart it and verify readiness,
`instance_profile_resolved`, `provider_budget_configured`, the effective
Recall limit, and the actual container resources. Run the same seed first at
`C=1` and then at `C=64`; retain 429/503, provider errors, timeouts, empty
recalls, and pending work in the report. If startup validation rejects a
combination, report the exact field and constraint instead of weakening the
client target or deleting the failed sample.

The default first-pass capacity ceiling is 32 tenants. The default concurrency
topology targets 1, 8, 16, and 64 observed in-flight requests. Pin the profile
with:

```json
{
    "m1_tenant_levels": [1, 2, 4, 8, 16, 32],
    "m1_user_levels": [1, 2, 4, 8, 16, 32],
    "m1_concurrency_levels": [1, 8, 16, 64],
    "m1_concurrency_tenants": 4,
    "required_concurrency": 64,
    "required_embedding_model": "qwen3.7-text-embedding-flash"
}
```

Explain that configured actors and observed simultaneous in-flight requests are
different facts. The default concurrency topology targets total in-flight
levels 1, 8, 16, and 64 across four independent tenants; report the target and
actual peak separately. Users can append 128 later. Record EchoMem concurrency
and queue settings for diagnosis, but never use them to lower the offered
client load.

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

When the profile enables `concurrency_topology`, run the 1/8/16/64 matrix for
four real layouts: one session per user with serial session access, multiple
serial sessions per user, concurrent requests within one session, and unequal
small-Search/large-Message+Commit tenant load. Treat each level as target client
concurrency, not tenant count, and preserve required versus actual identities.
When `payload_boundary` is enabled, exercise Message, Commit, and Search from 0
through 1 MiB as JSON text and raw binary, poll the oversized Commit to a terminal
state, and call the configured Streamable HTTP `add_memory` tool. Missing MCP
configuration is BLOCKED; never substitute an HTTP message and call it MCP.

For M1-M3, require EchoMem DEBUG JSON logging and a concrete
`resource_container`. Collect whitelisted `recall_stage_completed`,
`recall_engine_completed`, `dashscope_rerank_operation`,
`memory_extraction_completed`, and `atomic_pipeline_completed` events for the
bounded run window. Correlate response traces using hashed trace references and
report observations, P50/P95/P99, and queue-wait percentiles per stage. Independently
summarize the seven supported Prometheus histograms from window deltas and show
log/metric coverage side by side. A stage is unobservable only when neither source
contains a real sample; never derive stage time by subtracting end-to-end values.

The fixed `4U8G` resource check is platform-specific: enforce the exact
4-CPU/8-GiB container contract only when the runner host is Linux. On macOS or
Windows, keep the target running, record the host/container resource evidence,
and run HTTP metrics without blocking on a 4U8G cgroup mismatch. The report
must state that the numeric 4U8G check was skipped on a non-Linux host.

## Commands

Run from the harness repository root with a profile and a secret env file that
are outside Git:

```bash
performance/targets/echomem/run_six_metrics.sh full PROFILE OUTPUT ENV_FILE
performance/targets/echomem/run_six_metrics.sh full PROFILE OUTPUT ENV_FILE

.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE --env-file ENV_FILE --out-dir OUTPUT

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
