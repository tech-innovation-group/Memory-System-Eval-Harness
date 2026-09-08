# EchoMem 4U8G 六项黑盒观测运行手册

本入口只回答“本次真实环境观测到了什么”，不回答“是否达标”。它不设置 P95、
准确率、Jain、吞吐或劣化比例门槛。所有 HTTP 错误、超时、空召回、未发送请求、
Commit pending/failed 都保留在分母。最终数据状态只使用 `MEASURED`、`PARTIAL`、
`BLOCKED`、`EXECUTION_ERROR`。

## 安装

在仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

需要一个专用 EchoMem 测试容器。容器必须实际限制为 4 CPU、8 GiB，并允许测试
进程读取 Docker inspect；M5/M6 RESET 会对该容器执行 kill-9 和启动。不要填写
共享、生产或名称模糊的容器。

## 凭据与配置

复制 `performance/targets/echomem/docs/six-metrics.profile.example.json` 到 Git
忽略的本地目录。`tenant_config` 至少配置 8 个独立租户，每个凭据只引用环境变量：

```json
{
  "tenants": [
    {
      "tenant_id": "tenant-1",
      "user_id": "stress-user-1",
      "account_id": "stress-account-1",
      "agent_id": "stress-agent-1",
      "auth_key_env": "ECHOMEM_TENANT_1_KEY"
    }
  ]
}
```

禁止在 JSON、Git、日志或报告中写 `auth_key`。观测入口发现明文 key 会直接拒绝
运行。真实 LLM、embedding 和故障控制 token 同样只从环境变量读取：

```bash
export ECHOMEM_TENANT_1_KEY='...'
# 继续设置 ECHOMEM_TENANT_2_KEY ... ECHOMEM_TENANT_8_KEY
export ECHOMEM_TEST_CONTROL_TOKEN='...'
export YOUR_LLM_API_KEY='...'
export YOUR_EMBEDDING_API_KEY='...'
```

若专用服务允许 bootstrap 注册，可一次生成引用环境变量的租户文件和本地密钥文件：

```bash
.venv/bin/python -m performance.targets.echomem.provision \
  --base-url http://127.0.0.1:8010 --count 32 \
  --out .local-stress/tenants.json \
  --env-file .local-stress/test.env
```

不要省略 `--env-file` 后再把生成的明文 `auth_key` JSON 用于观测入口；该入口会拒绝运行。
模型密钥和测试控制 Token 继续追加到同一个 `test.env`，运行时通过 `--env-file` 加载。

`preflight_config` 必须指向 EchoMem 实际使用的 JSON 配置。运行器从该配置推导
expected lanes，不接受在测试配置里写死四条 lane。它会在发压前真实调用 LLM 和
embedding endpoint，mock/fake 或错误模型不会生成成功结果。

## 完整运行

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json \
  --profile 4U8G \
  --out-dir results/echomem-4u8g-$(date +%Y%m%d-%H%M%S)
```

完整运行包括 M1 的跨租户 T 阶梯、租户内 U 阶梯及 Search/Commit/mixed/hotspot
四类开放到达负载；M2 的 24 个故障用例；M3 的 4/8 租户等需求窗口；M4 的
baseline/均匀洪泛/单租户洪泛；M5 的三个独立 kill-9 样本；M6 的 2 秒时序采样。
默认关闭 soak。真实模型速度和故障恢复时间不同，通常需要 8 到 16 小时。

M4 基线不要求 100% 准确率：必须每租户至少观察到一次真实事实/标记命中，且所有样本具有
完整延迟与质量记录；部分未命中、降级及错误继续进入分母。没有真实召回证据的租户单独标出，
不能用纯空召回或问候语充当记忆召回基线。发现基线包含写入时，必须修复负载后重测，不能直接比较洪泛劣化。

## 单项与 quick

报告改进、独立 M3 与快速执行编排的逐项状态见
[改进跟踪清单](report-improvement-tracking.md)。清单中的待办不代表已通过实测。

用 `--metrics` 选择单项：

```bash
# 将 M1 替换为 M2、M3、M4、M5 或 M6
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json --profile 4U8G \
  --out-dir results/m1 --metrics M1
```

M6 单项会自动执行 NORMAL、QUEUE、REJECT、RESET 所需依赖负载，但不会把这些
依赖数据冒充为其他指标的完整执行。quick smoke 使用短窗口和小样本，报告固定标记
`quick-non-complete` 与 `PARTIAL`，不能与完整采样混用：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json --profile 4U8G \
  --out-dir results/smoke --quick
```

