# EchoMem 4U8G 六项指标黑盒压测方案

## 1. 目的与范围

本方案用于在 4U8G 单实例上，以黑盒方式验证 EchoMem 面向线上多租户服务的六项能力：

1. 单实例支持的最大用户量（DAU）和最大热用户量；
2. 任意单租户发生故障时，其他租户 Search P95 的劣化；
3. 稳态下不同租户的 Commit 吞吐和 Search 延迟公平性；
4. Commit 洪泛时，Search 是否严格优先；
5. 已返回 HTTP 202 的 Commit 在崩溃恢复后是否 100% 重放、不丢序；
6. 每层、每租户是否有排队深度、等待时长、执行时长、拒绝数四元组指标。

测试平台只通过真实 HTTP 接口和 `/metrics` 观察服务，不修改 EchoMem 内部代码。故障和重启控制必须由部署侧显式提供；没有控制能力时报告为 `INCONCLUSIVE`，不能伪造通过。

## 2. 方案改成合成可控语料

为了避免完整 LoCoMo 数据集注入耗时过长，默认不使用 LoCoMo，而是使用少量短文本和固定问题构造可控测试数据。每轮测试只需要少量文本和固定问题，速度更快，也更容易控制记忆长度、问题难度和租户数量。

具体流程如下：

1. **为每个租户写入一段结构化文本。**
   文本包含人物、时间、项目、地点和唯一编号等事实，例如：

   ```text
   林晓在 2026 年 3 月负责北极星知识库迁移，
   计划在杭州研发中心完成方案评审，事实编号为 PERFANCHOR-0-0-0。
   ```

2. **走真实的 `open → add → commit → completed` 流程。**
   测试平台不直接写本地文件，也不伪造记忆结果，而是让 EchoMem 自己接收消息、执行 Commit、抽取并生成记忆。HTTP `202` 只表示 Commit 已被接受，轮询到 `completed` 才表示异步任务结束。

3. **用预先定义的问题检索，并提前写好预期答案或关键实体。**
   例如问题是“林晓在 2026 年 3 月负责什么项目？”，预期关键实体是“北极星知识库迁移”。关键实体和唯一编号由测试平台保存，用于后续自动判定。

4. **Search 返回后检查是否命中预期实体或唯一标记。**
   命中预期事实才算召回正确；返回空结果、返回其他租户的事实或只返回无关记忆都不能算正确。平台同时记录准确率、P50/P95/P99、错误、超时和服务端降级。

5. **另外加入 no-recall 问题。**
   例如“今天有什么工单？”，这类问题预期不命中已注入的测试记忆。空结果是正常业务路径，不计为召回失败，但要统计接口成功率、空结果率和延迟。

这套数据设计分别支撑两类测试：

- `recall-only`：只使用已注入、已验证可召回的记忆问题，观察召回准确率、稳定性和延迟；
- `mixed`：混合 recall、no-recall 和后续扩展的线上问题，观察真实流量下的稳定性、延迟、错误和资源竞争。

### 2.1 Seed 失败时的场景隔离

真实模型 Commit 可能因为模型限流、依赖超时或服务重启而无法在准备窗口内完成。
这不应阻断所有指标。平台会按场景记录 `seed_dependency`：

- 容量、租户公平调度、Commit 恢复和 `/metrics` 采集仍继续发送真实 HTTP 请求；
- 依赖预置记忆的热缓存/召回质量结论标为 `INCONCLUSIVE`；
- 不会把没有请求的 `BLOCKED` 记录当成 EchoMem 性能失败；
- 每轮结果保留 `seed_status`、`seed_tenant_count` 和 `seed_evidence_status`，便于
  区分“平台准备失败”和“服务实际故障”。

容量阶梯不会为了 16/32/64/128 个活跃用户额外执行同数量的真实模型灌种；灌种租户数只按
实际需要热记忆证据的最大场景确定。没有需要 seed 的场景时，平台直接跳过 warm-up。

## 3. 测试数据：快速合成记忆

为了避免完整 LoCoMo 注入耗时过长，默认使用短文本生成可控记忆。每个租户写入独立事实，例如：

```text
事实编号 PERFANCHOR-0-0-0：林晓负责北极星知识库迁移，
计划在周三下午于杭州研发中心完成方案评审。
```

对应的 recall 问题不直接带编号：

```text
林晓在哪里负责什么项目？
```

