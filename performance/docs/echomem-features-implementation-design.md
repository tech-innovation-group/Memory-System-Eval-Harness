# 性能压测顶层设计的具体实现（performance/ 模块 + PR397 方案落地）

> 本文档是顶层设计意图（`echomem-features-under-test-design.md`）的**具体实现**：
> 把每个顶层测试目标落到场景、参数、工具、数据与判定流程。
> PR397 方案（`pr397_stress_plan_explained_20260826.html`）是本文档的一个具体实例——
> 其每个场景都能在此找到归类槽位；本实现按 PR397 的结论做了相应扩展。
> 模块级整体架构见 `../../docs/performance-stress-test-design.md`。

## 1. 目标与定位

本实现分两类能力：

- **已实现**（现有 `performance/` 代码，`tests/test_performance.py` 已覆盖）：检索、
  写事务四段、场景矩阵 A/B/C/D、双视角延迟、durability、公平性、RSS 趋势、资源时间线、
  一致性探测、种子、报告；
- **扩展能力**（顶层新增目标的具体实现，均已落地代码与配套测试）：
  写事务重试、消息对账去重、search 质量断言、隔离细粒度、RSS 归一校正、故障注入、
  模型与配置预检门禁、错误类型正确性、判定分层与 SLO 口径。

已实现部分与扩展部分遵循同一接口契约：客户端埋点 → 统计纯函数 → 判定 → 报告。

## 2. 实现模块与工具链

| 模块 | 职责 | 现状 |
|---|---|---|
| `backends/echomem/client.py` `EchoMemClient` | 服务端 REST 入口（search / 写事务 / 身份 / health） | 已实现 |
| `prepare.py` `TenantPreparer` | 租户身份与种子数据（synthetic 锚词 / locomo 复制式） | 已实现 |
| `loadgen.py` `LoadGenerator` | 并发注入：读/写 worker、burst 池、`RateLimiter`、逐请求 `RequestRecord` 埋点 | 已实现；扩展重试与质量断言字段 |
| `monitor.py` `MetricsMonitor` | `/metrics` 周期采样 + Prometheus 文本解析 + 派生统计 | 已实现 |
| `metrics_calc.py` | 统计纯函数与特性判定 | 已实现；扩展对账/归一/错误类型 |
| `run_stress.py` | 入口编排（预检→准备→场景→冷却→报告） | 已实现；扩展预检门禁与故障注入编排 |
| `report.py` | 产物（summary.json / requests.csv / metrics_samples.csv / report.html） | 已实现；扩展判定分层渲染 |

新增能力（已实现）：
`perf_mock_provider.py`（故障注入 mock 代理）、`perf_preflight.py`（模型/配置预检门禁）、
对账与重试逻辑（并入 loadgen/metrics_calc）。

## 3. 场景矩阵与 PR397 映射

### 3.1 场景矩阵

| 场景 | 负载 | 顶层目标 | PR397 对应 |
|---|---|---|---|
| A 纯读基线 | 全部线程 search | 2.1 检索基线 / 2.5 劣化对照 | S1（读侧） |
| B 纯写 | 全部线程完整写事务（含重试语义）+ 尾段消息对账与一致性探测 | 2.2 / 2.3 / 2.4 / 2.9 / 2.10 | S1（写侧）/ S2 |
| C 读写混合 | 按 read:write 比例分配线程 | 2.5 | S3 |
| D 注入洪峰 | 读持续 + 短窗口 K 个并行写事务；read 按同租户/跨租户分组统计 | 2.5（细粒度）/ 2.8 | S3 |
| F 故障注入 | mock provider 下注入模型故障序列（独立流程，不并入并发矩阵） | 2.10 / 2.11 | S4 |
| 长跑 | 长 `--duration-s` 混合负载 + 每 10 分钟资源快照 | 2.7 / 2.8 | S6 |

### 3.2 PR397 场景归类

| PR397 场景 | 归类槽位 | 说明 |
|---|---|---|
| S1 基线（单租户 30min 持续写+查） | A + B，`--tenants 1 --duration-s 1800` | 顶层长基线 |
| S2 多租户 commit 风暴（429 + Retry-After 退避重试） | B 高并发档 + 写事务重试语义 + 消息对账 | 见 §4.1 / §4.2 |
| S2b auto-commit 内部退避 | **不测** | auto-commit 后续关闭，顶层明确排除 |
| S3 commit/search 隔离（同/跨租户 + 质量断言） | D 场景 + §4.3 / §4.4 | 顶层 2.5 细粒度 |
| S4 模型故障注入 | F 场景（mock provider） | 见 §4.6 |
| S5 重启恢复（local/cluster） | **当前未纳入** | 顶层未定义该目标；如需，按 local/cluster 分模式新增 |
| S6 2h 混合长跑泄漏 | 长跑 + §4.5 RSS 归一校正 | 顶层 2.7 |

