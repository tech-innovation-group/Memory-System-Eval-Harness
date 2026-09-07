# EchoMem 压测 target（`performance/targets/echomem`）

六项 4U8G 指标的输入、步骤、分母与判定见 [六指标用例](docs/six-metrics.md)。
第一次运行请从 [安装、环境检查到生成报告的完整手册](docs/six-metrics-runbook.md) 开始。
从仓库根目录运行 `python -m performance --target echomem`。
正式六指标使用 `--six-metrics`，默认不包含 soak。

EchoMem 记忆服务的正式压测与验收入口。复用通用 HTTP 压测框架
（`performance/`：`engine.py` worker 池 + `ctx.py` 请求原语 + `suite.py`
套件编排 + `probe.py` 探针），本目录提供 EchoMem 的：

- **场景语义**（`scenes/`）：读 / 写 / 混合 / 屏障 / 洪峰 / 容量 6 类负载场景；
- **正式场景矩阵**（`orchestrator/suites.py`）：26 例完整矩阵 / 22 例 4U8G
  bounded 目录 / quick 收敛子集；
- **验收**（`acceptance/`）：O1–O7 产品目标、PR421 门禁、调度 7 检查、
  13 特性判定、preflight / seed / probes 编排；
- **CLI**（`main.py`）：`python -m performance --target echomem ...`（仓库根目录）。

---

## 1. 目录结构

| 路径 | 职责 |
|---|---|
| `scenes/scene_*.py` | 负载场景（任务函数 `tasks` / `task(ctx)`，可选 `schedule` 阶段注入、`report` 质量钩子） |
| `protocol.py` | EchoMem HTTP 协议适配：端点级函数（`task_read` / `task_write` / open / add / commit / poll）与默认查询词 |
| `orchestrator/suites.py` | 正式 case 矩阵定义 + case → `Profile` 翻译（barrier/burst 参数） |
| `orchestrator/runner.py` | 单 profile 套件执行（挂 echomem 的 summarize 扩展、证据 CSV、/metrics 采样） |
| `orchestrator/probes.py` | 8 类配置化探针编排 |
| `orchestrator/report.py` | objective-suite.html 渲染 |
| `acceptance/` | preflight / seed / features / metrics / scheduler / objectives / evaluate |
| `probes/` | 有向行为验证探针（契约 / 故障 / 恢复 / 限流 / 对账 / 隔离） |
| `main.py` | CLI 入口（多 profile 编排、O1–O7 汇总、输出锁） |

---

## 2. 测试场景

### 2.1 负载场景（`scenes/`）

场景 = Python 模块，导出 `tasks`（`{read, write}` 映射）或 `task`；`schedule`
在 worker 启动前注册阶段注入；`report` 返回 EchoMem 特有质量分析（合并进
summary 的 `custom` 段）。

| 场景 | 文件 | 语义 |
|---|---|---|
| A 纯读基线 | `scene_a_pure_read.py` | 全部 worker 循环 `POST /api/retrieval/search`，query 从查询池轮询；空结果按锚词质量规则标记 `quality_ok` |
| B 纯写注入 | `scene_b_write_injection.py` | 写事务 = `open → add×N → commit submit → poll done`（四阶段独立计时；末条携带 anchor） |
| C 读写混合 | `scene_c_mixed.py` | 总 worker 按 `profile.load.mix` 权重拆成读者组 + 写者组并发运行 |
| K 固定速率容量 | `scene_capacity.py` | 读写混合持续打满，到达率按任务固定 rps 控制；基础 / 均衡 / commit-storm / search-storm / soak / capacity-N 共用 |
| S/H Commit barrier 风暴 | `scene_barrier.py` | 读负载全程打满；`barrier_at_s` 处一次性并发注入 `barrier_count` 个写事务，按租户分布（uniform/zipf/explicit），支持多波（`barrier_waves`/`barrier_cooldown_s`）与公平性下限（`floor_to_tenants`） |
| D 注入洪峰 | `scene_d_burst.py` | 持续读负载；`delay=(duration-burst_window)/2` 处启动 `burst_commits` 个写事务（max_workers=8，记录 `extra="burst"`） |
| D 多波变体 | `scene_burst_waves.py` | 洪峰多波注入（`burst_waves` 波，间隔 `burst_cooldown_s`） |