quick 一般需要 10 到 40 分钟，取决于真实模型和 Commit 恢复时间。

## 续跑、停止与清理

同一命令追加 `--resume`。已完成的 case 和 M1 子目录会跳过，未完成的通用 case
从第一个缺失 `summary.json` 的场景继续。中断时按 `Ctrl-C`；每个故障用例都在
`finally` 中关闭故障，M5 每次 kill 后都会启动目标容器。

中断后仍应人工确认专用容器已运行，并用控制接口执行一次 disable：

```bash
docker inspect echomem-stress-4u8g --format '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}'
curl -fsS -X POST http://127.0.0.1:8010/api/inspect/test-control/fault \
  -H "X-EchoMem-Test-Token: $ECHOMEM_TEST_CONTROL_TOKEN" \
  -H 'Content-Type: application/json' -d '{"action":"disable"}'
```

## 产物

结果根目录固定保留：

- `execution-manifest.json`：运行时间、Git commit、所选指标、环境摘要和探针执行记录
- `suite.json`：场景、探针及完整原始引用；`tenant_observability_monitor` 保留本机单调时钟窗口、采样间隔上限及采集异常类型
- `records.csv`：合并后的逐请求记录
- `metrics_samples.csv`：合并后的资源/Prometheus 采样
- `summary.json`：M1-M6 四态观测汇总
- `tenant-observability-samples.json`：M6 两秒快照、队列和重启分段来源
- `tenant-observability-before.json`：M6 发压前同步基线，用于计算计数器增量
- `fault-isolation-*.json`、`commit-recovery-*.json`：逐探针证据
- `report.html`：结论先行的最终 HTML

汇总值均可追溯到 JSON/CSV。字段未采集时保持 `null`；运行器不会删除失败样本、
隐藏错误或用 0 填补缺失值。

### 正式 M4 的 CSV 证据与完整性

一键观测中的 `m4-baseline`、`m4-flood-uniform`、`m4-flood-single-tenant`
也需要真实的非终态证据，不能只因三个目录存在就标为 `MEASURED`。

- `commit_submit` 只有 HTTP 202 且包含 archive ID 才建立受理区间。
  `commit_done` 是一次轮询观察的汇总，不一定代表任务完成。
- `records.csv` 保存 `poll_evidence_version=echomem-poll-v1`、轮询次数、HTTP/传输异常数、
  `last_nonterminal_at_ms`、`observation_ended_at_ms` 与明确的 `commit_terminal_state`。
  只有成功状态查询明确返回 completed/failed/error 才写 `terminal_at_ms`；
  `completed_at_ms` 仅在 completed 时写入。timeout、404、停止观察均不证明任务执行失败。
- 按 tenant/session/archive 三元组对账，重复回执、重复观察和孤立终态单列，不能后写覆盖先写。
  `commit_results.csv` 的 `unresolved` 表示尚未确认终态，`ambiguous` 表示重复证据；
  `rejected` 是入口拒绝，`failed` 才是明确的任务失败。`observation_status` 另列轮询超时或停止。
- HTML 同时展示宽观察窗口与非终态确认窗口的 Search 样本、平均延迟、P95、错误和质量。
  在途峰值是客户端观察值，不等于服务端排队深度，更不能证明内部严格 Search 优先级。
- 实际生效的 tenant 数、query 模式和 barrier 参数写入 `summary.json.measurement_contract`，
  quick 缩减后的数量不会冒充原计划。缺清单、缺租户召回基线、计划 Commit 未全部受理、
  终态不明或确认重叠证据不全时保留数据并标为 `PARTIAL`。不以 P95 或质量数值作为性能准入门槛。
- 历史 CSV 不补造新字段，旧报告重新汇总可能降为 `PARTIAL`；这是证据不足，不是服务性能退化。

### M3 独立周期公平性（新版正式入口）

六项观测入口可在 profile 中显式配置 `semantic_seed_cache` 为已有容量运行目录的绝对路径。
目录必须包含 `identities.private.json`（权限 0600）及 `seed-evidence.json`；它们不是公开报告，禁止提交仓库。
配置的每个租户必须与缓存中的 tenant/user/account/agent/key 完全匹配且只匹配一次。
平台不会再次 Commit，而是对每个租户现场抽取 4 道事实问题发起真实 Search；全部校验通过后才发压。
旧的 PASS 不作本次证据，当前校验失败仍保留全部租户分母并阻止开始测量。
报告标注 `validated-cache`，语料数量按实际缓存统计；这代表复用记忆，不代表本版本重新生成过记忆。
未配置该选项仍走下述全新注入流程；M3/M4 组合运行复用同一次准备结果，M2 使用同一事实样本。