## 4. 各测试目标的具体实现

### 4.1 写事务重试（背压：429 + Retry-After 退避重试）

**语义**（对应顶层 2.2 / 2.3a / 2.4）：

- 写事务四段中，`commit_submit` 收到**可重试拒绝**（HTTP 429 + `Retry-After` 头）时，
  解析 `Retry-After`（秒），`sleep` 退避后重试提交；重试有上限
  （`--commit-retry-max`，默认 3 次）；
- 收到**不可重试失败**（业务类 4xx、终态 failed）不重试，直接计失败；
- 5xx / 连接错误 / 超时 按可重试策略退避重试（同上限），超出上限计「重试耗尽失败」；
- 其余段（open / add）失败即中止事务（与现状一致）。

**埋点扩展**（`RequestRecord` 新增字段）：

- `retry_count`：该请求重试次数；
- `retried`：是否发生过重试；
- `retry_total_wait_ms`：退避等待总时长；
- `final_success`：重试后最终是否成功。

**判定**：

- 背压场景下，429 出现**不是失败**；只要拒绝原因正确（带 `Retry-After`）、
  退避重试后最终成功、消息最终落库，即判定通过；
- 正确写入 = 最终提交成功（`final_success`）+ 消息对账通过（§4.2）；
- 报告同时输出**原始值**（首次提交成功率）与**重试后值**（最终成功率）。

### 4.2 消息对账与去重

**流程**（对应顶层 2.3b，B 场景尾段执行）：

1. **建立消息全集清单**：写负载逐消息埋点 `message_id` / `content_hash` / 锚词，
   形成本场景写入的消息全集 `M`；
2. **采集对账数据**：commit 完成后，拉取
   - committed cursor（会话提交游标，覆盖哪些消息）；
   - atom 的 `source_turn_ids`（抽取结果引用了哪些消息 id）；
   - archive 终态（completed / failed）；
3. **交叉核验**：
   - `M` 全量 ⊆ committed cursor 覆盖范围；
   - `M` 全量 ⊆ 全部 atom `source_turn_ids` 的并集；
   - `source_turn_ids` 无重复项（**无重复 transcommit**）；
   - 每条消息对应 archive 终态为 completed；
4. 任一核验失败 → `正确写入` FAIL，输出缺失/重复的消息 id 清单。

**数据源**：服务端会话/commit/atom 查询接口 + 客户端逐消息埋点。

### 4.3 search 质量断言

**语义**（对应顶层 2.5）：

- `read` 埋点扩展字段：
  - `hit_count`：本次 search 返回条数；
  - `real_recall`：是否走了真实召回（响应确实来自 recall 路径，非短路空响应）；
  - `model_called`：RAG/模型路径上模型请求是否发生（按配置可豁免，豁免时注明）；
- **「成功」定义** = HTTP 200 + 质量断言通过：
  - 锚词查询：`hit_count >= 1`（锚词必须可召回）；
  - 普通查询：`real_recall == true`；
- 防止「没有调用模型所以很快」的假通过：延迟统计只统计质量断言通过的 read。

**判定**：质量断言失败率计入 read 错误；`hit_count` 分布（P50/P95）进报告。
**洪峰排除**：D 场景 burst 窗口内的 read 从质量判定中排除
（`search_quality_summary(records, burst_windows=...)`），洪峰降级由劣化/信号单独报告；
窗口外的 read 严格判定。

### 4.4 隔离细粒度（同租户 / 跨租户）

**语义**（对应顶层 2.5 细粒度）：

- D 场景洪峰窗口内，把 read 记录按「租户是否等于洪峰写入租户」分为两组：
  - **同租户**：发起洪峰写入的租户自己的 search；
  - **跨租户**：其它租户的 search；
- 两组分别统计 P50/P95/P99 与错误率，并与 A 基线对照；
- **判定**（对应顶层 2.5）：主判据 = 两组劣化各自 < `--degradation-threshold`（默认 2x）；
  副判据（跨租户串扰）= 跨租户劣化显著高于同租户时才判失效
  （`cross_ratio > same_ratio * CROSSTALK_TOLERANCE`，容差 1.25）。
  理由：真实模型路径下 embedding/LLM 是全局共享资源，burst 抽取公平拖慢所有租户，
  cross ≈ same 是预期行为而非隔离失效。

### 4.5 RSS 归一校正

**语义**（对应顶层 2.7）：