写事务全程走真实 HTTP：`/api/sessions/open` → `/api/sessions/{id}/messages`
→ `/api/sessions/{id}/commit` → poll `/api/sessions/{id}/commits/{archive}`。
检索走 `/api/retrieval/search`（`X-Auth-Key` 鉴权）。

### 2.2 正式 case 矩阵（`orchestrator/suites.py`）

case = 矩阵最小单元：`{label, scene, tenants, duration_s, search_rps,
commit_rpm, sessions_per_tenant, messages_per_session, commit_barrier* 系列,
search_workers/commit_workers, read_only, ...}`。两个显式常量目录，不做动态
叉乘：

**`complete_cases()`（26 例，完整正式目录）**：

- report(6) 12 例 —— 按并发度 1 / 2 两组（workers 8/16，A/B/C/D 四类）：
  - `A@1`/`A@2`：纯读容量（scene_capacity，read_only）
  - `B@1`/`B@2`：Commit barrier（scene_barrier，barrier 8/16）
  - `C8:1@1`/`C8:1@2`、`C4:1@1`/`C4:1@2`、`C1:1@1`/`C1:1@2`：8:1 / 4:1 / 1:1 读写混合（scene_capacity）
  - `D@1`/`D@2`：注入洪峰（scene_d_burst，10s 窗口 32 写事务）
- PR421 场景集 14 例：`baseline` / `mixed` / `commit-storm` / `commit-barrier`
  / `saturation` / `tenant-skew`（zipf 260）/ `capacity-16` / `capacity-2` /
  `capacity-4` / `capacity-8` / `capacity-32` / `search-priority-blackbox` /
  `search-storm` / `soak`（1800s）

**`four_u8g_cases()`（22 例，4U8G bounded 目录）**：report(6) 12 例 +
`baseline` / `mixed` / `commit-barrier` / `saturation` / `tenant-skew` /
`search-priority-blackbox` / `capacity-2` / `capacity-4` / `capacity-8` +
`fairness-bounded`（`capacity-*` 强置 `quick_commit_rpm=0` 关闭后台 Commit）。

**`QUICK_SCENARIOS`（quick 默认子集）**：`baseline, fairness-bounded,
search-priority-blackbox, saturation, capacity-2, capacity-4, capacity-8`。

### 2.3 quick 收敛

`--quick` 时经 `apply_quick`：`duration_s = min(原值, QUICK_DURATION_CAP_S)`、
barrier 计数双 cap（全局 cap 与 case `quick_barrier_count_cap`）、
`quick_commit_rpm` 覆盖 commit_rpm（capacity-* 为 0）、sessions 压到 1；
当前 quick 缩小灌种会话数，但仍执行灌种；它是诊断模式，不代替正式六指标验收。

---

## 3. 测试方法

### 3.1 前置条件

- 已部署 EchoMem HTTP 服务，使用 profile 的 `base_url` 指定地址；
- 独立租户凭据写入仅本机可读的 `tenant_config`，或通过 `auth_key_env` 引用环境变量。
  测试环境可以使用 [六指标文档](docs/six-metrics.md) 中的 provision 命令创建身份。

### 3.2 CLI

```
python -m performance --target echomem \
    --profiles <instance-profiles.json> \
    [--profile 4U8G] \
    --out-dir <results dir> \
    [--quick | --six-metrics] [--scenarios a,b,c] [--resume] \
    [--quick-duration-cap-s 30] [--quick-case-timeout-s 120] \
    [--quick-barrier-count-cap 32] [--quick-include-seed] \
    [--timeout-s 7200] [--skip-run] [--suite-path <json>] [--env-file <env>]
```

