# EchoMem stress testing

`echomem/runner.py` is a real HTTP runner for an EchoMem service. It uses
Python's standard library and does not replace the real model service with a
mock during an official run.

## 上线前严谨压测方案

压测结果不能只用 `PASS/FAIL` 表示。正式报告必须同时保留实际提交数、
目标缺口、Commit/Search 的平均、P50、P90、P95、P99、最大耗时、逐租户
数据、慢请求发生时间、429/Retry-After、资源变化和原始请求证据。

### 1. 真实租户和隔离边界

每个租户必须使用独立 API Key，并尽量对应独立的 `account_id`、
`workspace_id` 或组织空间；只改变客户端 `tenant` 标签不算隔离。每个
租户写入 5 个随机唯一 marker，然后执行完整交叉矩阵：

```text
4 个写入租户 × 4 个读取租户 × 5 个 marker = 80 次探针
```

同租户必须全部命中，跨租户必须全部不命中。上线门槛是同租户漏读为 0、
跨租户误命中为 0，并且服务端能返回稳定租户身份。探针的 HTTP 200 不能
替代数据隔离结论。

### 2. 测试矩阵

先做单租户基线，再做四租户并发；每个场景至少重复 3 次，并按请求级
CSV 聚合，不使用“各轮均值的均值”代替总体统计：

| 场景 | 租户 | Search | Commit | 目的 |
| --- | ---: | ---: | ---: | --- |
| baseline | 1 | 2 RPS | 2 RPM | 单租户参考 |
| mixed | 4 | 8 RPS | 2 RPM/租户 | 常规混合负载 |
| commit-storm | 4 | 4 RPS | 10 RPM/租户 | Commit 排队和长尾 |
| search-storm | 4 | 20 RPS | 1 RPM/租户 | Search 峰值对 Commit 的影响 |
| soak | 4 | 8 RPS | 2 RPM/租户 | 长时间资源增长和退化 |

Commit 压力和 Search 压力还要分别做阶梯测试，例如 `2/5/10/20/50`
的 RPM 或 RPS；每一级记录目标数和实际发出数，未发出的请求不能隐藏。

### 3. 延迟归因和发生时间

每个请求必须保存客户端入队、发送、开始、完成时间，以及服务端的
`received_at`、`queue_entered_at`、`execution_started_at`、`finished_at`、
`queue_depth`、`active_workers`。报告按 60 秒窗口展示请求数、Commit
平均/P95、Search 平均/P95 和慢请求数，再关联到租户、时间和 Request ID。

这样可以区分：

```text
端到端慢 + 服务端排队慢       => 调度/队列问题
端到端慢 + 服务端执行慢       => Commit/Search 执行问题
客户端排队慢 + 服务端不慢     => 压测端 worker 或准入限制
```

没有服务端时序时，只能报告客户端端到端耗时，调度结论必须标为
`INCONCLUSIVE`，不能声称服务端是 FIFO 或 Search 优先。

### 4. 调度和限流必须分开测

`--scheduler-policy` 只控制压测端准入，不代表 EchoMem 服务端调度。
服务端调度观察要使用 `--no-client-admission`，并通过服务端时间戳验证
固定序列，例如：

```text
C1 -> C2 -> S1 -> C3 -> S2 -> C4 -> S3
```

根据服务端实际开始时间判断 FIFO、Search 优先或双通道并行；客户端发送
顺序不能作为服务端结论。

限流要用真实独立租户做单独的 Burst 和持续速率阶梯：

```text
Burst: 1/2/5/10/20/50/100
持续: 1/5/10/20/50/100 RPS
```

记录 HTTP 状态分布、429、`Retry-After`、实际吞吐、队列等待和其他租户
是否被连带影响。没有 429 只能写“本轮未观察到显式限流”，不能写成
“没有限流”。

### 5. 上线门槛

正式上线前至少要求：

```text
目标请求达成率 >= 95%
跨租户误命中 = 0
同租户漏读 = 0
独立认证身份映射可验证
服务端逐请求时序覆盖率 = 100%
错误和超时符合预设门槛
长稳态无持续内存增长
429/Retry-After 行为符合设计
```