- 压测期间记录注入数据量：新增消息数、新增 atom 数、索引文件字节增长；
- 输出三个 RSS 口径：
  - `rss_raw_mb`：`/metrics` 原始 RSS 时序；
  - `rss_net_mb`：扣除按「新增 atom/索引字节」归一估计的索引增长后的 RSS；
  - `rss_peak_mb` / `rss_settled_mb`：峰值与冷却后；
- **判定**：泄漏用**校正后斜率**（`rss_net_mb` 时序最小二乘斜率）< 5 MB/min；
  正常数据增长导致的 RSS 上升不计为泄漏。

### 4.6 故障注入（mock provider，不修改服务端）

**设计前提**：故障注入对象是服务端的**外部依赖**（LLM/embedding HTTP 端点），
因此不修改服务端代码也能注入故障——把服务端 engine 配置的 `api_base` 指向
可控 mock，只改配置。

**组件**：`perf_mock_provider.py`——本地 HTTP 服务（默认 `127.0.0.1:18090`），
实现 EchoMem engine 实际调用的 LLM/embedding 端点；行为由注入脚本控制：

| 注入 | mock 行为 |
|---|---|
| 正常 | 返回固定成功响应（或转发真实 provider） |
| 500 | 返回 HTTP 500 |
| 挂起 | 延迟超过服务端超时（不响应） |
| 429 | 返回 HTTP 429 + `Retry-After` |
| 恢复 | 恢复正常 |

**执行序列**（独立 F 流程，不并入并发矩阵）：

正常（基线）→ 50% 500 → 完全挂起 → 恢复 → 429 + Retry-After → 恢复；
每阶段持续一个观测窗口，客户端持续写/读。

**观测与判定**：

- 错误分类正确（2.10）：500 → 5xx；挂起 → 超时；429 → 带 `Retry-After`；
- 熔断触发后快速失败（请求不无限卡住）；
- 恢复后请求成功；
- commit 终态正确：可重试因素 → 客户端重提后成功；不可重试 → 终态 failed。

**约束**：

- 只改服务端**配置**（模型端点指向 mock），不改服务端代码；
- mock 结果 = 「可控故障语义」证据；真实模型容量另测 = 「真实容量」证据；
  **两类证据分开报告，禁止互相替代**；
- 注入前记录配置摘要（SHA-256），流程结束恢复原配置。

### 4.7 模型与配置预检门禁

**组件**：`perf_preflight.py`（或 `run_stress --preflight` 阶段），对应顶层 2.13。

**步骤**：

1. 读取**实际运行**的 engine 配置，逐 engine 解析 `api_key_env` / `api_base` / `model`；
2. 检查所有 `api_key_env` 指向的环境变量存在且非空；
3. 对每个实际使用的 LLM/embedding endpoint 做一次**最小真实请求**；
4. 检查模型名是否被该 endpoint 支持；不支持即**停止测试**；
5. 记录 provider 响应时间、HTTP 状态码、模型名、配置摘要（SHA-256），不记录 API key。

**判定**：预检全部通过才进入压测；任一 engine 失败 → 停止并归类「环境/依赖失败」
（不归因于被测代码）。

### 4.8 判定分层与 SLO 口径

**报告分层**（对应顶层 2.10 / 5）：

- 证据类型字段：`real`（真实容量）/ `mock`（可控故障语义）分开成节；
- 状态分类：`pass` / `fail` / `not_run` / `known_limit` / `env_error`；
  环境/依赖失败（key、模型名、网络、provider admission）单独归类；
- SLO 口径表：每个指标列出 分子 / 分母 / 时间起点 / 时间终点 / 是否含重试；
- 涉及重试的指标同时输出**原始值**与**重试后值**；
- 服务端错误类型校验（2.10）：对每种拒绝/故障场景断言服务端返回的错误类型
  与预期一致，作为其它判定成立的前提（429 带 `Retry-After`、400 模型名、
  5xx 模型故障、超时挂起、终态 failed 等）。

## 5. 阈值与判定规则汇总

| 顶层目标 | 判定输入 | 阈值 |
|---|---|---|
| 2.2/2.3a 写事务重试 | 最终提交成功率 + 消息落库 | 重试后成功即通过；429 语义正确 |
| 2.3b 消息对账 | 全集 ⊆ cursor/atom、无重复、archive completed | 全过 |
| 2.4 durability | `guarantee_violations` | = 0 |
| 2.5 search 优先级 | D 洪峰窗口读 P95 劣化 | < `--degradation-threshold`（默认 2x） |
| 2.5 隔离细粒度 | 跨租户劣化 vs 同租户劣化 | 各自 < 阈值 且 跨租户 ≤ 同租户×1.25（串扰容差） |
| 2.6 租户公平性 | 组间读 P95 max/min 比 | < 3x |
| 2.7 无内存泄漏 | 校正后 RSS 斜率（MB/min） | < 5 MB/min |
| 2.10 错误类型正确性 | 各场景服务端错误类型 | 与预期一致 |
| 2.11 故障注入 | 错误分类/熔断/恢复/终态 | 分类正确、恢复成功 |
| 2.13 预检门禁 | 逐 engine 真实请求 | 全部通过 |

