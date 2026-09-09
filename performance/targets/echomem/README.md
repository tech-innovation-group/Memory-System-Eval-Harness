# EchoMem 压测 target（`performance/targets/echomem`）

观测型 4U8G 六项黑盒压测请从
[六项观测运行手册](docs/six-metrics-observation.md) 开始，入口为
`python -m performance.targets.echomem.observation_run`。它不应用性能门槛，
默认不包含 soak。旧的 [六指标用例](docs/six-metrics.md) 与
[旧运行手册](docs/six-metrics-runbook.md) 记录历史 SLO 验收语义，不作为观测结论。

## 六项测试最短路径

以下是新测试人员唯一需要先跑通的入口。完整参数、口径和故障恢复说明见
[六项观测运行手册](docs/six-metrics-observation.md)。所有命令均在仓库根目录执行。

### 0. 获取当前测试代码并确认入口

PR 尚未合入时：

```bash
git clone https://github.com/tech-innovation-group/Memory-System-Eval-Harness.git
cd Memory-System-Eval-Harness
gh pr checkout 32
git rev-parse --short HEAD
```

PR 合入后直接使用目标分支，并保留 `git rev-parse HEAD` 的输出作为测试版本证据。
不要复制别人机器上的结果目录后只重新生成 HTML；正式结果必须由本次源码、配置和
真实服务共同产生。

### 1. 安装并准备本地目录

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p .local-stress
cp performance/targets/echomem/docs/six-metrics.profile.example.json \
  .local-stress/six-metrics.profile.json
```

将被测 EchoMem **实际生效的** `config.json` 放到
`.local-stress/echomem.config.json`。不要使用测试平台保存的旧模板。

### 2. 准备独立租户和密钥

仅在允许 bootstrap 注册的专用测试服务执行：

```bash
.venv/bin/python -m performance.targets.echomem.provision \
  --base-url http://127.0.0.1:8010 --count 32 \
  --out .local-stress/tenants.json \
  --env-file .local-stress/test.env
chmod 600 .local-stress/tenants.json .local-stress/test.env
```

`tenants.json` 只保存 `auth_key_env` 引用；真实 key 写入 Git 忽略的
`test.env`。继续在 `test.env` 中配置真实 LLM、embedding 和
`ECHOMEM_TEST_CONTROL_TOKEN`，不要把值放进 profile、报告或 Git。

### 3. 核对专用 4U8G 容器

```bash
docker inspect echomem-stress-4u8g \
  --format '{{.State.Running}} cpu={{.HostConfig.NanoCpus}} memory={{.HostConfig.Memory}}'
```

profile 中的 `resource_container` 必须是这个专用容器。M5 会执行真实
`kill -9/start`，M6 也会借助重启观测计数器代际；禁止指向机器人、共享或生产容器。

### 4. 先跑快速诊断版，确认链路

执行前先做一分钟检查：

```bash
test -f .local-stress/six-metrics.profile.json
test -f .local-stress/echomem.config.json
test -f .local-stress/tenants.json
test -f .local-stress/test.env
bash -n performance/targets/echomem/run_six_metrics.sh
.venv/bin/python -m performance.targets.echomem.observation_run --help
curl -fsS http://127.0.0.1:8010/health
docker inspect echomem-stress-4u8g \
  --format '{{.State.Running}} cpu={{.HostConfig.NanoCpus}} memory={{.HostConfig.Memory}}'
```

不要把 `test.env` 的内容打印到终端或 CI 日志。模型是否接入以运行产物中的 LLM 与
Embedding 真实预检为准；`真实 HTTP：是`、`mock 模型：否` 或普通 Search 返回 200
都不能单独证明 Provider/API Key 在整场测试中正常。

```bash
performance/targets/echomem/run_six_metrics.sh quick \
  .local-stress/six-metrics.profile.json \
  results/echomem-4u8g-smoke .local-stress/test.env
```

quick 只验证真实模型、真实 HTTP、租户、故障控制、恢复和报告链路，结果固定为
`PARTIAL`，不能作为完整六项结论。

### 5. 跑正式完整版

```bash
performance/targets/echomem/run_six_metrics.sh full \
  .local-stress/six-metrics.profile.json \
  results/echomem-4u8g-formal .local-stress/test.env