任一项缺失或证据不足，结论为 `FAIL` 或 `INCONCLUSIVE`，不能只因请求
返回 HTTP 200 就判定通过。

The default authentication header is `X-API-Key`, matching the current
EchoMem HTTP server. Use `--auth-header Authorization` when the deployment
expects a bearer token, or set `ECHOMEM_AUTH_HEADER`.

```bash
python3 stress/echomem/runner.py \
  --base-url http://127.0.0.1:8010 \
  --auth-key "$ECHOMEM_AUTH_KEY" \
  --scenario all \
  --tenants 1 \
  --sessions-per-tenant 2 \
  --duration-s 300 \
  --search-rps 2 \
  --pid "$(pgrep -f echomem | head -1)" \
  --out-dir results/stress/echomem_$(date +%Y%m%d_%H%M%S)
```

Use repeated `--tenant` only when each label maps to an independently
authenticated EchoMem tenant. With one credential, the fairness result is
`INCONCLUSIVE`, not a pass.

For a formal multi-tenant run, `--tenant-config` is required. The runner
refuses `--tenants > 1` with a shared credential so a labeled single-tenant
run cannot be mistaken for an isolation result. Use
`--allow-shared-identity` only for explicitly exploratory, non-isolation
measurements.

The output directory contains `summary.json`, `report.html`, and CSV files for
commit, search, resource, and `/metrics` samples. Raw Prometheus responses are
also retained in `server_metrics.jsonl`. Transport/startup/authentication
failures are reported as `ENVIRONMENT_ERROR`.

For a compact, data-first report from an existing run, generate
`report_executive.html`:

```bash
python3 stress/echomem/executive_report.py \
  results/stress/echomem_YYYYMMDD_HHMMSS/summary.json \
  results/stress/echomem_YYYYMMDD_HHMMSS/report_executive.html
```

This report shows the actual target/request gap, Commit and Search
mean/min/P50/P90/P95/P99/max, per-tenant distributions, slow-request
timestamps, client queue time, operation/tenant ordering, 429 and
`Retry-After`, server-side telemetry coverage, identity/isolation evidence,
and the full request timeline. Missing server evidence is displayed as
missing; it is never inferred from client-side timestamps.

For a policy matrix, the runner also writes `matrix.json`, the legacy compact
`matrix.html`, the data-first `matrix-detailed.html`, and the number-first
auditing report `matrix-audit.html`. The auditing report shows Commit/Search
mean, P50/P90/P95/P99/max, per-tenant statistics, every delayed request's
timestamps and queue data, strategy semantics, and links to the raw CSV files.
It explicitly distinguishes client-side admission wait from server-side
queueing; without server telemetry it must not be used to claim server-side
rate limiting.

Example formal matrix command:

```bash
python3 stress/echomem/run_matrix.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.json \
  --tenants 4 \
  --duration-s 600 \
  --search-rps 2 \
  --commit-rpm 2 \
  --sessions-per-tenant 4 \
  --messages-per-session 3 \
  --out-dir results/stress/matrix_$(date +%Y%m%d_%H%M%S)
```

Do not use `--allow-shared-identity` for a release decision. It is only
allowed for exploratory performance measurements and makes isolation and
fairness conclusions `INCONCLUSIVE`.

## Real tenant isolation

Changing only `account_id` or a tenant label does not create a real tenant.
For an isolation or fairness conclusion, provide one independent EchoMem
credential per tenant:

```json
{
  "tenants": [
    {"tenant_id": "tenant-a", "user_id": "user-a", "auth_key_env": "STRESS_TENANT_A_KEY"},
    {"tenant_id": "tenant-b", "user_id": "user-b", "auth_key_env": "STRESS_TENANT_B_KEY"}
  ]
}
```

Run with `--tenant-config tenants.json`. The runner executes a marker probe:
the writer tenant must retrieve its own marker and the reader tenant must not
retrieve it. Without this file, multiple labels share one credential and the
tenant-isolation result is `INCONCLUSIVE`.

