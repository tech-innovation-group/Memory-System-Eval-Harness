# EchoMem 多租户上线压测方案

## 目标

验证三件事：租户数据绝不串读；Search 的在线延迟在 Commit
后台任务运行时仍满足 SLO；高负载下不同租户不会出现长期饥饿或无限排队。

本方案必须回答四个具体问题，而不是只给出 PASS/FAIL：

1. 一次 Commit 从入队到最终完成平均需要多久，P50/P90/P95/P99 和最大值是多少。
2. 哪个租户、在什么时间、因为多深的队列出现了延迟。
3. 限流发生在压测端还是 EchoMem 服务端，429、Retry-After 和实际执行开始时间分别是多少。
4. FIFO、Search 优先、双通道和租户公平调度在同一负载下的数值差异是多少。

## 前置条件

- 每个租户必须有独立的 tenant、user 和 API Key。
- 运行配置使用 `stress/echomem/tenants.json`，Key 只通过环境变量注入。
- 关闭 Mock、resource-engine 和 MCP；固定 EchoMem 代码、模型、温度和索引数据。
- 测试开始前记录服务版本、配置哈希、模型配置哈希和机器资源。
- 任一跨租户误命中都使隔离项失败；共享 Key 的运行只能作为探索性性能数据。

## 隔离验证

对每个租户写入一个随机 marker。对每个 writer/reader 有向组合执行搜索，
共 `N x N` 个探针：

- writer == reader：必须命中自己的 marker。
- writer != reader：必须不命中 marker。

报告保存每个探针的租户、Session、HTTP 状态、耗时、命中结果和错误，
不保存 API Key。探针覆盖不完整时，隔离结论为 `INCONCLUSIVE`。

## 工作负载

每个场景先预热 60 秒，正式采样 10 分钟，冷却并排空 60 秒，独立重复 3 次。
正式结论使用三次运行的中位数，并同时报告运行间的最小/最大值；若同一指标的
三次运行变异系数超过 10%，增加到 5 次，不得挑选单次最好结果。
所有策略使用同一批次的租户、消息、Session 数和模型配置。

| 场景 | 租户 | 每租户 Search | 每租户 Commit | 正式采样 | 用途 |
|---|---:|---:|---:|---:|---|
| 单租户基线 | 1 | 0.5 / 1.0 RPS | 1 / 2 / 4 次/分钟 | 10 分钟 | 建立延迟基线 |
| 轻量多租户 | 4 | 0.25 RPS | 1 次/分钟 | 10 分钟 | 验证隔离和基础公平 |
| 中等混合负载 | 4 | 0.5 RPS | 2 次/分钟 | 10 分钟 | 观察 Search/Commit 竞争 |
| 高负载 | 4 | 1.0 RPS | 4 次/分钟 | 10 分钟 | 找到队列和延迟拐点 |
| 租户倾斜 | A 高流量，B/C/D 低流量 | A: 2.0；其他: 0.25 RPS | A: 8；其他: 1 次/分钟 | 10 分钟 | 验证限流和抗饥饿 |
| 长稳态 | 4 | 0.5 RPS | 持续提交 | 30~60 分钟 | 验证内存、队列和资源泄漏 |

每个场景都要同时输出“目标发送量”和“实际完成量”。例如 4 个租户、
每租户 0.5 RPS、10 分钟，Search 目标量应为
`4 × 0.5 × 600 = 1200`；若实际只有 900，报告必须显示缺口及原因。

## 调度策略对照

1. **全局 FIFO**：Search 和 Commit 共用一个队列，记录队列等待和阻塞。
2. **严格 Search 优先**：Search 优先执行，同时记录 Commit 的最长等待和饥饿次数。
3. **双通道 FIFO**：Search 和 Commit 使用独立 FIFO 队列，分别设置容量；
   runner 中对应 `--scheduler-policy dual-lane`。
4. **单通道租户公平**：所有操作共用容量，但租户间轮询，队列内 FIFO；
   runner 中对应 `--scheduler-policy tenant-fair`。
5. **双通道 + 租户公平**：Search 和 Commit 各自独立，每条通道内按租户轮询，
   队列内 FIFO；runner 中对应 `--scheduler-policy dual-lane-tenant-fair`。
   这是生产候选，但仍需用服务端真实队列和限流数据验证，不能只凭客户端结果决定。

压测端策略只代表请求生成和准入策略，不能代替服务端内部调度证明。
若要证明服务端限流，还必须采集服务端队列长度、拒绝数、Retry-After、
执行开始时间和服务端 request ID。