```

| 版本 | 用途 | 是否能作为六项正式数据 |
|---|---|---|
| `quick` | 几分钟到数十分钟内检查模型、接口、租户、故障和报告链路 | 否，固定 `PARTIAL` |
| `full` | M1→M3→M4→M2→M5，M6 全程采样；保留完整矩阵与分母 | 是 |
| `m6` | M6 专项复测：1 个真实 reject 用例 + 1 次真实崩溃恢复 | 仅作为 M6 正式数据 |

M6 修改后需要快速复测时：

```bash
performance/targets/echomem/run_six_metrics.sh m6 \
  .local-stress/six-metrics.profile.json \
  results/echomem-4u8g-m6 .local-stress/test.env
```

`m6` 专项仍使用 4 个独立租户、真实模型、真实 HTTP、真实故障控制和真实容器重启；
依赖的基线/洪泛负载和 Commit 终态观察均限制为 45 秒，洪泛使用 8 个 Commit 屏障，
并去掉对 M6 结论没有新增证据的
M2 其余 23 个故障组合与 M5 其余 2 个重复样本。正式 `full` 的 M3/M4 窗口、M2/M5
分母均不受影响。

### 6. 断点续跑

```bash
# 中断后使用完全相同的 profile 和输出目录续跑。
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json --profile 4U8G \
  --env-file .local-stress/test.env \
  --out-dir results/echomem-4u8g-formal --resume

```

只测单项时加 `--metrics M1`，可替换为 `M2` 至 `M6`。M6 会自动执行
NORMAL、QUEUE、REJECT、RESET 所需依赖负载，但不会把依赖数据冒充其他指标完成。

### 7. 看结果

先打开 `results/echomem-4u8g-formal/report.html`。页面默认展示六项含义、关键图表、
数据状态和 EchoMem 模块改进建议；逐请求表、资源逐点采样和原始 JSON 默认折叠。

| 状态 | 含义 |
|---|---|
| `MEASURED` | 本方案要求的分母已采集，不代表性能一定达标 |
| `PARTIAL` | 有真实数据，但重复次数、场景或分母不完整 |
| `BLOCKED` | 前置条件或关键接口阻塞，未形成有效数据 |
| `EXECUTION_ERROR` | 测试平台或执行过程异常 |

报告必须与同目录的 `summary.json`、`suite.json`、CSV 和执行清单一起留档；不能只截图，
也不能删除失败、超时、空召回或 pending Commit 后重新计算。

### 8. 六项是否真正执行的核对表

正式报告按 `M1 → M3 → M4 → M2 → M5 → M6` 展示，编号仍对应需求定义。看到
`MEASURED` 只表示分母采集完整，不表示性能达标；还要核对下列原始证据：

| 指标 | 测试动作 | 报告必须出现的数据 |
|---|---|---|
| M1 最大容量与 DAU | 预注入可验证记忆，按热用户档同时发纯 Search 和混合读写；持续拥塞后停止升档 | 每档发送/严格成功/质量失败/HTTP/传输错误、失败责任域、P95、有效吞吐、Commit、CPU/RSS、首个拥塞档和 DAU 情景换算 |
| M2 单租户故障隔离 | 对目标租户分别注入 `delay`、`reject`，其他租户持续真实记忆 Search | 目标故障确实生效、旁观租户 before/during/after P95、劣化百分比、恢复分母；正式默认 24/24 用例 |
| M3 多租户公平性 | 4 租户和 8 租户同档等需求，Search 与独立 Session Commit 周期并发 | 每租户 Commit 窗口内完成吞吐、Search P95、两种 Jain、零完成租户、停压后完成和发压缺口 |
| M4 Search 洪泛优先级 | 先测纯 Search 基线，再分别进行均匀 Commit 洪泛和单租户洪泛 | 基线/洪泛 Search P95、错误和召回质量；Commit 计划/202/拒绝/完成/未终态；只用确认存在非终态 Commit 的重叠窗口 |
| M5 202 Commit 崩溃恢复 | Commit 获得 202 且仍未完成时真实 kill-9，重启后观察原任务并进行幂等 replay | 202 回执、kill 时状态、恢复终态、history/archive/cursor 集合与顺序对账、重复执行检查；正式默认 3/3 样本 |
| M6 分层分租户可观测性 | 从测试开始按固定间隔采集观测端点，并覆盖 NORMAL/QUEUE/REJECT/RESET | 每个实际启用的 `tenant × lane` 均有 queued/wait/exec/rejected 四元组、逐帧缺失/非法值、进程代际和重启后计数器变化 |

最终入口的正式报告是输出目录根部的 `report.html`。旧入口生成的
`objective-suite.html` 属于历史 O1-O7 验收报告；它缺少上述场景时显示
`INCONCLUSIVE`，不能据此判断“模型未接入”。

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
