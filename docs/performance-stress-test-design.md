# 性能压力测试模块设计意图（performance/）

> 本文件是 `performance/` 模块的设计意图文档：目标、接口、行为约束与边界条件。
> 对应的单元测试为 `tests/test_performance.py`、`tests/test_acceptance.py`、
> `tests/test_formal_suite.py`、`tests/test_formal_suite_timeout.py`、
> `tests/test_formal_data_report.py`、`tests/test_failure_tools.py`，
> 两者一一对应、协同演进。

## 1. 目标与背景

现有评测系统（`benchmarks/`、`dynamic/`）只度量**精度**（F1/EM/Judge 分数）。
本模块补上**性能**维度：对运行中的 EchoMem 服务做多租户高并发读写压测，量化：

- **读（检索）**：吞吐（QPS）、延迟（客户端 P50/P95/P99 + 服务端直方图双视角）、错误率、超时率；
- **写（注入）**：`open_session` / `add_message` / `commit 提交(202)` / `commit 完成(poll)` 四段延迟、写吞吐、提交回拒、**写后读一致性窗口**；
- **读写干扰**：读写混合与「注入洪峰」下读延迟的劣化倍数——用户已观察到**注入会阻塞检索**，本模块必须能重复检出并量化该现象；
- **资源**：进程 CPU 占用（均值/峰值/时间序列）、RSS（基线/峰值/冷却回落/未回落/上升趋势斜率）、线程/句柄/commit 队列水位。

设计原则：**只观测、不改服务端**。EchoMem 已暴露 Prometheus `/metrics`
（`EchoMem/src/echomem/entrypoints/api/handlers.py:145`）与全套检索/commit/进程指标，压测侧零新增依赖（urllib + ThreadPoolExecutor + 手写 Prometheus 文本解析）。

### 1.1 四项特性保证（压测必须验明的验收判据）

| 特性 | 断言 | 验证数据与判定 |
|---|---|---|
| 1. commit 异步、成功保证、不阻塞检索 | commit 提交(202) 后最终必须 completed；commit 失败（不重试）只出现在 LLM 访问拒绝等不可重试因素；任何 search 到达必须优先响应 | `commit_durability()`：202 接受数 vs 最终完成数，`guarantee_violations`；提交阶段拒绝分类（不重试）；D 场景洪峰窗口读 P95 劣化倍数 < 阈值（默认 2x） |
| 2. 租户公平性 | 没有任何一个租户能占满机器资源；不同租户响应延迟均衡 | `tenant_fairness()`：按场景×租户的读 P50/P95/QPS，组间 P95 max/min 比 < 3x 判均衡 |
| 3. 无内存泄漏 | 长期多租户满负荷运行，内存不能无限增长 | RSS 时间序列最小二乘斜率（`rss_trend_mb_per_min`）+ 冷却后未回落量；斜率 ≥ 5 MB/min 报疑似泄漏信号 |
| 4. 资源利用率随时间变化 | 报告须展示 CPU/内存随时间的利用率曲线 | `cpu_utilization_series()`（帧差百分位）+ `gauge_series(RSS)`；report.html 每系列独立子图 |

压测时长由 `--duration-s`（每场景每并发档）控制：泄漏与公平性判定的可信度随
时长与并发档升高；长满负荷运行使用全矩阵 + 大 `--duration-s`（如 300s）。

### 1.2 特性结论（feature_verdicts）

每次运行结束由 `evaluate_features(summary)` 对四项特性逐一出结论：

- `PASS`（通过）/ `FAIL`（不通过）/ `INCONCLUSIVE`（数据不足无法判定）；
- 特性 1 拆两个子项 `durability`（commit 成功保证）与 `retrieval_precedence`
  （search 优先级），任一子项 FAIL 则特性 1 FAIL；
- **总体结论** = 任一特性 FAIL 则 FAIL；无 FAIL 且存在 INCONCLUSIVE 则
  INCONCLUSIVE；否则 PASS。

结论落三处：`summary.json["feature_verdicts"]`（机器可读）、`report.html`
顶部「特性结论」表（颜色标注：绿=通过 / 红=不通过 / 灰=数据不足，含每条依据
与数据引用）、终端摘要「特性结论」块。判定阈值与第 1.1 节一致（劣化 2x /
公平性 3x / 泄漏斜率 5 MB/min，`--degradation-threshold` 可调）。