结论结构：`evaluate_features` 逐特性 PASS/FAIL/INCONCLUSIVE + 量化 measurements +
overall（任一 FAIL 则 FAIL；无 FAIL 且有 INCONCLUSIVE 则 INCONCLUSIVE；否则 PASS）。

## 6. 与单元测试的对应关系

### 已实现（现有 `tests/test_performance.py`）

| 设计意图条目 | 测试 |
|---|---|
| 检索/写事务统计口径（分位、QPS、错误分类） | `PercentileTests` / `SummarizeTests` |
| 写事务四段计时与失败中止 | `WriteTransactionTests` |
| 劣化倍数与绝对劣化量化 | `SummarizeTests.test_degradation_*` / `test_degradation_measurements` |
| commit 成功保证 | `FeatureGuaranteeTests.test_commit_durability_*` |
| 租户公平性 | `FeatureGuaranteeTests.test_tenant_fairness_*` / `test_fairness_measurements` |
| RSS 趋势 | `FeatureGuaranteeTests.test_rss_trend_*` |
| CPU 利用率时间序列 | `FeatureGuaranteeTests.test_cpu_utilization_series` |
| 写后读一致性 | `SummarizeTests.test_consistency_summary` |
| 特性结论判定全分支 | `FeatureVerdictTests` |
| 场景矩阵 / 混合与线程分配 / 错误分类 | `ScenarioMatrixTests` / `LoadgenTests` |
| Prometheus 解析 / 直方图分位 / counter/gauge | `PrometheusParseTests` / `MonitorAnalyticsTests` |
| 种子灌入 / locomo / static 约束 / 清理 | `PrepareTests` / `RunStressArgsTests` |
| 埋点序列化 / 报告区块 / 报告再生成 | `SerializationTests` / `ReportTests` |

### 扩展目标（均已落地代码与配套测试）

| 顶层目标 | 配套测试 |
|---|---|
| 写事务重试（429 退避 / 上限 / 不可重试不重试） | `WriteRetryTests` |
| 消息对账去重（全集⊆cursor/atom、无重复） | `MessageReconciliationTests` |
| search 质量断言（hit_count / real_recall / 假通过识别） | `SearchQualityAssertionTests` |
| 隔离细粒度（同/跨租户分组判定） | `IsolationGranularityTests` |
| RSS 归一校正（原始/净/峰值三口径） | `RSSNormalizedTrendTests` |
| 故障注入 mock（500/挂起/429/恢复响应） | `MockProviderTests` |
| 预检门禁（逐 engine 配置/真实请求/失败即停） | `PreflightTests` |
| 错误类型正确性 / 判定分层 / SLO 口径 | `ErrorTypeTests` |

## 7. 使用示例与产物

```bash
# 本机快速冒烟（并发档 1,16，时长减为 1/4）
python performance/run_stress.py --quick --tenants 4 --duration-s 60

# 全矩阵（默认 A/B/C/D + 写事务重试语义）
python performance/run_stress.py

# 背压验证：高并发档 + 显式重试上限
python performance/run_stress.py --scenarios B --concurrency-steps 16,64 \
  --commit-retry-max 3 --messages-per-session 10

# 故障注入（mock provider，不修改服务端；独立流程）
python performance/run_stress.py --scenarios F --mock-provider http://127.0.0.1:18090

# 含真实模型路径：先过预检门禁，失败即停
python performance/run_stress.py --preflight --config path/to/config.json

# 长期满负荷（检验泄漏/公平性，RSS 归一校正）
python performance/run_stress.py --duration-s 1800 --scenarios A,B,D

# 报告再生成：对已有运行重建 report.html（无需重跑压测）
python -m performance.report performance/results/<ts>
```

产物（`performance/results/<ts>/`）：

- `summary.json`：按场景×并发档分节的延迟/吞吐/错误/资源/劣化 + `feature_verdicts`
  特性结论 + 对账结果 + 重试统计（原始值/重试后值）+ 判定分层；
- `requests.csv`：逐请求（含 retry 与质量断言字段）；
- `metrics_samples.csv`：服务端采样时序；
- `report.html`：自包含报告（含证据分层、错误类型校验、RSS 三口径）；
- `config.json`：参数（auth_key 掩码）+ 配置摘要（SHA-256）。