观测入口的 M3/M4 及 M2 依赖基线，种子使用 5 段固定事实文本（20 个事实、40 种自然语言问法），
每租户先验证其中 4 个跨主题问题，再进入负载。问题中不包含所求日期、地点等答案；
断言只检查返回 items 的记忆正文，忽略 query 回显、ID 和 debug 元数据。
随机编号只作诊断，不作为唯一前置条件。使用专用、未混入其他相冲突事实的测试租户。
失败的种子验证保留通过数/总数；负载期间任何错误、空召回或降级仍算质量失败。
M4 基线可用实际事实命中证明真实召回，不再要求编号命中；M2 断言答案别名而不是查询句本身。
M1 容量阶梯仍有独立的身份与记忆准备流程，不能把组合入口的种子通过当成 M1 全部租户验证通过。

与后面的历史洪泛补测不同，正式 M3 分别使用 4 和 8 个独立租户，每个租户
拥有独立的 Search/Write 限速器。同档位、同语料、同速率，不能让快租户拿走慢租户的发压配额。

| 阶段 | 每租户负载 | 统计口径 |
| --- | --- | --- |
| 0–30 秒预热 | Search 1 次/秒，不启动 Write | 不参与 Jain 测量窗口 |
| 30–300 秒测量 | Search 1 次/秒；每 30 秒启动一个独立写事务 | 每租户计划 270 次 Search、9 次 Write |
| 300–480 秒观察排空 | 不再启动新事务，继续观察已有事务 | 完成数另列，不能回填窗口内吞吐 |

Write 是 `open → add×4 → commit → poll`，不是每次直接重复提交同一个会话。
计划时刻对应事务开始，真正 Commit HTTP 发送会晚于 open/add；报告分别列启动数、
提交数、HTTP 202 受理数，不能把三者混为一谈。
每个租户的实际启动时刻、计划时刻、序号和发压延迟写入 `records.csv` 的 `arrival` 行。
worker 不足或请求慢导致实际启动不足，保留未启动数量，不能只展示成功请求。

Commit 吞吐按 `[30,300)` 内确认完成数除以 270 秒；Search 按请求开始时间选入该窗口，
即使响应晚于 300 秒也保留其完整延迟。对所有 4/8 租户分别计算吞吐 Jain 与 `1/P95` Jain，
零完成租户仍在分母中，全部为零时 Jain 为 undefined。排空阶段失败及最终未完成任务另外保留。
Jain 接近 1 只说明租户之间均匀，并不说明性能好；短窗口不能证明长期稳态。

快速模式是 3 秒预热、12 秒测量、每租户每 3 秒一次 Write、最多 30 秒排空，
始终为 PARTIAL；额外 quick 时长上限可能进一步截短，应以报告实际窗口和缺口为准。
没有执行时钟或仍使用旧 barrier 合约的历史运行保留数据，但不标为新版 M3 完整实测。

### M3/M4 历史洪泛补测的 Commit 证据

#### 突发与固定速率两种负载

默认`--commit-submit-rps 0`保留一次性突发；正数表示所有租户合计的固定提交速率，
不是每租户速率，也不是服务完成吞吐。两种模式分别报告，不能把降低入口突发后
错误变少称为EchoMem性能优化。所有任务都是不同Session的一次提交，被拒绝后不补发。

提交HTTP与终态轮询采用独立线程池：最多32个提交worker，每个已受理任务独立观察，
总计划数上限256。完成观察的时限从收到202算起，不因轮询启动延迟重新计时。
每个任务记录计划提交时间、实际开始时间与轮询启动时间；报告展示提交时间跨度、
最大提交延迟和计时覆盖数。如果客户端来不及发出，不能把计划RPS写成实际RPS。
轮询间隔为每次请求完成后1秒，其HTTP请求也会给服务增加负载，原始轮询数单列。

例如，在完成真实种子注入后运行3轮固定速率场景：

```bash
python -m performance.targets.echomem.acceptance.contention_matrix \
  --base-url "$ECHOMEM_BASE_URL" --container "$ECHOMEM_CONTAINER" \
  --seed-directory "$SEED_DIR" --output results/contention-paced \
  --expected-lanes commit,recall_engine,recall_intent_llm,recall_query_embedding \
  --repeats 3 --duration-s 120 --search-rps 0.5 \
  --commits-per-tenant 8 --commit-submit-rps 2 --commit-timeout-s 360
```