Start from `echomem/tenants.example.json`, then provide the four keys through
the environment:

```bash
export STRESS_TENANT_A_KEY='...'
export STRESS_TENANT_B_KEY='...'
export STRESS_TENANT_C_KEY='...'
export STRESS_TENANT_D_KEY='...'
python3 stress/echomem/runner.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.json \
  --no-client-admission \
  --duration-s 120 \
  --search-rps 2 \
  --sessions-per-tenant 2 \
  --messages-per-session 3 \
  --out-dir results/stress/server-observe-$(date +%Y%m%d_%H%M%S)
```

The report never writes the key itself. It records only the key source name,
tenant id, request order, and timing evidence.

## Scheduling mode

正式测试只有 `server-observe` 一个调度口径。压测端不做业务队列、不替
EchoMem 选择 FIFO、Search 优先、双通道或租户公平；它只负责按场景产生
真实并发请求，并记录客户端发送时间、端到端耗时、错误和服务端遥测。

报告仍可展示客户端 worker 等待、请求顺序和队列深度字段，但这些字段只
描述压测端观测，不能被解释成 EchoMem 的内部调度结果。Commit 状态轮询
不计入 Commit 写入请求的统计。
`--search-rps` is the total Search arrival rate for the whole run, not a
per-tenant rate. For example, with four tenants and a desired 0.5 RPS per
tenant, set `--search-rps 2`; the runner distributes arrivals round-robin and
the report shows the actual per-tenant counts. Set `--commit-rpm` to a positive
value for fixed-rate Commit arrivals per tenant. The runner prepares dedicated sessions before the timed interval and
then submits Commit requests at the configured rate; this is the recommended
mode for sustained load. With `--commit-rpm 0`, it retains the legacy
one-Commit-per-prepared-session mode.
The formal isolation probe writes five distinct random markers per tenant by
default and tests every directed writer/reader pair for every marker. With
four tenants this produces `4 x 4 x 5 = 80` probes. Change the count with
`--isolation-markers-per-tenant`; a release run should not reduce it to one
without documenting why.

For measuring EchoMem's own scheduler, disable the load generator's admission
controller:

```bash
python3 stress/echomem/runner.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.json \
  --tenants 4 \
  --no-client-admission \
  --commit-workers 64 \
  --search-workers 64 \
  --duration-s 600 \
  --search-rps 2 \
  --commit-rpm 2 \
  --out-dir results/stress/server-observe-$(date +%Y%m%d_%H%M%S)
```

`--no-client-admission` removes FIFO/Search-priority/tenant-fair gating from
the runner. The executor worker pool can still become a client-side bottleneck,
so its queue wait remains recorded and its worker count must be sized above the
expected in-flight load. Use this mode for service-side scheduling conclusions;
use the policy modes above only for comparing client-side request shaping.

## Formal release suite

For an online-readiness decision, use `formal_suite.py` instead of a single
short matrix run. It executes these cases:

- `baseline`: one independently authenticated tenant for the reference latency
- `mixed`: four tenants with balanced Search and Commit traffic
- `commit-storm`: four tenants with elevated Commit traffic
- `search-storm`: four tenants with elevated Search traffic
- `soak`: four tenants under a longer steady-state load

Each case runs in `server-observe` mode. The load generator does not apply
FIFO, Search-priority, dual-lane, tenant-fair, or dual-lane-tenant-fair
admission; it sends concurrent real HTTP requests to EchoMem and records the
service-side evidence. This matches the production topology, where online users
do not pass through this test platform's client admission controller. The
default is three repetitions per case. Every run retains `summary.json`,
request CSVs, raw `/metrics`, and its own `report.html`; the suite-level
`suite.html` contains the numeric comparison table.

The suite automatically enables `--no-client-admission` in the default
`server-observe` mode. Set `--commit-workers` and `--search-workers` high
enough that the test platform's worker pool is not the intended bottleneck;
the report still records any client executor wait separately.