### 1.3 特性量化分析（满足到什么程度）

除 PASS/FAIL 结论外，每条特性判定携带 `measurements` 量化字段（写入
`summary.json["feature_verdicts"]`、渲染进 `report.html`「特性量化分析」小节、
终端「特性结论」行内展示），把「是否满足」扩展为「满足到什么程度」：

| 特性 | 量化输出 | 说明 |
|---|---|---|
| 1a commit 成功保证 | `commit_success_rate`、`violations`、`completion_latency_ms`（submit→completed 的 P50/P95/P99/max 等待） | 异步完成的实时性直接可读 |
| 1b search 优先级 | 每对 `D_x_vs_A_y`（及 C）的 `baseline_p95_ms` / `flood_p95_ms` / `delta_p95_ms`（绝对毫秒差）/ `ratio_p95` | 写洪峰时 search 延迟比基线高多少：如「基线 P95 42ms → 洪峰 120ms，+78ms，2.86x」 |
| 2 租户公平性 | 每多租户场景的 `fastest/slowest_tenant_p95_ms`、`slowest_tenant_p99_ms`、`slowest_waits_extra_ms`（最慢比最快多等的绝对时长） | 不均衡时受困租户最坏等待的直接度量 |
| 3 无内存泄漏 | `slope_mb_per_min`、`projected_growth_mb_per_hour`（斜率×60）、`rss_baseline_mb`/`rss_peak_mb`/`rss_unsettled_mb`、`trend_r2`、`trend_samples` | 泄漏增长率与小时外推 |
| 4 资源时间线 | `cpu_util_mean_percent` / `cpu_util_max_percent`、`metrics_frames`、`rss_peak_mb`、`threads_max`、`commit_queue_max` | CPU/内存利用率的极值与均值 |

## 2. 边界条件

- 压测对象是**已启动**的 EchoMem 服务（`--echomem-url` 可指向任意 IP:端口，含外网部署），本模块不拉起服务；
- 压测客户端一律 `max_retries=0`（重试会掩盖错误率并扭曲延迟分布），超时单独归类；
- 种子数据注入（prepare 阶段）**不计入**压测计时，但保证检索索引有真实内容；
- 写事务不自动补偿（commit 提交失败即计入回拒，不重试）；
- `/metrics` 不可达时自动降级为客户端指标（警告 + 报告注明），不中断压测；
- `--auth-mode static` 仅允许单租户（外网预置身份），`--auth-mode provision` 支持多租户自助创建；
- `--cleanup-identities` 仅对 provision 模式可用：**static 模式在参数校验阶段直接拒绝**
  （删除会连同预置生产租户的会话/记忆数据一起清掉）；清理动作在入口 `finally` 中执行，
  场景或报告阶段异常退出时也保证执行；清理语义 = 身份记录标记删除 + 租户数据目录整体
  `rmtree`（会话/记忆/索引/日志全部清空，EchoMem 侧实现见
  `runtime.delete_identity_from_key` → `auth_service.delete_tenant`）；
- 种子数据源两类：`synthetic`（默认，合成锚词消息）与 `locomo`（真实对话）。
  `--seed-source locomo` 时从 `--dataset-path`（默认 `benchmarks/locomo/data/locomo10.json`）
  按 `--sample-filter` 选取样本，每个租户灌入同一套真实会话（复制式多租户布局，
  保证各租户数据规模一致，公平性对比不因数据量差异失真）；数据集路径必须存在，
  `--sample-filter` 不能为空，无匹配样本直接抛错；真实会话没有锚词，B 场景的
  写后读一致性只针对压测写事务（自带锚词），不受种子影响。
- 压测结果好坏不由退出码表达（正常完成退出码 0），由 `summary.json` 的劣化倍数/信号/资源指标判定。

## 3. 模块职责与接口

### 3.1 `scenarios.py` — 场景矩阵

- `SceneRun`：一个原子压测单元（`scene_id` / `per_tenant_conc` / `duration_s` / `mix` / `burst_*`），`key` 为稳定标识（如 `C:8:1@4`）；
- `expand_matrix(...) -> list[SceneRun]`：并发档 × 场景 × read:write 档展开，场景主序、并发次序列；
- 场景定义：