`SEED_DIR`使用容量测试保存的`identities.private.json`（权限0600）和
`seed-evidence.json`，前4个身份必须是4个不同租户、4个不同鉴权Key，且记忆仍存在。
`ECHOMEM_TEST_CONTROL_TOKEN`必须与服务只读观测接口一致，禁止写入命令输出或报告。
目标容器必须实为4CPU/8GiB，输出目录必须不存在；计划提交区间必须落在Search窗口内。
示例共32个Commit，按总2次/秒计划在15.5秒内提交；Search为每租户0.5次/秒。
这仍是有限后台积压场景，不等于长期稳态公平性或最大容量测试。每轮保留所有错误，
实际202受理不足32、预热召回无效或无法确认积压时，仍不给完整M4结论。

`performance.targets.echomem.acceptance.contention_matrix` 的固定租户洪泛补测会保存
每次提交的 HTTP 状态、可识别公共原因码、Retry-After、受理时间和终态轮询历史。
综合报告按轮次列出计划/记录数、202、HTTP 拒绝、未知原因、完成、失败与未终态。

负载中的 Commit 只提交一次：503/429 不自动重提，不能用后来成功的尝试覆盖拒绝。
轮询是对已受理原任务的只读观察，可在原期限内继续；轮询的错误也全部保留。
503 本身不能证明限流、模型欠费或 API key 异常，必须结合实际原因码和服务证据。
未识别的原因仅标 `UNRECOGNIZED`；历史未记录原因标 `NOT_RECORDED`，不导出响应正文。

兼容 EchoMem 的字符串 `error` 响应（如 `HTTP_LANE_SATURATED`、
`HTTP_INGRESS_SATURATED`、`COMMIT_UNAVAILABLE`），只导出固定公共枚举。
这一区分可用于定位 HTTP 入口拒绝、Commit 服务不可用等不同阶段，不能仅凭503推断。
采集器修复不会回填历史缺失原因，也不会改变历史样本的通过状态。

洪泛计划数与实际受理量分开。若锁定场景要求至少32个已受理Commit而实际只有16个，
报告会同时展示16次受理、剩余拒绝和真实Search结果，但不会将该轮判为完整洪泛证据。

M4同时保留两种时间区间：202返回至首次看到终态（或结束观察）的观察区间，
以及202返回至最后成功非终态轮询开始时刻的确认区间。后一种要求原任务终态不回退。
Search在确认区间内开始才进入`confirmed_overlap_search`，最后轮询空档的请求
仍保留在`overlap_search`中。每轮记录`overlap_protocol=nonterminal-poll-v1`，
报告列出两类样本数和P95；历史缺少非终态记录时不自动推断，不给完整M4结论。
这仅确认请求开始时存在后台任务，不证明整条Search都与Commit重叠或严格调度顺序。

#### 2026-09-08 单轮回执与积压复测

该轮用于验证采集契约，不是六项完整验收：4CPU/8GiB限制的既有服务、4个独立
预注入租户、每租户0.5 Search/s、基线和负载各60秒、每租户8次独立Commit，
每任务观察上限180秒。保持既有模型和服务配置，不重启、不自动重提拒绝任务。
本轮未重新采集完整模型配置和服务代码SHA，不能用容器名称代替版本证明。

| 观察项 | 实测数据 | 解释范围 |
| --- | --- | --- |
| 提交 / 202 / 拒绝 | 32 / 17 / 15 | 15次均有HTTP_LANE_SATURATED公共回执 |
| 受理后完成 / 失败 / 未终态 | 17 / 0 / 0 | 不包含被拒绝的15次，不代表崩溃恢复测试 |
| 状态轮询 | 1,644次，全部HTTP 200 | 只观察原任务，没有重提Commit |
| 非终态确认Search | 86/86严格有效；P95 2.504秒 | 请求开始时可确认13–17个Commit在途；不是队列深度 |
| 快速路径 / 意图LLM路径 | 69 / 17次 | 平均分别0.221 / 2.343秒；是路径总耗时 |
| 同窗Commit完成分布 | 0 / 2 / 1 / 1 | Jain 0.6667；后续排空不回填60秒窗口 |
| Search逆P95 Jain | 0.9964 | 短窗口相对均匀，不代表长期稳态 |
| M6锁定合同 | 4租户×4层，94/94帧字段完整 | 仅commit、recall_engine、recall_intent_llm、recall_query_embedding；不是所有内部层 |
| 退出状态 | 服务存活、OOM=false、容器重启计数0 | 4CPU/8GiB限制未变化；不证明宿主机独占 |

