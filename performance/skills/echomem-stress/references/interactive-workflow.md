# EchoMem Stress Interactive Workflow

This reference defines the product experience for guiding another engineer from
an unknown local machine to a reproducible M1-M6 HTML report.

## 1. Start screen

Begin with facts, not a long questionnaire. Inspect the machine and show:

| Check | Ready when |
| --- | --- |
| Harness | repository found; branch, commit, and dirty state recorded |
| EchoMem | repository/deployment found; `/api/v1/system/ready` succeeds |
| Runtime | Python 3.11+ dependencies installed; Docker reachable when needed |
| Real models | both LLM and Embedding preflights succeed |
| Tenants | independent credentials exist; 32 recommended for capacity steps |
| Control plane | fault and tenant-observability endpoints authenticate |
| Recovery target | a dedicated local container is identified for M5 |
| Output | new directory, or an existing directory explicitly selected to resume |

Use these states: `READY`, `NEEDS_SETUP`, `BLOCKED`, and `DANGEROUS_TARGET`.
Never echo secret values while checking them.

When the profile pins `required_embedding_model`, compare the successful
Embedding preflight model name exactly. For the current formal profile the value
is `qwen3.7-text-embedding-flash`; a working request to a different model is not
an acceptable substitute.

If setup is missing, direct the user to the single local guide:
`performance/targets/echomem/README.md`. Do not invent a second deployment path.

## 2. Scope chooser

Do not ask again when the user already named metrics or a mode. Otherwise use
M1-M3 as the default scope. The available explicit alternatives are:

1. **Quick chain check (recommended on a new machine)**: real HTTP and real
   providers with shortened sampling. It validates wiring, not capacity.
2. **First three metrics (default)**: M1 capacity, M2 fairness, and M3 Search priority.
3. **Full M1-M6**: includes tenant fault injection and real container restart.
4. **Single metric**: accept one or more of `M1` through `M6`.
5. **Resume**: continue the same profile and output directory with `--resume`.
6. **Report only**: rebuild or inspect existing evidence without new load.

Before execution, display a concise preview:

```text
Target:        EchoMem <branch>@<commit>
Harness:       <branch>@<commit>
Scope:         M1,M2,M3
Models:        <llm-name> / <embedding-name> (preflight passed)
Tenants:       32 independent credentials
Resources:     observed Docker limits, or host-default
Destructive:   none | tenant fault | container kill/restart
Output:        <absolute path>
```

Also display the distinction between configured actors and measured overlap:

```text
Hot-user levels:       1,2,4,8,16,32
Required concurrency:  32 simultaneous in-flight requests
EchoMem limits:         observed and reported; not used to cap client load
Heterogeneous tenants: Search weights 8:4:2:1 / Commit weights 1:2:4:8
```

M4 and M5 must target a dedicated test deployment. Remote login, shared compute,
fault injection, and container kill/restart require explicit authorization for
that run; having this skill installed is not authorization.

## 3. Local preparation

Use the checked-out code's own configuration:

1. Deploy EchoMem from its repository, copying its current
   `configs/config.example.json`. Change credentials through environment variables;
   do not substitute a harness-owned config template.
2. Enable the protected test control only on the dedicated test deployment with
   `ECHOMEM_TEST_CONTROL_ENABLED=true` and a random
   `ECHOMEM_TEST_CONTROL_TOKEN` present on both service and runner sides.
3. Confirm these endpoints exist for full coverage:
   `GET/POST /api/inspect/test-control/fault` and
   `GET /api/inspect/tenant-observability`.
4. Provision independent tenants:

```bash
.venv/bin/python -m performance.targets.echomem.provision \
  --base-url http://127.0.0.1:8010 \
  --count 32 \
  --out .local-stress/tenants.json \
  --env-file .local-stress/test.env
chmod 600 .local-stress/tenants.json .local-stress/test.env
```

5. Create one local profile by following
   `performance/targets/echomem/README.md`. Use absolute paths, set
   `require_4u8g` according to the actual test objective, and identify the
   dedicated recovery container. Keep profiles and env files out of Git.