```bash
python3 stress/echomem/formal_suite.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.server.json \
  --out-dir results/stress/formal_$(date +%Y%m%d_%H%M%S) \
  --repeats 3 \
  --pid "$(pgrep -f echomem | head -1)" \
  --reset-command '/path/to/reset-echomem-test-data.sh'
```

The reset command is optional but strongly recommended. It must restore the
same EchoMem version, configuration, index/data snapshot, and resource
limits before every case; otherwise growing indexes or stale memory can be
confused with a scheduling effect. The suite does not claim that a client-side
policy is EchoMem's internal scheduler. Service-side rate limiting requires
server telemetry such as queue depth, execution start time, HTTP 429, and
`Retry-After`; those values are retained when the service exposes them.

The generated `suite.html` is data-first. It shows numeric distributions for
every scenario, including Commit and Search mean/P50/P95/P99/max,
client admission wait, server queue wait, server execution time, per-tenant
counts and quantiles, delayed requests, HTTP 429, and telemetry coverage.
Expand a row to inspect every delayed request and the raw CSV files. Missing
server timestamps are rendered as `-`; they are never replaced with
client-side timing.

The formal suite already disables the runner's admission controller. No
additional client-policy run is needed for the platform test. The explicit
command is:

```bash
python3 stress/echomem/formal_suite.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.server.json \
  --out-dir results/stress/formal_server_observe_$(date +%Y%m%d_%H%M%S) \
  --repeats 3 \
  --no-client-admission \
  --commit-workers 64 \
  --search-workers 64
```

The report labels this mode explicitly. It is only a server-side queueing
conclusion when EchoMem provides per-request `received_at`,
`queue_entered_at`, `execution_started_at`, `finished_at`, `queue_depth`, and
`active_workers`. Without those fields, the report still shows client
observations but marks server scheduling evidence as missing.

## Rate-limit boundary probe

Use `rate_limit_probe.py` to test the service's actual admission boundary with
independent tenant credentials. It sends a simultaneous burst per tenant and
records each HTTP status, latency, request ID, `Retry-After`, and the stable
identity returned by the service. It does not add a client-side token bucket,
so a missing `429` is reported as "not observed", not as proof that no limit
exists.

Search burst:

```bash
python3 stress/echomem/rate_limit_probe.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.server.json \
  --tenants 4 \
  --operation search \
  --burst-count 50 \
  --workers 64 \
  --out-dir results/stress/rate_limit_search_$(date +%Y%m%d_%H%M%S)
```

Commit acceptance burst:

```bash
python3 stress/echomem/rate_limit_probe.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config stress/echomem/tenants.server.json \
  --tenants 4 \
  --operation commit \
  --burst-count 20 \
  --workers 32 \
  --out-dir results/stress/rate_limit_commit_$(date +%Y%m%d_%H%M%S)
```

Run the probe at increasing burst sizes, for example `1, 2, 5, 10, 20, 50`
per tenant. The report should show the first burst that returns `429`, the
per-tenant success/429/error counts, the `Retry-After` distribution, and
whether one tenant's limit affects the other tenants.

## Docker isolation

The runner can be executed as a disposable container so each test gets a
clean process, filesystem, and output directory. `network_mode: host` is used
on Linux because EchoMem is commonly bound to `127.0.0.1:8010` on the server;
the runner container still does not share EchoMem's filesystem or Python
environment. The compose service uses the host PID namespace only so the
runner can sample the target process's RSS/CPU when `--pid` is supplied.

```bash
cd /path/to/Memory-System-Eval-Harness
export ECHOMEM_BASE_URL=http://127.0.0.1:8010
export ECHOMEM_AUTH_KEY='set-on-server'
export STRESS_DURATION_S=300
export STRESS_OUTPUT_DIR=/var/lib/echomem-stress/results
RUN_ID=$(date +%Y%m%d_%H%M%S) ./stress/deploy_stress.sh
```

Each invocation builds the runner image, starts one disposable container,
writes results to `STRESS_OUTPUT_DIR/echomem_<RUN_ID>`, and removes the
container after completion. Do not put API keys in compose files or commit
them to git.