| 场景 | 负载 | 用途 |
|---|---|---|
| A 纯读 | 全部线程 search | 无写干扰基线，C/D 的劣化对照 |
| B 纯写 | 全部线程完整注入事务 | 四段延迟、回拒率、写后读一致性 |
| C 读写混合 | 按 read:write 比例分配线程 | 双方延迟劣化曲线 |
| D 注入洪峰 | 读持续 + 短窗口 K 个并行写事务 | 检出「注入阻塞检索」 |

约束：mix 比例必须 `read:write`，禁止 `0:0`；并发档必须正整数；未知场景 id 直接抛错。

### 3.2 `prepare.py` — 租户与种子数据

- `TenantPreparer`：`provision` 模式逐个 `provision_isolated_identity`；`static` 模式绑定预置身份（`tenants != 1` 时抛 `ValueError`）；`prepare(..., locomo_batches=...)` 非空时改用真实会话种子；
- `seed_tenant(...) -> TenantContext`：串行 `open -> add -> commit -> poll` 注入，每条消息带唯一锚词（`PERFANCHOR-...`），query 池 = 锚词查询 + 消息分句片段；种子 commit 失败即中止（种子是后续场景前提）；
- `load_locomo_seed_batches(dataset_path, sample_filter)`：读取 locomo 数据集（复用 `benchmarks/locomo/dataset.py` 的 `load_dataset`），`sample_filter` 支持单个 sample_id / 逗号分隔多个 / `all`，返回每个会话的 `[{role, content}, ...]` 列表；无匹配样本抛 `ValueError`；
- `seed_tenant_from_conversations(client, idx, batches, ...)`：locomo 变体，每个真实会话一个 session，写路径与合成种子一致；query 池只取用户消息的分句片段（`_query_fragments` 同时按中文标点与英文句点分句）。

### 3.3 `loadgen.py` — 负载注入器

- `RequestRecord`：逐请求埋点（场景键/并发档/租户/操作/阶段耗时/状态/错误类别/完成时间戳/extra）；
- `run_write_transaction(...) -> WriteTransactionResult`：写事务四段独立计时，段失败即中止事务并归类错误；
- `mix_token_sequence(read, write, total)`：确定性 read/write token 序列（纯函数，供测试与校验）；
- `split_threads(total, ratio)`：按比例分配读/写线程数；
- `RateLimiter`：fixed-rps 限速（仅读）；
- `LoadGenerator.run_scene(scene, tenants, ...) -> SceneResult`：并发执行场景，`SceneResult` 携带 records、墙钟时长、burst 窗口时间戳；
- `LoadGenerator.run_consistency_checks(...)`：对最近完成的写事务轮询 search 直至命中锚词或超时（写后读一致性窗口）。

### 3.4 `monitor.py` — 服务端观测

- `parse_prometheus_text(text)`：手写 Prometheus 文本解析（注释跳过、同 key 覆盖为最后一次、histogram 保留 `_bucket/_sum/_count` 全名、`# EOF` 终止）；
- `MetricsMonitor`：后台线程周期 GET `/metrics`，抓取失败计数 + 继续（不中断）；
- 推导：`counter_delta`（帧差）、`gauge_max` / `gauge_series`、`histogram_percentiles`（桶累计线性插值）、`cpu_utilization`、`scene_resource_summary`（场景窗口资源快照）。

### 3.5 `metrics_calc.py` — 统计纯函数

- `percentile` / `percentiles`：线性插值分位数（约定与 `dynamic/metrics.py`、`scripts/compare_memory_backends.py` 一致）；
- `summarize_records(records, wall_s)`：按场景键 × 操作分组的 count/QPS/avg/P50/P95/P99/max/错误率/错误分类明细；
- `degradation_factor(baseline, target)`：P50/P95/P99 劣化倍数（缺数据返回 None）；
- `read_records_in_window` / `burst_summary` / `consistency_summary`：劣化定位切片与判定输入；
- `commit_completion_latency(records)`：submit→completed 异步等待耗时的 P50/P95/P99/max（commit_done 阶段）；
- `degradation_measurements(summary)`：每对劣化对照（读场景 vs 同并发 A 基线）的绝对延迟、绝对差（delta_ms）与倍率（ratio），即「写洪峰时 search 比基线高多少」；
- `fairness_measurements(fairness)`：每多租户场景的最快/最慢租户 P95/P99 与额外等待差（`slowest_waits_extra_ms`）。