每条问题绑定预期事实编号。Search 返回后，测试平台检查返回记忆内容是否包含该编号：

- 包含预期编号：召回正确；
- 返回空结果：召回失败；
- 返回其他记忆但不含预期编号：召回错误；
- 服务端返回 `degraded`：单独统计为降级/容量证据，不伪装成正确召回。

### 3.1 Commit 前置流程

所有种子数据必须经过真实写入路径：

```text
open session
  -> add message
  -> commit
  -> poll commit_status
  -> status=completed
```

HTTP `202` 只代表服务接受了异步任务，不代表记忆已经可检索。正式 Search 开始前，平台会用候选 query 做一次预验证，只有真实命中记忆的 query 才进入 `recall` 流量池。

如果本轮只需要测容量、调度或可观测性，而没有可复用的预置记忆，正式套件会传入
`--allow-unverified-search`。此时仍然发送真实 HTTP Search，可以统计吞吐、延迟、
并发和服务端指标；但因为没有证明记忆命中，热缓存、召回准确率以及依赖命中的
优先级结论必须标记为 `INCONCLUSIVE`，不能当作 PASS。

### 3.2 两类 Search 流量

| 流量 | 内容 | 空结果含义 | 用途 |
|---|---|---|---|
| `recall-only` | 预注入且预验证命中的语义问题 | 空结果或错误记忆都算召回失败 | 测召回准确率、延迟、稳定性 |
| `mixed` | recall + no-recall + 后续扩展的普通问题 | no-recall 空结果是正常路径，不算错误 | 模拟线上真实读流量 |

默认 `mixed` 比例为 recall 70%、no-recall 30%。两类 query 必须分开统计，不能只报告一个总 P95。

## 4. 六项指标总览

| 指标 | 主场景 | 核心结论 | 主要依赖 |
|---|---|---|---|
| 最大 DAU / 热用户量 | `K` 容量阶梯 | 最大满足 SLO 的用户档位 | 真实多租户、Search 流量 |
| 单租户故障隔离 | `F` + 旁观租户 Search | 其他租户 P95 劣化百分比 | 真实故障控制 |
| Commit/Search 公平性 | `fairness-steady` | 两套 Jain 分别计算 | 独立租户、固定速率 |
| Search 严格优先 | `S` / `search-priority-blackbox` | Commit 洪泛期间 Search 延迟是否受控 | 服务端真实竞争 |
| 202 Commit 恢复 | `commit_recovery_probe.py` | 消息集合、顺序、cursor、幂等重放 | kill-9/restart 控制 |
| 四元组可观测性 | `/metrics` + 并发负载 | 每层/每租户指标是否真实变化 | EchoMem `/metrics` |

## 5. 指标一：最大 DAU 和最大热用户量

### 4.1 测什么

在 4U8G 单实例上逐步增加活跃用户和热用户，找到仍满足服务目标的最大档位。

- DAU 代理：独立租户数、活跃 session 数、持续发起请求的用户数；
- 热用户代理：在固定时间窗口内重复发起 Search 的用户数；
- 不把租户数直接等同于 DAU，报告中必须分别展示租户、session、用户代理和热请求数。

### 4.2 怎么测

1. 准备 2、4、8、16、32、64、128 个活跃用户档位；
2. 每个租户预先注入少量合成记忆；
3. 使用 `mixed` Search 流量，默认 recall 70% / no-recall 30%；
4. 逐档运行固定时长，记录 Search 请求、成功率、P50/P95/P99、429、超时、CPU、RSS、线程、队列；
5. 容量场景关闭 Commit 生成，避免写入耗时污染纯读容量；
6. 下一档必须明确超过 SLO，才能把上一档称为容量上限。

### 4.3 判定与数据

每档至少输出：

```text
tenant_count
active_session_count
hot_user_count
search_submitted / search_succeeded
http_error / timeout / 429
recall_accuracy
search_p50 / p95 / p99
cpu_mean / cpu_peak
rss_peak
queue_peak
```

推荐判定条件由运行配置指定，例如 Search 成功率至少 99%、P95 不超过服务目标、无持续资源耗尽。没有“合格档 + 下一档失败”的连续证据，只能标记 `INCONCLUSIVE`。

## 6. 指标二：单租户故障对其他租户的影响

### 5.1 测什么