因此正式调度结论分为两组：

- 服务端观察组：使用 `--no-client-admission`，压测端只按目标速率发送，
  不替 EchoMem 排队；同时将客户端 worker 数设置为足以覆盖预期并发，
  并单独报告仍然存在的客户端 executor 等待。
- 客户端整形组：分别运行上述五种策略，比较 FIFO、Search 优先、双通道
  和租户公平对请求发送端的影响；该组不能直接证明 EchoMem 内部采用了
  相同策略。

## 必须输出的指标

- 全局及逐租户：提交数、完成数、失败数、超时数、成功率。
- Commit 和 Search：平均、P50、P90、P95、P99、最大值。
- 每条请求：入队、开始、结束、排队、服务、端到端耗时。
- 延迟事件：租户、Session、请求 ID、入队时间、开始时间、完成时间、耗时、
  队列深度、状态、错误和 Retry-After。
- 公平性：逐租户 P95 最大/最小比值、Jain 指数、吞吐差异、
  最大等待时间、连续未服务时间。
- 资源：RSS、CPU、线程、FD、Swap、采样时间和 RSS 斜率。
- 隔离：`N x N` 探针总数、同租户命中率、跨租户误命中率。
- 可复现信息：EchoMem commit、配置 SHA256、模型 endpoint/model、
  测试机规格、容器限制、运行 ID 和时区。

Commit 状态轮询不计入 Commit 写入队列；它只用于观察已提交任务的完成状态。

## 初始上线门槛

正式门槛应由业务 SLO 最终确定。没有业务 SLO 前，先使用：

- 跨租户误命中率为 0%，同租户命中率为 100%。
- Search 成功率至少 99.9%，P95 不超过单租户基线的 2 倍。
- Commit 最终完成率 100%，无未解释超时。
- 任意租户 P95 不超过最快三租户 P95 的 2 倍。
- 队列长度不能在长稳态中持续增长，最大等待必须有上界。
- 长稳态 RSS 不能持续线性增长；异常请求必须能关联到原始日志。

## 服务端证据要求

客户端记录的排队时间只能说明压测端等待，不能证明 EchoMem 内部队列。
正式上线结论至少需要 EchoMem 为每次请求记录以下字段，或通过等价的
Metrics/Tracing 提供：

```text
request_id
tenant_id
operation
received_at
queue_entered_at
execution_started_at
finished_at
queue_depth
active_workers
status_code
retry_after
terminal_status
```

服务端可以通过响应 JSON 的顶层或 `telemetry`/`observability`/`debug`
对象返回这些字段，也可以通过等价的响应头返回。测试平台会把原始字段、
每条请求的服务端排队时间、执行时间、队列深度和覆盖率写入 CSV 与 HTML；
字段缺失时显示为缺失，不用客户端时间代替。

推荐的响应字段示例：

```json
{
  "request_id": "req-01",
  "status": "accepted",
  "telemetry": {
    "tenant_id": "tenant-a",
    "received_at": "2026-08-26T08:00:00.100Z",
    "queue_entered_at": "2026-08-26T08:00:00.101Z",
    "execution_started_at": "2026-08-26T08:00:00.120Z",
    "finished_at": "2026-08-26T08:00:01.200Z",
    "queue_depth": 3,
    "active_workers": 2,
    "terminal_status": "completed"
  }
}
```

Commit 的异步状态查询也应返回同一个 `request_id` 或可关联的
`operation_id`，并在最终状态中补齐 `finished_at` 和 `terminal_status`。
如果只能提供 Prometheus 汇总指标而不能逐请求关联，报告可以展示总体
队列趋势，但不能回答“哪个租户的哪一次 Commit 在什么时间排队”。

报告必须把客户端等待和服务端等待分成两列。没有服务端字段时，
对应结论只能标记为“客户端黑盒观测”，不得写成“服务端限流已验证”。

## 推荐生产策略

在正式数据出来之前不预先宣称某一种策略最优。默认候选方案为：

- Search 和 Commit 使用两条独立异步通道，不互相占用执行槽位。
- Search 使用全局并发上限加每租户配额，超限返回 429，并提供 Retry-After。
- Commit 使用持久化队列、每租户公平轮询和等待 aging，避免高流量租户长期占满队列。
- Commit 状态轮询不占用 Commit 写入配额。
- 生产策略由 Search P95、Commit P95、最大排队时间、429 比例和租户公平性共同决定。