| 参数 | 作用 |
|---|---|
| `--profiles` | instance-profiles JSON（含 base_url / tenant_config / 探针段）；`${ENV:-default}` 占位展开 |
| `--profile` | 只跑指定 profile（默认全部） |
| `--out-dir` | 输出根目录；每 profile 的 suite 目录为 `<out>/<profile 名>`（如 `4U8G`），case 目录为 `<suite>/<label>`。quick+4U8G 用 4u8g bounded 矩阵（22 例），否则用 complete 矩阵（26 例） |
| `--quick` | bounded smoke 矩阵（QUICK_SCENARIOS + 收敛） |
| `--six-metrics` | 4U8G 六指标目录，9 个负载场景及配置化探针；不含 soak，与 quick/resume 互斥 |
| `--scenarios` | 按 label 过滤并保序（逗号分隔） |
| `--resume` | 跳过 case 目录已有 `summary.json` 的已完成场景，从第一个未完成场景继续；历史 run 合并进最终报告 |
| `--timeout-s` | 单 case 超时（默认 7200） |
| `--skip-run` / `--suite-path` | 只读已有 suite.json 重新生成报告，不重发压测请求 |

### 3.3 执行流程（`run_suite`）

对每个 profile：`prepare_command`（如有）→ `preflight_config` 门禁 →
`tenant_config` 灌种（`acceptance/seed.py`：provision/static 两模式，
open→add×N→commit→poll 写入含锚词的种子会话，生成检索 query 池）→
`select_cases` 逐 case（`load_scene` + `Engine.run` 进程内执行，ENV_ERROR /
TIMEOUT 语义）→ `run_configured_probes`（8 类探针）→ `evaluate_pr421_acceptance`
→ 写 suite.json / acceptance.json / objective-suite.json / objective-suite.html。

每个 case 输出目录（`<out>/<profile>/<fs_safe_label>/`）：

- `summary.json`（metrics 契约摘要 + details/parameters）
- `records.csv`（全量请求记录）
- `commit_results.csv` / `search_results.csv`（对账证据，含质量字段）
- `metrics_samples.csv`（`metrics_enabled` 时后台采样服务端 /metrics，2s 间隔；
  并把 `details.pr421_metric_coverage` 挂到该 run）

### 3.4 灌种（seed）

按 `tenant_config` 解析租户；每租户打开 `seed_sessions` 个会话、每会话写
`seed_messages` 条消息，消息携带唯一 anchor token（后续作检索质量锚点）；
commit 轮询到 completed。检索 query 由种子消息片段 + 锚词自动生成，纯合成、
样本构造不调用 LLM；服务端的记忆生成、Embedding 和检索使用真实模型。
灌种后还必须 Search 命中唯一标记，单凭 completed 不算准备成功。

### 3.5 探针（`probes/`，8 类，按 profile 配置段启用）

| 探针段 | 内容 |
|---|---|
| `capability_probe` | 可选契约探测（health/metrics/cursor 等；显式 404 才视为未实现） |
| `blackbox_contract` | 复用压测记录的会话/归档复查公开 history/archive/status/cursor/指标 |
| `missing_cases` | PR397 可观测性：写后读一致性、commit 状态机、冷/热检索延迟 |
| `concurrent_commit` | 同会话 N 次并发 commit：接受/拒绝、operation/archive 唯一性、终态收敛 |
| `fault_isolation` | 真实租户故障期间旁观租户 Search P95 劣化（≤20% PASS） |
| `commit_recovery` / `recovery` | 容器/进程 Commit 中途或 kill-9 后恢复、消息对账、幂等重放 |
| `fault_plan` | 按 plan JSON 编排故障 + 恢复 + cursor 对账 case |
| `limit_failure_sweep` | 有界负载扫描（levels 档并发 wave + 恢复），数据采集 |

`probes/` 下另有 `nxn_isolation` / `cursor_reconcile` / `disconnect_recovery` /
`auth_preflight` / `limit_failure` / `fault_injection` 等独立探针文件（不在上述
配置段编排内；N×N 租户隔离由 `acceptance/features.py` 特性 11 依据
`op="isolation_probe"` 记录判定）。

探针统一契约：`run(ctx)` + `ctx.check` 四态断言（PASS / FAIL /
NOT_IMPLEMENTED / INCONCLUSIVE）；缺前置条件记 INCONCLUSIVE，绝不把环境
问题归因为 EchoMem 缺陷。

---

## 4. 验收指标

### 4.1 产品目标 O1–O7（`acceptance/objectives.py`）