Run a quick chain check before a formal run:

```bash
performance/targets/echomem/run_six_metrics.sh quick \
  .local-stress/six-metrics.profile.json \
  results/local-six-metrics-quick \
  .local-stress/test.env
```

Quick results are `PARTIAL` by design. They must never be reported as a capacity
boundary or formal acceptance result.

After the command exits, verify the report contract:

```bash
test -f "OUTPUT/report.html"
```

If `OUTPUT/report.html` does not exist, classify the run as `WRONG_ENTRYPOINT`
and rerun with the commands in section 5; do not reinterpret or rename another
HTML artifact as the current M1-M6 report.

## 4. Six metric test cases

### M1: Capacity, hot users, and DAU

Pre-seed each tenant with a unique natural-language fact and verify that Search
can retrieve it. Increase tenant count and hot users per tenant in configured
steps. At each level run real Search, Commit, and mixed traffic, then record P50,
P95, P99, throughput, error classes, recall numerator/denominator, CPU, memory,
pending Commit depth, and recovery after load stops.

The boundary is the last sustainable level followed by the first level with
persistent blocking, request failure, crash, OOM, or backlog that does not drain.
An API/provider error is a failure type, not proof of EchoMem capacity. Convert
the measured peak throughput into read-heavy, balanced, and write-heavy DAU
estimates; label these as model-based conversions rather than measured users.

For the default 32-tenant target, distinguish these measurements:

- configured active tenants or hot users at the 32 level;
- actual peak simultaneous in-flight HTTP requests;
- the last level whose backlog drains after load stops;
- the first level with persistent blocking, timeout, rejection, crash, OOM, or
  non-draining backlog.

Do not read EchoMem `max_concurrency`, queue capacity, or worker count and reduce
the generator target. Capture those settings in the report as explanatory
evidence. A service-side rejection or queue limit is a measured boundary result.
Users may append 64 and 128 levels after the first pass without changing the test
logic.

### M2: Equal-tier fairness

Run 4 and 8 independently authenticated tenants with equal offered Search and
Commit load. Each Commit uses its own session and terminal-state polling. Report
per-tenant Commit completions/second and Search P95. Compute Jain separately for
Commit throughput and the inverse of Search latency so that larger means better.
List zero-completion tenants explicitly. The equal-weight Jain denominator must
not include duplicate credentials masquerading as tenants.

### M3: Search priority under Commit floods

Measure a pre-seeded hot-memory Search baseline, then repeat the same Search
queries while real Commits remain outstanding. Run both:

- `m3-flood-uniform`: Commit load spread across all tenants.
- `m3-flood-single-tenant`: one tenant produces all Commits while every tenant
  continues Search, exposing noisy-neighbor coupling.

Count only Search samples whose timestamps overlap confirmed unfinished Commits.
Report baseline and overlap-window Search P95/P99, degradation ratio, errors,
recall quality, and Commit planned/202/rejected/completed/non-terminal counts.
This scenario measures cross-operation priority; it does not replace M2 fairness.

Run an additional `m3-heterogeneous-tenants` case with four independent tenants.
Apply Search weights `8:4:2:1` and Commit weights `1:2:4:8`, then report each
tenant's configured weight, planned rate, actual arrivals, Search P95/errors and
recall quality, and Commit completions. This verifies that one run can model a
read-heavy tenant, two intermediate tenants, and a write-heavy tenant instead of
assuming all tenants have identical request costs and rates.

### M4: One-tenant fault isolation

For each repeat, measure bystander tenants before the fault, inject `delay` and
then `reject` into one authenticated tenant, keep bystanders searching during the
fault, clear it, and measure recovery. Report every bystander's before/during/after
P95, error rate, and percentage degradation. The target tenant must be excluded
from the bystander denominator. A control endpoint response alone is not evidence;
the workload must show that the target fault was exercised.

### M5: Accepted Commit crash recovery