### 3.6 `report.py` — 产物

- `config.json`（参数 + auth_key 掩码）、`requests.csv`（逐请求）、`metrics_samples.csv`（采样时序全量）、`summary.json`（按场景分节 + 劣化 + 信号 + 资源 + 一致性）、`report.html`（自包含 SVG，无外部依赖）。

`report.html` 为自包含单文件（内联 CSS + 手写 SVG），内容结构：

1. **报告概述**：压测对象、运行状态、起止时间、生成器；
2. **测试方法**（`_methodology`）：由 `summary.config` / `data_scale` / `server` 参数化生成——压测对象与接口、负载模型（读=search；写=`open → add → commit_submit → commit_done` 四段）、并发模型（租户 × 并发档矩阵）、种子数据、观测方式（客户端计时 + 服务端 /metrics 双视角）、判定阈值与特性规则，附「压测参数」表；
3. **测试场景**（`_scenarios_section`）：A 纯读基线 / B 纯写注入 / C 读写混合 / D 注入洪峰的定义表 + 本次实际执行的场景矩阵（按运行顺序）+ 每场景服务端资源快照表（线程 / inflight / commit 队列峰值、recall / http / commit 服务端分位）；
4. **指标字典**（`_metric_glossary`）：每个指标的含义 / 算法 + 本次实测值（支撑事实）；
5. **特性结论** / **特性量化分析** / **判定摘要**；
6. **压测结果**：客户端读 / 写指标表（支撑事实）与图（QPS、读延迟分位、写事务四段延迟 P50、劣化倍数、租户公平性、资源时间线）；
7. 每张表 / 图配 `p.note` 说明「本节度量什么、怎么算」，指标与场景均有实测数据锚点。

再生成入口（不重跑压测即可对已有运行重建报告）：

- `chart_series_from_metrics_csv(csv_path)`：从 `metrics_samples.csv` 重建五条时间线（`rss_mb` / `threads` / `commit_queue` / `inflight` / `cpu_percent`）；对每 (ts, metric) 跨标签集求和（对齐 `monitor._value`），CPU 用 `echomem_process_cpu_seconds_total` 帧差（对齐 `monitor.cpu_utilization_series`）；
- `regenerate_report(out_dir)`：读 `summary.json` + `metrics_samples.csv` → `build_html` → `save_html`；会从 CSV 回填 CPU 均值/峰值统计（`_cpu_stats_from_csv`，语义与运行期 `counter_delta`/`cpu_utilization_series` 一致），保证 counter 名修正后重建的报告 CPU 数据生效；
- `python -m performance.report <results_dir>`：CLI 入口。

### 3.7 `run_stress.py` — 入口编排

五阶段：预检（/health、/metrics 探测）→ 准备（种子注入 + RSS 基线帧）→ 场景矩阵逐单元执行（monitor 全程采样）→ 冷却观测 → 报告落盘与终端摘要。
`--quick` 收敛并发档为 1,16 并把时长减为 1/4；`--no-metrics` / `--skip-health` 提供外网降级路径；参数校验失败 `exit 2`。
种子来源由 `--seed-source` 选择：`locomo` 时入口先 `load_locomo_seed_batches` 再交给 `TenantPreparer.prepare(locomo_batches=...)`，
`--seed-sessions-per-tenant` / `--messages-per-session` 对 locomo 源不生效（会话数由数据集决定）。

### 3.8 `acceptance.py` — PR421 验收门禁求值器

纯函数求值器，只消费 run_stress 已落盘的 `summary.json` / `requests.csv` 等制品，
不碰在线服务。缺失的服务端证据绝不从客户端计时推断；需要 EchoMem 控制面但当前
不可用的能力明确标 `INCONCLUSIVE` / `NOT_IMPLEMENTED`，评审状态（
`PR28_REVIEW_RESOLUTION`）与实测 gate 分离记录。

- `evaluate_pr421_acceptance(manifest) -> dict`：8 个 gate（search 成功率 /
  report6 质量 / search 隔离 / 租户公平 / commit 完成 / 拒绝 / hot tenant /
  容量阶梯），每个 gate 输出 verdict（`PASS` / `FAIL` / `INCONCLUSIVE` /
  `NOT_IMPLEMENTED`）+ 依据与数据引用；`overall` 汇总；