| 目标 | 判定来源 | 通过标准 |
|---|---|---|
| O1 单实例最大用户量 / 热用户量 | scheduler：容量阶梯 | `capacity-*` 有真实完成档 + 更高一档真实失败/超时边界 |
| O2 多规格实例调度与 config | 多 profile 完成情况 | ≥2 种规格均有真实完成场景（completed_runs>0） |
| O3 单租户故障下 Search P95 劣化 | fault_isolation 探针 | 旁观租户 P95 劣化 ≤ 20% 且故障已恢复 |
| O4 多租户公平性 | scheduler：Jain | 按 Commit 吞吐与 Search P95 双维 Jain ≥ 0.9（取较小值） |
| O5 Commit 洪泛时 Search 优先 | scheduler：priority | 洪泛样本（≥32 已受理 commit）+ 黑盒 Search P95 ≤ 5s |
| O6 Commit 崩溃恢复后 100% 重放不丢序 | recovery/cursor 探针 | 服务恢复 + 消息集合对账 + 幂等重放（同 key `replayed=true`） |
| O7 每层每租户四元组可观测 | /metrics 采样 | `recall_engine/recall_intent_llm/recall_query_embedding/recall_rerank/commit` 五 lane 每项有 queued/wait/exec/rejected 四元组 |

状态取值：PASS / FAIL / INCONCLUSIVE（证据不足，绝不静默放行）。

### 4.2 调度 7 检查（`acceptance/scheduler.py`）

`DAU / 最大热用户容量`、`多规格实例调度配置`、`单租户故障隔离`、
`Commit/Search 公平性 Jain`、`Search 优先于 Commit`、`Commit kill-9 恢复与
重放`、`分层/分租户调度可观测性` —— 全部基于真实压测证据严格计算；证据
缺失记 INCONCLUSIVE。

### 4.3 PR421 门禁（`acceptance/evaluate.py`）

- **B7 lane/fan-out 指标覆盖**：五 lane 四元组 + fanout（recall/commit 的
  exec/skipped），缺失或 tenant 标签违规不通过；
- **Search 成功率**：min(search.success_rate) ≥ 0.99（有洪泛样本时）；
- **report(6) 质量断言**：所有检索必须有确定性锚词断言（quality_asserted），
  空/未验证检索不允许假通过；
- **饱和拒绝契约**：过载时显式 429/503 + `Retry-After` + `reason_code`（含
  legacy 400），不静默失败。

### 4.4 13 特性（`acceptance/features.py`）

commit 异步/成功保证、租户公平性、无内存泄漏、资源利用率时间线、写事务
重试与对账、search 质量断言、读写隔离粒度、错误类型正确性、故障注入、
模型与配置预检、N×N 租户隔离、饱和拒绝契约、热租户旁观公平性。每特性带
`measurements` 量化证据；状态 pass / fail / not_run / known_limit / env_error。
其中「无内存泄漏」为通用能力：压测收尾（`suite.py` `_finalize_suite`）自动基于
各 case 的 `*_resident_memory_bytes` 指标做最小二乘趋势诊断，结果写入
`suite.json` 的 `memory_leak` 字段，报告渲染为「内存泄漏诊断」节；判定口径
（`performance/memory_leak.py`）：斜率 ≥ 5 MB/min 判 FAIL，采样窗口 < 600 s
判 INCONCLUSIVE，其余 PASS。

### 4.5 延迟阈值（`runner.py` 摘要 parameters）

- `search_delay_threshold_s = 2.5`（Search P95 参考阈值）
- `commit_delay_threshold_s = 10.0`（Commit 端到端参考阈值）
- 延迟统一记秒（`stage_ms/1000`），分位 p50/p95/p99。

---

## 5. 快速开始

```bat
:: 完整 26 例（数小时，先部署服务并准备自己的配置）
python -m performance --target echomem ^
    --profiles _config/instance-profiles.json --out-dir results\<ts>

:: bounded smoke（分钟级）
python -m performance --target echomem --profiles _config/instance-profiles.json ^
    --out-dir results\<ts> --quick --scenarios baseline,fairness-bounded

:: 中断后续跑（跳过已完成场景）
python -m performance --target echomem --profiles _config/instance-profiles.json ^
    --out-dir results\<ts> --resume
```

Windows BAT 由部署方另行提供，不在本仓库中；`_config/instance-profiles.json`
代表部署方准备的配置文件，不是仓库内置环境。输出：
`<out>/objective-suite.html`（O1–O7 可视化报告）。