本轮仍未达到锁定的32个实际202洪泛数量，不得与上一轮受理数相加来凑门槛，
也不能把HTTP入口拒绝直接解释为模型API key失败。回执只定位直接拒绝阶段；
入口为什么持续占用仍需工作线程容量、持有时长和同请求内部计时证据。
M6本轮没有进程身份时间线，不能据此证明跨进程重启的计数连续性。
运行源码快照SHA256：`3a32db45bb0f3eb237118d6d3f1a1c9a78d28d125f335d44cb6427f4568e7212`。

#### 同日固定速率提交补测

新增模式的单轮验证：总2 Commit/s、32个不同Session；每租户0.5 Search/s，
基线和负载各60秒，原任务观察上限360秒。仍使用既有真实服务和记忆，不改配置、
不重启、不自动补发。源码快照SHA256：
`dac200119c2a4116255333f11da2b9fa28ebcff9080ec22118897e549d540941`。

| 观察项 | 本轮数据 |
| --- | --- |
| 提交 / 受理 / 拒绝 | 32 / 32 / 0 |
| 受理后完成 / 失败 / 未终态 | 32 / 0 / 0 |
| 计划 / 实际提交跨度 | 15.5 / 15.496秒，计时覆盖32/32 |
| 最大提交调度延迟 / 轮询启动延迟 | 14.549 / 1.151毫秒 |
| 状态轮询 | 4,917次，全部HTTP 200 |
| 非终态确认Search | 86/86严格有效，P95 2.385秒 |
| 请求开始时确认在途下界 | 3–32个Commit，不是队列深度或全轮并发峰值 |
| 60秒内Commit完成分布 | 1 / 1 / 1 / 1，Jain=1；样本少，不代表长期稳态 |
| Search逆P95 Jain | 0.9959 |
| M6采样 | 152/152帧，锁定4租户×4层字段完整；没有进程身份时间线 |
| 资源及退出 | 4CPU/8GiB限制未变化，服务存活，未OOM、未容器重启 |

这轮补到了单轮M4的受理数量、有效配对与确认积压样本，不是内部严格调度证明，
也不是六项全部完成。它与突发模式的拒绝差异来自负载形态与压测端调度变化，
不能称为EchoMem代码优化后的性能提升。两轮之间也有历史写入积累，记忆规模并非严格冻结。

### M6 过程完整性

M6 不只比较首尾。每一帧均使用运行前锁定的 `tenant × lane` 分母，空帧、
缺失字段、重复行、布尔值、负数及 NaN 不会被跳过。报告同时展示有效快照数、
每帧有效单元数，以及在全部采样帧中均完整的单元数；后者不是服务可用租户数量。

采样器在一次请求结束后等待 2 秒；HTTP 超时为 15 秒。默认最大采样空档为 20 秒，
可通过 `tenant_observability.max_sampling_gap_s` 调整。该参数约束证据完整性，
不是 Search 性能合格线。比较包括负载窗口首尾；旧产物没有本机时钟边界时，
只计算实际相邻帧间隔，不补造窗口覆盖证据。

计数器只在已确认的同一进程内做差。`queued` 下降是正常现象，累计计数下降
需要解释：同进程下降记录为异常；已确认重启则分段，不跨进程相减；身份未知
保持证据不足。M6 的 RESET 必须来自进程身份变化，不能仅凭计数下降或 PID
缺失推断重启。故障期间无法获取的帧仍保留，不能借“计划重启”隐藏采集失败。

只有每帧覆盖、采样间隔和进程分段核验完整，且实际观察到四种行为，M6 才为
`MEASURED`；缺数据为 `PARTIAL`，没有有效分母为 `BLOCKED`，采集失败或同进程
计数异常为 `EXECUTION_ERROR`。这些状态不代表服务性能是否达标。

## EchoMem 接口契约

当前完整执行需要：公开 Search、session open/message/Commit、commit status、history、
archive、cursor，以及受保护的 `/api/inspect/test-control/fault` 和
`/api/inspect/tenant-observability`。后者需返回 tenant、lane、queued、
wait/exec seconds total、rejected/accepted/completed/failed total。可靠切分 RESET
还需 `process_started_at`（可附 `process_id`）或每进程唯一的 `boot_id`；单独 PID
可能复用，不足以证明一直是同一进程。缺接口或身份字段时报告会
标记 `BLOCKED/PARTIAL`，不会降低测试要求。