- `build_model_analysis_input(manifest, acceptance) -> dict`：无密钥、无凭据的
  模型分析输入（secret-free），供评审方直接消费。

### 3.9 `formal_suite.py` — 正式多租户验收套件编排

可重复的真实多租户压测验收套件。套件只负责编排：每个 case 由 `run_stress.py`
子进程（`RUNNER`）执行，产物落到 case 的 `run/` 子目录；套件叠加场景/轮次元数据，
并把 run_stress 原生产物推导成验收求值器消费的契约摘要（`summary.json` /
`commit_results.csv` / `search_results.csv`）。只有每次运行都使用独立租户凭据时
（`_identity_is_independent`）才允许做出上线结论；共享凭据仅用于探索，隔离/公平
保持 INCONCLUSIVE。

- `SCENARIO_PROFILES`：包含 `report6`（PR397/report(6) 矩阵）、
  `pr421`（PR421 验收，默认）、`complete`（两者去重并集）以及
  `4u8g-full`（PR397 12 项 + PR421 27 项，重复场景分别执行）；
- `run_case(...)`：单 case 执行——可选 reset 命令、子进程超时（超时杀进程组）、
  逐请求 CSV 规范化、契约摘要推导、产物落盘；
- `aggregate_runs(...)`：多轮结果聚合（分位/吞吐/错误）；
- `main()`：参数校验（未知场景、`--repeats < 1`、report6/complete 必须给
  `--preflight-config`、租户数不足直接拒绝）→ 逐 case 执行（`FORMAL_PROGRESS`
  进度行）→ `evaluate_pr421_acceptance` + `build_model_analysis_input` 落盘 →
  `suite.html` 数据报告 → `summary.json` 套件总结；退出码 0=PASS/INCONCLUSIVE，
  2=FAIL。

### 3.10 `formal_data_report.py` — 套件数据报告

从 `suite.json` 渲染自包含 `suite.html` 数据报告：逐 case 状态徽章、数值明细、
时间分桶、跨 run 分组；缺失服务端证据保持可见（不推断成功）。纯 stdlib，
含 argparse `__main__`（`python -m performance.formal_data_report <suite.json> <out.html>`）。

### 3.11 `probes/` — 故障 / 恢复 / 限流 / 对账探针

探针是独立 CLI 工具，直接以 urllib 访问真实 EchoMem HTTP 服务，不依赖压测
runner。每个探针只在部署方显式提供故障/恢复控制（命令、HTTP 端点、容器、PID）
时才执行真实操作，否则如实上报 `INCONCLUSIVE`；显式 HTTP 404 是「未实现」的
唯一证据，探针不得把自身缺少适配器当成对 EchoMem 能力的否定。

| 探针 | 职责 |
|---|---|
| `limit_failure_probe.py` | 真实限流/失败探针：不合成 429/5xx，使用真实租户凭据与真实依赖 |
| `limit_failure_sweep.py` | 有界负载阶梯扫描（`--levels`）+ 恢复观测 |
| `concurrent_commit_cases.py` | 同一真实 session 上并发 commit 行为 |
| `missing_cases.py` | PR397 缺失用例（可经真实 API 观测的部分）；不从两次成功 HTTP 推断幂等 |
| `fault_injection.py` | 显式真实故障控制（命令/HTTP/容器）+ 防篡改时间线；无控制则 INCONCLUSIVE |
| `fault_suite.py` | 按 `--plan` 编排故障 / 恢复 / 光标对账 case 的子进程编排 |
| `recovery.py` | 真实进程/容器 kill-9 恢复观测 |
| `commit_recovery_probe.py` | commit 中途被杀时的恢复观测（保守：丢失响应/无 cursor 端点记 INCONCLUSIVE） |
| `disconnect_recovery_probe.py` | 真实客户端断开处理与有界资源恢复 |
| `cursor_reconcile.py` | 已接受 commit 与真实 cursor/message-set API 对账 |
| `capability_probe.py` | 可选 EchoMem 契约探测（仅显式 404 证明未实现，其余 INCONCLUSIVE） |

### 3.12 `probes/_client.py` — 探针共享 HTTP 客户端

探针共用同一份真实 HTTP 客户端与租户规格解析（`EchoMemHTTP`、
`TenantSpec` / `load_tenant_specs`、`HttpResult`、`extract_archive` 等），保证
各探针在同一套鉴权（`X-Auth-Key`）与响应契约下运行；仅标准库，不依赖压测 runner。

