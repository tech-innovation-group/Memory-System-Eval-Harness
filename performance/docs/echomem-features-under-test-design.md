# 性能压测顶层设计意图：被测 EchoMem 特性与测试目标（performance/docs/）

> 本文档是 `performance/` 压测在「被测特性 × 测试目标」层面的**顶层设计意图**：
> 明确压测要验证 EchoMem 的哪些特性、为什么测、验收判据是什么。
> 「怎么测」的具体手段与方法见配套的 **具体实现文档** `echomem-features-implementation-design.md`；
> 模块级整体架构见 `../../docs/performance-stress-test-design.md`；
> 对应单元测试为 `tests/test_performance.py`（已实现部分）与实现文档中标注的待配套测试。

## 1. 目标与背景

`performance/` 对**已启动**的 EchoMem 服务做多租户高并发读写压测（默认不需要 LLM），
量化读/写/资源三个维度，并验明 EchoMem 的**特性保证**。基本原则：

- **只观测、不改服务端**：压测侧零新增依赖（urllib + ThreadPoolExecutor +
  手写 Prometheus 文本解析）；故障注入通过替换服务端的**外部依赖**（模型端点）实现，
  不修改服务端代码（见 2.11）；
- **适用范围**：默认纯接口层压测（无需 LLM）；扩展模式含真实模型路径
  （commit 抽取真实调用 LLM/embedding），此时必须先过模型与配置预检门禁（见 2.13）；
- 运行结束产出可复现的量化结论（`summary.json` + `report.html`），逐特性给出
  PASS / FAIL / INCONCLUSIVE 判定及支撑数据。

顶层设计回答两个问题：**测什么**（本文档第 2 节）与 **怎么测**（具体实现文档）。

## 2. 被测 EchoMem 特性（顶层测试目标）

每项给出：定义 / 为什么测 / 验收判据 / 数据来源与判定入口。

### 2.1 检索（search / recall）

| 项 | 内容 |
|---|---|
| 定义 | 通过 `POST /api/retrieval/search` 对记忆索引做语义检索，返回 top-k 条目 |
| 为什么测 | 检索是核心读路径，吞吐/延迟决定上层 Agent 响应质量 |
| 验收判据 | 延迟稳定（P50/P95/P99）、错误分类可量化；作为 A 场景基线供劣化对照；「成功」须含质量断言（见 2.5） |
| 数据来源 | 客户端埋点（QPS、延迟、错误分类、质量断言）+ 服务端 `echomem_recall_duration_seconds` 直方图 |

### 2.2 写事务注入（四段 + 可重试语义）

| 项 | 内容 |
|---|---|
| 定义 | 完整写事务四段：`POST /api/sessions/open` → `POST /api/sessions/{id}/messages`（×N）→ `POST /api/sessions/{id}/commit`（提交，202 返回 archive_id）→ `GET /api/sessions/{id}/commits/{archive_id}` 轮询完成 |
| 可重试语义 | 提交阶段收到**可重试拒绝**（限流 429 + `Retry-After` 等）→ 按 `Retry-After` 退避重试（有上限）→ 最终成功即验收通过；**不可重试失败**（业务类 4xx、终态 failed）不重试 |
| 为什么测 | 注入是记忆写入路径，其各段延迟、吞吐、拒绝分类与重试行为决定写入可靠性 |
| 验收判据 | 四段延迟、吞吐、错误分类（可重试 / 不可重试）、重试次数与退避时间可量化；「正确写入」另见 2.3 |
| 数据来源 | 客户端埋点四段独立计时 + 重试埋点（retry 次数、重试后是否成功）+ 服务端 `echomem_session_commit_duration_seconds` |

### 2.3 正确写入（重试成功 + 消息对账去重）★

| 项 | 内容 |
|---|---|
| 定义 | 压测写入的**全部消息**最终以正确、无重复的方式进入记忆索引。「202 接受」不等于消息落库 |
| 子目标 a | **重试成功**：被背压拒绝（429）的写入经退避重试后最终成功，消息最终落库 |
| 子目标 b | **消息对账**：以消息全集为基准，与 committed cursor、atom 的 `source_turn_ids`、archive 终态四者交叉核验——无丢失、**无重复 transcommit** |
| 为什么测 | 正确写入是记忆系统的第一性保证；只查 commit 终态会漏掉「消息级丢失/重复」 |
| 验收判据 | 消息全集全量出现在 committed cursor / atom `source_turn_ids`；无重复；archive 终态 completed |
| 数据来源 | 写负载逐消息埋点（message_id / content_hash / 锚词）+ 对账接口与交叉核验流程（实现见具体实现文档 4.2） |