Submit a uniquely marked Commit with an idempotency key. Only after the service
returns 202 and before terminal completion, kill the dedicated EchoMem container
or process. Restart it and poll the original operation without creating a
replacement. Repeat the same idempotency key, then reconcile Commit status,
history, archive, and cursor. Report accepted/recovered samples and exact missing,
duplicate, or reordered message IDs. Passing requires every configured sample,
not only the successful subset.

### M6: Per-tenant lane observability

Sample the protected observability endpoint throughout M1-M5 and deliberately
cover normal execution, queueing, rejection, reset, and restart generations.
For every observed `tenant_id x lane`, require queue depth, total wait duration,
total execution duration, and rejection count. Report expected versus observed
cells, missing frames, negative/non-monotonic values, reset events, and whether
all active tenants and lanes are represented. Metrics-family existence alone is
not full M6 coverage.

## 5. Commands

Formal full run:

```bash
performance/targets/echomem/run_six_metrics.sh full PROFILE OUTPUT ENV_FILE
```

First three or selected metrics:

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE \
  --env-file ENV_FILE \
  --out-dir OUTPUT
```

Omitting `--metrics` intentionally selects `M1,M2,M3`. Use an explicit metrics
list for every other scope. The `full` wrapper explicitly selects all six.

Resume in the same output directory:

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE \
  --env-file ENV_FILE \
  --out-dir OUTPUT \
  --resume
```

Never overwrite an old result with a fresh run. Use a new output directory unless
the user explicitly selected `--resume` with the same profile and metric set.

## 6. Progress presentation

Keep one live report at `OUTPUT/report.html`. Create it after preflight and
refresh it after memory seeding, each M1 level, each M2 tenant tier, each M3 flood
mode, each M4 fault phase, each M5 recovery sample, and final M6 collection. Each
refresh must use persisted evidence, retain the full denominator, and label
unfinished metrics as running, partial, blocked, or not selected. Verify the
file modification time advances and tell the user the path, update time,
completed/total work, current denominator, latest P95, and error count. Do not
flood chat with every request or wait until the whole run finishes to publish.

When an error occurs, continue independent metrics when safe and classify it as:

- external provider: authentication, balance, quota, rate limit, model timeout;
- deployment/control: readiness, missing token, protected endpoint, container;
- EchoMem Search/Recall, routing/admission, Commit/recovery, engine, tenant
  isolation, or observability;
- harness execution or evidence defect.

Provider failure blocks recall-dependent conclusions but does not erase valid M5
recovery or M6 control-plane evidence. A missing test-control endpoint blocks M4
or M6; do not call it an EchoMem performance failure.

## 7. Report delivery

Inspect and preserve:

- `execution-manifest.json`: versions, config fingerprint, provider preflight;
- `summary.json`: structured M1-M6 results and denominators;
- `suite.json`: case/probe evidence;
- `records.csv` and `metrics_samples.csv`: raw request and resource samples;
- `report.html`: visual report with test method after each metric.

Open `report.html` for the user. Put the overall conclusion first, then M1, M2,
M3, M4, M5, M6. For every selected metric state what it reflects, how it was
tested, the measured denominator, charts/data, status, failure types, and concrete
improvements grouped by EchoMem modules: external providers, Search/Recall,
routing/admission, Commit/recovery, memory engine, tenant isolation/control,
observability, and harness/deployment.

Add these report-wide audits after the six metric sections:

1. **Invalid-input matrix**: exercise missing/invalid auth, malformed JSON,
   missing/invalid Search fields, invalid session operations, unknown Commit
   status/memory/history/archive/cursor targets, invalid filesystem URI, and
   protected endpoints without a token. Show every case and status; do not report
   only the successful subset.
2. **API call ledger**: list every six-metric runtime endpoint with HTTP method,
   path, exact or minimum observed call count, and coverage state. Product APIs
   unrelated to M1-M6 are outside this ledger and must be labeled as such.
3. **Module timing evidence**: chart HTTP endpoint P50/P95/P99 and any explicit
   timing fields returned by EchoMem. Mark router, recall, admission, Commit, and
   atomic-engine stages as unobservable when the service does not expose them;
   never manufacture stage timing through subtraction.