### 3.13 配置示例

- `tenants.example.json`：provision 模式租户凭据示例（4 租户，`auth_key_env`
  指向环境变量名，前缀 `ECHOMEM_TENANT_*_KEY`）；
- `instance-profiles.example.json`：机器规格 profile 矩阵示例（供容量阶梯 case
  引用，`tenant_config` 指向 `performance/tenants-*.server.json` 形态）。

## 4. 指标定义

| 指标 | 数据来源 | 统计方法 |
|---|---|---|
| 读 QPS / 延迟 / 错误率 | 客户端埋点（requests.csv） | 分组 count/wall；有序数组线性插值分位 |
| 写四段延迟 / 回拒率 | 客户端埋点 | 同上 |
| 写后读一致性窗口 | consistency_check 埋点 | P50/P95 + 超时计数 |
| 服务端读延迟 | `/metrics` `echomem_recall_duration_seconds` | 桶累计插值 P50/P95/P99 |
| 服务端写延迟 | `echomem_session_commit_duration_seconds` | 同上 |
| CPU | `echomem_process_cpu_seconds_total`（user+system）帧差 ÷ 墙钟 | 场景均值 + 峰值区间 |
| 内存 | `echomem_process_resident_memory_bytes` | 基线（种子后帧）/ 峰值 / 冷却后 / 未回落 |
| 并发水位 | `echomem_http_requests_inflight` | 窗口内峰值 |
| commit 队列 | `echomem_session_commit_queue_depth` | 窗口内峰值 + 时间线 |
| commit 成功保证 | 客户端按 session 配对 submit/done | `commit_durability`：接受数/完成数/接受后失败/poll 超时（单列） |
| 租户公平性 | 客户端按场景×租户分组 read | `tenant_fairness`：组间 P95 max/min 比、变异系数 |
| RSS 趋势 | `/metrics` RSS 时间序列 | 最小二乘斜率（MB/min）+ R² |
| CPU 时间序列 | `/metrics` CPU counter 帧差 | `cpu_utilization_series`：每帧利用率 % |
| 劣化绝对值（基线→洪峰 ms、+delta） | summary.scenes read 统计 + degradation | `degradation_measurements`：baseline/flood/delta/ratio |
| commit 完成耗时（submit→completed，ms） | commit_done 埋点 | `commit_completion_latency`：P50/P95/P99/max |
| 租户等待差（最快/最慢 P95、额外等待 ms） | tenant_fairness 分组 | `fairness_measurements`：slowest_waits_extra_ms |

「注入阻塞检索」判定信号集（场景 D，`--degradation-threshold` 默认 2x）：

1. 洪峰窗口读 P95 劣化 ≥ 阈值；
2. 洪峰窗口 `engine_calls` 增量 ≈ 0 而延迟上升 → 锁/排他竞争；增量正常而延迟上升 → 资源竞争；
3. 洪峰窗口读错误数 > 0；
4. `inflight` 峰值 ≥ 总并发 90% → 请求堆积。

四项特性信号（summary.json `signals.signals_found`）：

| 信号 | 触发条件 | 对应特性 |
|---|---|---|
| commit 成功保证被违反 | `commit_durability.guarantee_violations > 0`（202 已接受但最终未 completed） | 1 |
| commit 提交阶段被拒绝 | `submit_rejected_total > 0`（分类明细输出，客户端不重试） | 1 |
| 租户延迟不均衡 | 某场景 `tenant_fairness.balanced=false`（租户间 P95 max/min ≥ 3x） | 2 |
| 疑似内存泄漏 | RSS 斜率 ≥ 5 MB/min（`resources.rss_trend.slope_mb_per_min`） | 3 |
| 读劣化/锁竞争/请求堆积 | 见上方 D 场景信号 | 1（search 优先） |

## 5. 测试对应关系

### 5.1 压测核心（tests/test_performance.py）