### 2.4 特性1a：commit 异步与成功保证（durability）

| 项 | 内容 |
|---|---|
| 定义 | commit 提交（202 接受）后最终必须 `completed` |
| 失败分类 | **可重试**（限流、瞬时模型故障 → 客户端退避重提或服务端接管后成功）与**不可重试**（终态 failed，不后台自动补齐）分开判定 |
| 为什么测 | 这是 EchoMem 对上层 Agent 的关键契约：异步但不丢、不永久挂起 |
| 验收判据 | `guarantee_violations == 0`（202 已接受但最终未 completed）；提交拒绝分类（可重试 / 不可重试）输出；poll 超时单列（观测窗口到期，不等于 commit 失败） |
| 数据来源 | 客户端按 session 配对 `commit_submit`（status=ok）与 `commit_done` |

### 2.5 特性1b：search 优先级（commit 不阻塞检索）★细粒度 + 质量断言

| 项 | 内容 |
|---|---|
| 定义 | 写洪峰期间检索必须优先响应：注入不能阻塞检索（须能重复检出并量化「注入阻塞检索」） |
| 细粒度 | **同租户 vs 跨租户**分别统计：commit 风暴期间本租户 search 与其它租户 search 的延迟/错误率，识别隔离是否跨租户串扰。模型 provider（embedding/LLM）是**全局共享资源**，burst 抽取会公平地拖慢所有租户，因此**跨租户劣化允许与同租户在同一量级**；仅当跨租户劣化显著高于同租户（cross/same 比值 > 串扰容差 `CROSSTALK_TOLERANCE=1.25`）才判定跨租户串扰 |
| 质量断言 | search「成功」= HTTP 200 + **真实召回发生**：记录召回条数（hit count）、是否走了真实 recall、模型请求是否发生（按配置豁免时注明），防止「没调模型所以很快」的假通过。**D 场景洪峰窗口内的 read 不参与质量判定**（洪峰是刻意过载场景，读降级按劣化/信号单独报告，不当作写后读一致性失败）；窗口外 read 严格判定 |
| 为什么测 | 检出并量化写读耦合/锁竞争；质量断言保证延迟数字背后是真实检索 |
| 验收判据 | 洪峰窗口读 P95 劣化 < 阈值（默认 2x，`--degradation-threshold`）；跨租户劣化 ≤ 同租户×串扰容差（1.25），否则判跨租户串扰；量化输出基线/洪峰绝对 ms、delta、ratio；质量断言记录完整 |
| 数据来源 | 洪峰窗口 read 记录（含质量断言字段）vs 同并发档 A 基线 |

### 2.6 特性2：租户公平性

| 项 | 内容 |
|---|---|
| 定义 | 多租户隔离与公平：没有任何一个租户能占满机器资源；不同租户响应延迟均衡 |
| 为什么测 | 租户间公平性是多租户部署的核心隔离保证 |
| 验收判据 | 按场景×租户的读 P50/P95/QPS，组间 P95 max/min 比 < 3x（`FAIRNESS_MAX_MIN_RATIO=3.0`）；量化输出最慢租户比最快租户多等的 P95 时长（`slowest_waits_extra_ms`） |
| 数据来源 | 客户端按场景×租户分组的 read 记录（多租户身份经 `provision_isolated_identity` / `delete_current_identity` 自助创建与清理） |

### 2.7 特性3：无内存泄漏 ★RSS 按注入数据量归一校正

| 项 | 内容 |
|---|---|
| 定义 | 长期多租户满负荷运行，进程内存不能无限增长 |
| 归一校正 | RSS 增长需**按注入数据量归一**：记录压测期间新增消息/atom/索引增长量，把「泄漏性增长」与「正常数据增长」分开；同时输出**原始 RSS / 扣除新增 atom+索引后的 RSS / 峰值 RSS** 三值 |
| 为什么测 | 不校正会把正常索引增长误判为泄漏，导致漏检或误报 |
| 验收判据 | 校正后 RSS 斜率 < 5 MB/min（`RSS_LEAK_SLOPE_MB_PER_MIN=5.0`）+ 冷却后未回落量；至少 4 帧采样，不足判 INCONCLUSIVE |
| 数据来源 | `/metrics` 的 `echomem_process_resident_memory_bytes` 时序 + 注入数据量计数 |

### 2.8 特性4：资源利用率随时间变化