租户 A 出现模型超时、429、连接拒绝或慢依赖时，租户 B/C/D 的 Search 是否被拖慢。

### 5.2 怎么测

1. 所有租户先用相同的 mixed 或 recall-only 流量预热；
2. 记录故障前稳定窗口的每租户 Search P95；
3. 只对租户 A 注入一种真实故障；
4. B/C/D 继续发送同速率 Search；
5. 故障结束后继续观察恢复窗口；
6. 对每个旁观租户独立计算故障期间和恢复期间的劣化。

### 5.3 公式

```text
劣化百分比 =
(故障期 P95 - 故障前 P95) / 故障前 P95 × 100%
```

报告必须列出：

| 租户 | 故障前 P95 | 故障期 P95 | 劣化百分比 | 错误率 | 恢复时间 |
|---|---:|---:|---:|---:|---:|
| tenant-B | ... | ... | ... | ... | ... |
| tenant-C | ... | ... | ... | ... | ... |
| tenant-D | ... | ... | ... | ... | ... |

测试平台没有真实故障控制端点时，不得用普通高并发替代单租户故障，结论为 `INCONCLUSIVE`。

## 7. 指标三：不同租户的 Commit/Search 公平性

### 6.1 比较对象

Jain 比较的是不同租户之间的资源分配，不是比较同一个租户内部的 Commit 和 Search。

例如有四个租户：

```text
tenant-A、tenant-B、tenant-C、tenant-D
```

分别得到四个 Commit 吞吐值和四个 Search 延迟值，然后各自计算一套 Jain。

### 6.2 怎么测

1. 至少使用 4 个独立租户和独立凭证；
2. 所有租户使用相同消息大小、相同 Search query mix 和相同目标速率；
3. 先 warm-up，再进入固定时长稳态窗口；
4. 每租户以固定速率发送 Commit，例如 2 RPM；
5. 每租户以固定速率发送 Search，例如 2 RPS；
6. 不使用一次性 barrier 的短时结果作为正式公平性结论。

### 6.3 两套 Jain

Jain 公式：

```text
Jain(x) = (Σxi)^2 / (n × Σxi^2)
```

**Commit 吞吐 Jain**

```text
xi = 第 i 个租户在正式窗口内 completed 的 Commit 数 / 窗口分钟数
```

只统计最终进入 `completed` 的 Commit，不把 HTTP `202` 当成完成吞吐。

**Search 延迟 Jain**

Search P95 越小越好，不能直接把毫秒值代入 Jain。先转换为服务效用：

```text
utility_i = 1000 / Search_P95_i_ms
Search_Jain = Jain(utility_1, utility_2, ..., utility_n)
```

报告同时展示每租户原始值：

```text
tenant | commit_completed | commit_throughput | search_p95_ms | search_utility
```

Jain 接近 1 表示租户之间均衡，较低表示资源集中在少数租户。Jain 高不代表绝对性能高，还必须同时查看 P95、错误率和吞吐。

## 8. 指标四：Commit 洪泛时 Search 严格优先

### 7.1 测什么

大量后台 Commit 持续进入服务端在途或队列时，交互 Search 是否仍按优先级快速响应。

### 7.2 怎么测

1. 先使用 recall query 建立热缓存；
2. 运行 Search-only 基线窗口，记录 P50/P95/P99；
3. 同时启动大量 Commit，确保请求真正到达服务端，而不是全部在客户端被拦截；
4. Search 继续以固定交互速率发送；
5. 记录 Search 到达、开始、完成时间和 Commit 队列/在途数量；
6. 对比无洪泛和洪泛期间 Search 的延迟与错误。

### 7.3 判定

```text
Search 劣化倍数 = 洪泛期 Search P95 / 基线 Search P95
```

不能仅凭“Search 还有返回”判定严格优先。若 Commit 全部被入口拒绝，也不能证明服务端做到了优先级。必须有：

- Commit 确实进入服务端竞争的证据；
- Search 在洪泛期间的 P95/P99；
- Commit 队列或 inflight 的变化；
- Search 错误、超时和 degraded 统计。

## 9. 指标五：202 Commit 崩溃恢复、重放和顺序

### 8.1 测什么

服务返回 202 后立即崩溃，恢复后是否能完整执行该 Commit，不丢消息、不乱序、不重复。

### 8.2 怎么测

1. 创建独立 session；
2. 按顺序写入带编号的消息：