| 设计意图条目 | 测试 |
|---|---|
| Prometheus 文本解析规则 | `PrometheusParseTests` |
| 分位数线性插值与空值 | `PercentileTests` |
| 分组汇总 / 错误分类 / QPS | `SummarizeTests.test_summarize_reads_and_errors` |
| 劣化倍数与缺数据语义 | `SummarizeTests.test_degradation_*` |
| 窗口切片 / 一致性 / burst 汇总 | `SummarizeTests.test_window_slice` / `test_consistency_summary` / `test_burst_summary` |
| 场景矩阵展开与合法性 | `ScenarioMatrixTests` |
| mix token 序列 / 线程分配 / 错误分类 | `LoadgenTests` |
| 写事务四段计时与失败中止 | `WriteTransactionTests` |
| counter 差值 / gauge 峰值 / 直方图分位 | `MonitorAnalyticsTests` |
| CPU 利用率时间序列（帧差） | `FeatureGuaranteeTests.test_cpu_utilization_series` |
| commit 成功保证（配对与违规判定） | `FeatureGuaranteeTests.test_commit_durability_*` |
| 租户公平性（均衡/不均衡判定） | `FeatureGuaranteeTests.test_tenant_fairness_*` |
| RSS 趋势斜率（增/平/采样不足） | `FeatureGuaranteeTests.test_rss_trend_*` |
| 中文/英文消息分句 / static 单租户约束 | `PrepareTests` |
| locomo 种子加载（单样本/多样本/all/未知抛错）与真实会话灌入 | `PrepareTests.test_locomo_seed_batches_*` / `test_seed_tenant_from_conversations` |
| run_stress 参数校验（static 拒绝 cleanup / provision 允许 / locomo 数据集与 filter 校验） | `RunStressArgsTests` |
| 特性结论判定（PASS/FAIL/INCONCLUSIVE 全分支） | `FeatureVerdictTests` |
| 量化测量（commit 完成耗时 / 绝对劣化 / 租户等待差） | `SummarizeTests.test_commit_completion_latency` / `test_degradation_measurements` / `test_fairness_measurements` |
| 特性量化 measurements 断言（成功率/劣化 ratio/多等时长/小时外推） | `FeatureVerdictTests.test_all_pass` / `test_fairness_fails` / `test_memory_leak_fails` |
| record 序列化 | `SerializationTests` |
| 报告区块完整性（方法/场景/指标字典 + 支撑事实可见） | `ReportTests.test_build_html_contains_sections` |
| 报告再生成时间线重建（gauge 求和 / CPU 帧差） | `ReportTests.test_chart_series_from_metrics_csv` |
| 报告再生成入口（从制品重建 report.html） | `ReportTests.test_regenerate_report` |

### 5.2 验收与正式套件（tests/test_acceptance.py、tests/test_formal_suite*.py、tests/test_formal_data_report.py）

| 设计意图条目 | 测试 |
|---|---|
| acceptance：缺测量 INCONCLUSIVE、unavailable 显式 | `AcceptanceTests.test_missing_measurements_are_inconclusive_and_unavailable_are_explicit` |
| acceptance：report6 质量 gate 拒绝空 marker 结果 | `AcceptanceTests.test_report6_quality_gate_rejects_empty_marker_results` |
| acceptance：模型分析输入 secret-free 且保留验收结论 | `AcceptanceTests.test_model_input_is_secret_free_and_preserves_acceptance` |
| acceptance：HTML 验收矩阵 / 评审状态渲染 | `AcceptanceTests.test_html_renders_acceptance_matrix` / `test_html_renders_review_resolution_when_present` |
| acceptance：饱和无拒绝不判通过 / 拒绝须有 reason_code | `AcceptanceTests.test_saturation_without_rejections_does_not_claim_contract_pass` / `test_saturation_rejection_requires_reason_code` |
| acceptance：无效基线不产生劣化通过 / 标签违规不算覆盖通过 | `AcceptanceTests.test_report4_invalid_baseline_cannot_produce_degradation_pass` / `test_metric_label_violation_is_not_a_coverage_pass` |
| acceptance：公平性用 commit 完成吞吐 / 评审状态对模型可见 | `AcceptanceTests.test_fairness_uses_commit_completion_throughput` / `test_review_resolution_is_explicit_and_model_visible` |
| formal_suite：场景目录（report6 / pr421 / complete）与容量阶梯 | `Report6ScenarioTests`（目录/容量点/混合比例/D 洪峰/instance-profile 一致性） |
| formal_suite：case 命令映射 / 契约摘要 / CSV 规范化 | `FormalSuiteAdapterTests` |
| formal_suite：子进程超时与进程组终止 | `FormalSuiteTimeoutTests` |
| formal_data_report：数值明细 + 缺失服务端证据可见 | `FormalDataReportTests` |