| 项 | 内容 |
|---|---|
| 定义 | 报告须展示 CPU/内存等资源利用率随时间的曲线 |
| 为什么测 | 单一均值掩盖负载波动；时间线暴露尖峰、队列堆积与泄漏趋势 |
| 验收判据 | `report.html` 含 CPU%/RSS/线程/句柄/commit 队列/inflight 全过程独立子图；`metrics_samples.csv` 保留原始采样时序 |
| 数据来源 | `/metrics` 相关 series（`echomem_process_cpu_seconds_total` 帧差、`echomem_process_resident_memory_bytes`、线程/句柄/队列/inflight gauge） |

### 2.9 写后读一致性窗口

| 项 | 内容 |
|---|---|
| 定义 | commit 完成后，已提交内容需要多久才能被 search 命中（异步抽取的可见延迟） |
| 为什么测 | 量化「写后多久可检索」，是 commit 异步抽取实时性的直接度量 |
| 验收判据 | 对最近完成的写事务锚词轮询 search 直至命中或超时；P50/P95 + 超时计数输出 |
| 数据来源 | 写场景尾段 `run_consistency_checks` 埋点（锚词会被后续场景覆盖，故紧接写场景执行） |

### 2.10 服务端错误类型正确性 ★

| 项 | 内容 |
|---|---|
| 定义 | 服务端对每种拒绝/故障场景返回**正确的错误类型**：队列满/限流 → 429 + `Retry-After`；模型名不支持 → 4xx（业务类）；模型故障 → 5xx；模型挂起 → 超时；provider admission 超时 → 明确超时错误；不可重试 → 终态 failed |
| 为什么测 | 判定分层与 SLO 口径的前提：错误类型错了，可重试/不可重试语义就不可信 |
| 验收判据 | 客户端错误分类（timeout / http_4xx / http_5xx / connection / other + 429 语义）与服务端返回语义一致；可重试 / 不可重试分类正确 |
| 数据来源 | 客户端埋点错误分类 + 服务端返回的状态码与错误体（`Retry-After` 头、错误类型字段） |

### 2.11 故障注入与恢复 ★

| 项 | 内容 |
|---|---|
| 定义 | 在模型故障（50% 500 / 完全挂起 / 429 / 恢复）下验证：错误分类、熔断、恢复与 commit 最终一致性 |
| 前提约束（不修改服务端） | 故障注入对象 = 服务端的**外部依赖**（LLM/embedding HTTP 端点）。通过把 engine 配置的 `api_base` 指向**可控 mock provider** 实现——只改配置、不改服务端代码；mock 按脚本返回 正常 / 500 / 挂起（超时）/ 429+Retry-After / 恢复 |
| 证据分层 | 真实模型容量结果 与 mock 可控故障语义结果 为**两类证据，禁止互相替代**；报告分别标注 |
| 为什么测 | 验证模型故障下错误分类正确、熔断后快速失败（不无限卡住）、恢复后请求成功、commit 进入正确终态 |
| 验收判据 | 错误分类正确；熔断触发后快速失败；恢复后请求成功；commit 终态正确（可重试 → 客户端重提成功；不可重试 → 终态 failed） |
| 数据来源 | mock provider 注入记录 + 客户端埋点 + commit 终态配对 |

### 2.12 健康与可观测性

| 项 | 内容 |
|---|---|
| 定义 | 压测对象必须可通过 `/health` 探测存活、通过 `/metrics` 暴露 Prometheus 指标 |
| 为什么测 | 可观测性是压测/运维的前提；服务端指标提供客户端视角之外的独立证据 |
| 验收判据 | `/health` 预检通过；`/metrics` 不可达时自动降级为纯客户端指标（警告 + 报告注明），不中断压测 |

### 2.13 模型与配置预检门禁 ★（适用范围扩展：含真实模型路径）

| 项 | 内容 |
|---|---|
| 定义 | 压测扩展为含真实模型路径（commit 抽取真实调用 LLM/embedding）时，必须先通过预检门禁 |
| 预检步骤 | ① 读取**实际运行**的 engine 配置（非测试平台旧模板），逐 engine 解析 `api_key_env` / `api_base` / `model`；② 检查所有 `api_key_env` 环境变量存在且非空；③ 对每个实际使用的 LLM/embedding endpoint 做一次**最小真实请求**；④ 检查模型名是否被该 endpoint 支持，不支持即**停止测试**；⑤ 记录 provider 响应时间、HTTP 状态码、模型名、配置摘要（SHA-256），**不记录 API key** |
| 为什么测 | 真实模型压测前，环境/配置错误必须与代码故障分开；否则压测报告只是在测 API key、模型路由或 provider 排队 |
| 验收判据 | 预检全部通过才进入压测；任一 engine 缺 key / 模型不支持 / 真实请求失败即停止并归类为「环境/依赖失败」 |
| 数据来源 | 实际运行配置解析 + 最小真实请求埋点 |

