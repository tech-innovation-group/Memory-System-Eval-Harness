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

If setup is missing, direct the user to the single local guide:
`performance/targets/echomem/README.md`. Do not invent a second deployment path.

## 2. Scope chooser

Do not ask again when the user already named metrics or a mode. Otherwise offer:

1. **Quick chain check (recommended on a new machine)**: real HTTP and real
   providers with shortened sampling. It validates wiring, not capacity.
2. **First three metrics**: M1 capacity, M2 fairness, and M3 Search priority.
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
  --metrics M1,M2,M3 \
  --env-file ENV_FILE \
  --out-dir OUTPUT
```

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

Send updates only at meaningful transitions: preflight, memory seeding, each M1
level, M2 tenant tier, each M3 flood mode, M4 fault phase, each M5 recovery sample,
and final M6/report assembly. Include completed/total work, current denominator,
latest P95/error count, and the output path. Do not flood chat with every request.

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