### 5.3 探针（tests/test_failure_tools.py）

| 设计意图条目 | 测试 |
|---|---|
| capability 探针：404→NOT_IMPLEMENTED、未配置→INCONCLUSIVE | `FailureToolTests.test_capability_probe_classifies_404_as_not_implemented_and_unconfigured_as_inconclusive` |
| cursor 对账：嵌套 operation/archive 提取 | `FailureToolTests.test_cursor_payload_extracts_nested_operation_and_archive` |
| fault 控制：无真实控制→INCONCLUSIVE | `FailureToolTests.test_fault_control_without_real_control_is_inconclusive` |
| cursor 对账：消息集比对 | `FailureToolTests.test_cursor_reconcile_compares_message_set` |

## 6. 使用示例

```bash
# 本机快速冒烟（并发档 1,16，时长 15s）
python performance/run_stress.py --quick --tenants 4 --duration-s 60

# 全矩阵（默认并发档 1,4,16,64，A/B/C/D，C 三档读:写比）
python performance/run_stress.py

# 外网部署：静态身份 + 关闭自助租户创建 + metrics 不可达时降级
python performance/run_stress.py \
  --echomem-url http://203.0.113.10:8010 \
  --auth-mode static --auth-key XXX --tenant-id T1 --user-id U1 \
  --tenants 1 --scenarios A,D --concurrency-steps 1,8 --duration-s 30

# 只测纯读与注入洪峰
python performance/run_stress.py --scenarios A,D --concurrency-steps 1,16,64

# 真实对话种子：locomo conv-30 复制灌入每租户，测后清理租户
python performance/run_stress.py --tenants 8 --seed-source locomo \
  --sample-filter conv-30 --cleanup-identities

# locomo 全样本 / 多样本
python performance/run_stress.py --tenants 8 --seed-source locomo \
  --sample-filter all --duration-s 60

# 报告再生成：对已有运行重建增强版 report.html（无需重跑压测）
python -m performance.report performance/results/<ts>

# 正式验收套件（默认 pr421 场景目录，3 轮，每 case 独立 run_stress 子进程）
python -m performance.formal_suite \
  --base-url http://127.0.0.1:8010 \
  --tenant-config performance/tenants.example.json \
  --profile pr421 --repeats 3

# report6 + pr421 全目录（需真实 EchoMem config.json 做 preflight）
python -m performance.formal_suite --profile complete \
  --preflight-config /path/to/echomem/config.json \
  --tenant-config performance/tenants.example.json \
  --out-dir results/performance/formal_capacity

4U8G 单实例的常规验收使用 `performance/run_4u8g_complete.sh`。它执行
PR397/report(6) 的 12 项和 PR421 的 27 项，默认单轮、只使用 4U8G，不执行
长稳态 `soak`，也不启动 4U16G。`suite.json` 会记录实际场景列表和每个
case 的状态；`suite.html` 会保留逐 case 结果，不能把未执行场景混入总体结论。

# 单独渲染套件数据报告（suite.json → suite.html）
python -m performance.formal_data_report \
  results/performance/formal_<ts>/suite.json suite.html

# 探针（示例）：真实限流阶梯扫描 / 故障编排 / 光标对账
python performance/probes/limit_failure_sweep.py \
  --base-url http://127.0.0.1:8010 --tenant-config performance/tenants.example.json \
  --session-root <session_root> --out-dir results/performance/probes
python performance/probes/fault_suite.py \
  --plan fault-plan.json --out-dir results/performance/probes \
  --base-url http://127.0.0.1:8010
python performance/probes/cursor_reconcile.py \
  --commit-csv commit_results.csv --out reconcile.json \
  --base-url http://127.0.0.1:8010
```

结果写入 `performance/results/<ts>/`：`summary.json` / `requests.csv` /
`metrics_samples.csv` / `report.html` / `config.json`。正式套件结果写入
`results/performance/formal_<ts>/`：`suite.json`（清单 + 逐 case 摘要 +
验收结论）、`acceptance.json`、`model_analysis_input.json`、`summary.json`
（套件总结）、`suite.html`（数据报告），每个 case 的 `run/` 保留 run_stress
原生产物。