## 8. Report display contract

The repository report generator, not the agent, owns HTML structure, styling,
charts, status calculation, and escaping. The agent owns timely regeneration,
validation, opening the result, and a concise explanation. Do not create a
parallel `*-explained.html`, rename another artifact to `report.html`, or paste
secret/raw payloads into the page.

The top of `report.html` must make the run understandable without opening raw
JSON. Show, in this order:

1. an overall conclusion naming the measured boundary and the largest blocker;
2. generation/update time and run state (`RUNNING`, `PARTIAL`, `PASS`, `FAIL`,
   `BLOCKED`, `INCONCLUSIVE`, or `NOT_SELECTED`), with text as well as color;
3. EchoMem/harness commits, profile/config fingerprint, real model names, actual
   resource limits, selected metrics, elapsed time, and completed/total work;
4. provider and deployment preflight state without secret values.

Every metric section must keep the same reading order:

1. **What it reflects** and **how it was tested** in plain language.
2. A status card with the primary value, numerator/denominator, latest completed
   level or phase, errors, and confidence limitation.
3. A chart for comparison or trend, followed by the exact-value table used to
   draw it. Tooltips or labels must expose exact values; charts never replace
   denominators.
4. Failure classes split into EchoMem, external provider, deployment/control,
   and harness/evidence causes. Keep unknown failures visible.
5. Concrete improvement suggestions grouped by the responsible EchoMem module,
   plus the exact rerun condition for partial or inconclusive evidence.
6. Links to the relevant persisted JSON/CSV/log evidence using relative paths.

Use these metric-specific visuals and tables:

| Metric | Required visual | Required exact data |
| --- | --- | --- |
| M1 capacity | load-level lines/bars for Search P95/P99, strict-success throughput, error rate, CPU, RSS, and Commit backlog | configured tenants/hot users, observed peak in-flight, Search sent/strict-success/quality-fail/non-200/transport error, recall hits/attempts, Commit planned/202/completed/failed/pending, backlog drain result, provider errors, first blocking level |
| M2 fairness | per-tenant Commit throughput and Search P95 bars, plus Jain summary | credential-unique tenant count, offered/actual Search and Commit per tenant, completions, errors, inverse-latency input, both Jain numerators/denominators, zero-completion tenants |
| M3 priority | baseline versus overlapping-flood Search P95/P99 for uniform, single-tenant, and heterogeneous cases | confirmed unfinished-Commit overlap window, overlapping Search count, recall hits/attempts, Commit planned/202/rejected/completed/non-terminal, per-tenant configured weights and actual arrivals |
| M4 isolation | each bystander's before/during/after Search P95 and degradation percentage | injected tenant and fault type, control response, exercised-fault evidence, bystander-only denominator, errors and recovery samples for every repeat |
| M5 recovery | acceptance-to-recovery funnel and per-sample outcome table | planned, 202 accepted, killed while non-terminal, recovered terminal, replayed idempotency key, history/archive/cursor missing/duplicate/order mismatches; failed samples remain in the denominator |
| M6 observability | tenant-by-lane coverage matrix and queue-depth/wait/execute/reject charts | expected and observed cells, sample timestamps, queue depth, wait/execute totals or deltas, rejected count, missing/non-monotonic/reset frames, generation/restart boundaries |

After M1-M6, render the invalid-input matrix, API call ledger, Search/Recall and
Commit/Atomic module timing distributions, and raw artifact index. For module
timings, show observation count and P50/P95/P99 plus queue wait when available;
identify whether each value came from a trace-correlated JSON log or a Prometheus
window delta. Never mix endpoint latency, model latency, and internal stage time
in one unlabeled series.

On every live refresh, preserve completed sections and prior denominators. The
agent must verify that `report.html` exists, its modification time advanced, its
displayed checkpoint matches persisted evidence, and no selected metric silently
disappeared. If the generator cannot render an available field, report
`REPORT_CONTRACT_GAP`, patch the canonical generator, regenerate the same file,
and rerun its focused tests before presenting the result.