```text
RECOVERY-001
RECOVERY-002
RECOVERY-003
...
```

3. 提交 Commit 并明确记录 HTTP 202、session_id、archive_id、idempotency key；
4. 在 202 返回后执行真实 kill-9 或容器重启；
5. 等待 `/health` 恢复；
6. 继续轮询 `commit_status`；
7. 用 history、archive、cursor 或只读包装接口做消息集合和顺序对账；
8. 用相同 idempotency key 重放一次，确认不产生重复归档或重复记忆。

### 8.3 判定

每一个已接受的 202 都必须有最终结果：

```text
accepted_202 == true
terminal_status == completed
message_set_equal == true
order_preserved == true
cursor_continuous == true
idempotent_replay == true
```

正式通过率：

```text
恢复成功率 =
所有条件都满足的已接受 Commit 数 / 已接受 HTTP 202 的 Commit 数
```

一次探针成功只能说明一次成功，不能直接宣称 100%。必须增加样本量并重复崩溃时机。

## 10. 指标六：每层、每租户四元组可观测性

### 9.1 四元组定义

每个预期的调度层和租户，都要观察：

| 指标 | 含义 |
|---|---|
| queued | 当前或累计排队深度 |
| wait | 请求从进入队列到开始执行的等待时长 |
| exec | 真正执行耗时 |
| rejected | 被拒绝或限流的请求数 |

### 9.2 怎么测

1. 开启 `/metrics` 采集；
2. 用 Search 和 Commit 并发制造排队、执行和拒绝；
3. 采集窗口前、负载中、窗口后的指标；
4. 按 lane、租户和时间窗口聚合；
5. 将指标变化与客户端 request_id、HTTP 状态和请求时间对账；
6. 至少覆盖交互 Search、后台 Commit、租户限流和 engine fan-out 等实际层。

指标名称存在不等于可观测性完成。必须证明负载变化会让对应数值变化，并能和客户端请求对上。

### 9.3 判定

每个实际存在的 lane 都应有：

```text
queued + wait + exec + rejected
```

若只能提供 lane 级而不能下沉到租户级，报告要明确标注覆盖边界。缺失某个层的证据时标记 `INCONCLUSIVE`，不能直接断言 EchoMem 未实现。

## 11. 测试平台执行流程

统一流程如下：

```text
读取实际配置
  -> /health 和 /metrics 预检
  -> 创建独立租户
  -> 注入短事实文本
  -> Commit completed
  -> recall query 预验证
  -> 执行六项场景
  -> 采集请求和服务端指标
  -> 对账、计算分位数/Jain/劣化
  -> 生成 summary.json、CSV、report.html
```

种子注入不计入正式压测窗口，但种子 Commit 和 Search 预验证结果必须保留在报告中。

## 12. 结果文件

每次运行保留：

- `config.json`：实际参数和配置摘要，API key 脱敏；
- `requests.csv`：逐请求记录；
- `metrics_samples.csv`：完整 `/metrics` 采样时序；
- `summary.json`：六项指标机器可读结论；
- `report.html`：人可读报告；
- `recovery.json` / 故障探针结果：恢复和故障场景证据。

Search 记录至少包含：

```text
query_kind
expected_terms
recall_matched
hit_count
quality_ok
degraded
stage_ms
tenant_idx
```

Commit 记录至少包含：

```text
commit_submit(202)
commit_done(completed/failed/timeout)
session_id
archive_id
message_id
content_hash
retry_count
```

## 13. 当前实现边界

PR29 测试平台已经具备：

- 合成短文本种子；
- recall/no-recall/mixed Search 分流；
- recall query 预验证；
- 预期事实匹配和逐请求记录；
- A/B/C/D/K/S/H 等读写、容量、洪峰和公平性场景；
- Commit completed、消息对账、Jain 和 `/metrics` 统计；
- 报告和原始 CSV 产物。

以下能力必须在运行时提供真实外部控制或足够证据，否则保持 `INCONCLUSIVE`：

- 单租户真实故障注入；
- 真实 kill-9 / 容器重启；
- 多租户独立凭证；
- 每个实际调度层的完整四元组；
- 固定速率、足够时长的正式公平性窗口。

完整 LoCoMo/`conv-30` 仍用于数据集准确率评测；本方案的合成数据用于快速、可控、可重复的六项服务能力压测，两者不混用为同一个准确率结论。