### 注：auto-commit 路径

**后续会关闭，无需测试**：不纳入负载模型、场景与验收（写负载一律显式 commit）。

## 3. 测试手段与测试方法（总纲）

| 维度 | 顶层要点 | 详细实现 |
|---|---|---|
| 客户端 | `EchoMemClient`（urllib、零三方依赖）；可重试语义下按错误类型决定是否退避重试 | 具体实现文档 §2/§4.1 |
| 并发注入 | `LoadGenerator`（ThreadPoolExecutor：读/写 worker、burst 池、`RateLimiter` fixed-rps、确定性混合） | 具体实现文档 §2 |
| 埋点 | `RequestRecord` 逐请求：scene / tenant / op / stage_ms / status / error_type / ts；扩展字段：retry 计数、质量断言（召回条数、真实 recall）、重试后成功标记 | 具体实现文档 §2/§4.3 |
| 服务端观测 | `MetricsMonitor` 周期 GET `/metrics`，手写 Prometheus 解析；不可达降级 | 具体实现文档 §2 |
| 统计与判定 | `metrics_calc.py` 纯函数：分位/汇总/劣化/一致性/durability/fairness/RSS 趋势 + `evaluate_features` 逐特性结论 | 具体实现文档 §5 |
| 种子数据 | `TenantPreparer`：synthetic 锚词 / locomo 真实会话复制式布局；不计入压测计时 | 具体实现文档 §2 |
| 故障注入 | mock provider 反向代理（替换模型端点配置，不改服务端） | 具体实现文档 §4.6 |
| 预检门禁 | 模型与配置预检（含真实模型路径时） | 具体实现文档 §4.7 |

## 4. 边界条件

- 压测对象是**已启动**的 EchoMem 服务，本模块不拉起服务；
- 客户端**按错误类型决定重试**：可重试拒绝（429 + `Retry-After` 等）按策略退避重试（有上限）；不可重试失败（业务类 4xx、终态 failed）不重试；重试语义与口径在报告中说明；
- **正确写入以消息对账为准**（消息全集 ↔ cursor ↔ atom `source_turn_ids` ↔ archive 终态），不是只查 commit 状态；
- auto-commit 路径后续关闭，不在测试范围；
- 故障注入只替换服务端的外部依赖（模型端点配置），不改服务端代码；mock 结果与真实模型结果分两类证据；
- 种子数据注入不计入压测计时；`/metrics` 不可达时降级为客户端指标（警告 + 报告注明）；
- `--auth-mode static` 仅允许单租户（外网预置身份）；`--cleanup-identities` 仅对 provision 模式可用，static 模式在参数校验阶段直接拒绝；
- 含真实模型路径的压测必须先过预检门禁；预检失败归类「环境/依赖失败」，不归因于代码；
- 压测结果好坏不由退出码表达（正常完成退出码 0），由 `summary.json` 判定。

## 5. 判定分层与 SLO 口径（顶层规则）★

- **证据分层**：真实容量压测结果 与 mock 故障语义结果 分开报告，禁止互相替代；
- **状态分类**：通过 / 失败 / 未执行 / 已知限制 / 环境错误 分开；环境/依赖失败（key、模型名、网络、provider admission）不得归因于被测代码；
- **错误类型正确性**：每种拒绝/故障场景的服务端错误类型须与预期一致（2.10），作为所有判定成立的前提；
- **SLO 口径**：每个指标明确分子/分母、时间起点/终点、是否包含重试；涉及重试的指标同时输出**原始值**与**重试后值**；
- **结论结构**：`feature_verdicts` 逐特性 PASS/FAIL/INCONCLUSIVE + 量化 measurements + overall（任一特性 FAIL 则 FAIL，无 FAIL 且有 INCONCLUSIVE 则 INCONCLUSIVE，否则 PASS）。

## 6. 文档关系与测试对应

- 顶层设计意图（本文档）→ 具体实现文档 `echomem-features-implementation-design.md`（怎么测）；
- 顶层目标 → `tests/test_performance.py`：已实现部分（检索/写事务四段/durability/fairness/RSS 趋势/场景矩阵/统计/报告等）已有对应测试；**新增目标**（写事务重试、消息对账、search 质量断言、隔离细粒度、RSS 归一校正、故障注入、预检门禁、错误类型正确性）标注为待配套实现与测试，与实现文档一一对应。
