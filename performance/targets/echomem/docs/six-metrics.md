# 4U8G 六项指标：测试用例与证据契约

本方案基于 PR31 的 `scenes -> orchestrator -> probes -> acceptance` 架构，改动提交独立新 PR。
样本由脚本生成简短事实；EchoMem 的记忆提取、路由、Embedding 和检索必须使用真实模型。
测试平台的单元测试不构成服务性能证据。

当前优先要求是输出六项主要实测数据，不设置性能合格门槛。
请使用[运行手册的观测入口](six-metrics-runbook.md#优先产出数据不应用性能门槛)。
下文完整矩阵中的数值门槛属于旧 `--six-metrics` 验收配置，不适用于当前容量观测；
慢、低吞吐或质量下降仍继续记录，不能据此输出“最大热用户=0”。

首次运行、依赖安装、凭据准备和故障排查见 [运行手册](six-metrics-runbook.md)。
PR397/421 的调度、隔离、恢复、指标要求在这里分解为 M1–M6；
这不是“必须合并两个 PR 才能运行”的版本判断，运行时以真实 HTTP 契约和证据为准。

## 运行入口

配置样例：[six-metrics.profile.example.json](six-metrics.profile.example.json)。
将配置放在自己的运行目录，修改 base_url 和 resource_container，旁边放 tenants.json
及被测 EchoMem 实际使用的 echomem.config.json；相对路径以 profile 文件所在目录解析。
模型密钥仅保存在本地配置/环境，不提交 Git。Docker socket 只交给可信的测试 runner，
因为 kill-9 恢复探针需要控制被测容器；resource_container 必须是专用测试容器。
样例默认 `commit_recovery.allow_container_restart=false`，确认是专用测试容器后改成 true。
`commit_recovery.container` 如单独填写，必须与 `resource_container` 相同。
expected_lanes 按实际启用配置填写；本轮未执行到的启用 lane 仍应保留并标记证据不足。

从仓库根目录执行：

```bash
python -m performance --target echomem \
  --profiles /absolute/path/instance-profiles.json \
  --profile 4U8G --six-metrics \
  --out-dir /absolute/path/results/run-001
```

环境文件通过 `--env-file` 指定。测试控制 Token 只通过环境变量
`ECHOMEM_TEST_CONTROL_TOKEN` 传递，不填写在报告或提交进 Git。
实例 profile 使用 `resource_container` 指定被测容器。
runner 必须能读取该容器的 Docker 信息；实际限额要求 4 CPU、8589934592 bytes。
资源校验失败时禁止把结果标为 4U8G 实测。

`--six-metrics` 与 `--quick` 互斥。六指标目录不含 soak。
`--check-only` 只检查配置、Docker、只读服务接口及最小真实模型请求，不灌种、不注入故障、不重启。
正式启动还会再次执行前置检查；没有测试 Token 或受保护接口未连通时提前返回完整六项证据不足报告。
`--six-metrics` 不接受 `--scenarios` 裁剪；单场景诊断使用 `--quick`，不得当成完整验收。
同一次运行固定 EchoMem 版本、模型、配置和租户身份，变更后使用新输出目录。
当前 `--resume` 仍未验证版本指纹，正式验收暂不使用断点续跑。
退出码：六项全部通过为 0；有明确 FAIL 为 1；证据不足或准备失败为 2。
程序生成了 HTML 不等于六项通过。

租户凭据过期时，通过公开注册接口准备新的测试身份，不修改 EchoMem 内部代码：

```bash
python -m performance.targets.echomem.provision \
  --base-url http://127.0.0.1:8010 --count 32 \
  --out /absolute/path/tenants.json
```

此命令适用于启用了公开 bootstrap 注册的测试环境；生产环境由管理员提供独立凭据。
输出文件权限为 0600，不上传 Git。将其绝对路径填入 profile 的 `tenant_config`。

## 数据准备与 Search 用例

每个租户使用独立凭据，写入一段带唯一编号的事实，经过
open -> add -> commit -> completed -> Search marker 命中后才开始计时。
例如："周四下午三点评审接口设计，编号 PERFANCHOR-0-0-0-本次随机标记"。
每次运行使用新的随机编号，防止旧记忆误命中。
只核验 Search 返回 `items` 内的标记，debug 中回显 query 不算命中。
每个租户只使用自己的 query 池与 agent/user 身份。
准备阶段的 marker 可见性与服务健康分开记录：若已经命中但服务返回 degraded，
允许继续采集故障数据，但该请求的质量仍判失败，降级原因写入逐请求 CSV。
故障隔离基线已经降级时只能报告 INCONCLUSIVE，不能把已有问题归因于故障注入。

| 样本组 | 输入 | 统计 |
|---|---|---|
| recall | 已注入事实的唯一标记检索 | HTTP 错误、降级数、平均/P50/P95/P99 延迟、标记命中率 |
| mixed | recall + 你好/谢谢/基础运算/翻译 | 按 recall/no_recall 分组延迟、召回命中率、无召回样本误召回率 |

标记命中率是检索正确性，不等于 LoCoMo QA/Judge 准确率。
未命中、超时和模型降级全部保留在分母，错误响应不能从延迟列表中消失。
通用问题是否应召回依赖业务定义；当前 no_recall 列表是明确的测试假设。
Search 延迟必须再按响应 Explain 的 `executed_layers` 拆成“未调用意图 LLM”、
“调用意图 LLM”和“路由层未观测”三组，分别报告样本数、占全部已发比例、
平均/P50/P95/最小/最大耗时，以及严格无效、HTTP/传输错误、降级计数。
所有已发请求均计入路径样本数，无计时或非法计时另外标记，不能从分母删除。
Atomic 引擎耗时继续单列；路径分组记录的是端到端
Search 耗时，不应冒充意图模型自身的精确推理时间。缺失 Explain 的旧样本不得并入
快速路径，Thinking 是否开启必须读取生效配置摘要，不能根据总延迟猜测。

## M1：活跃用户与热用户容量

依次执行 capacity-2/4/8/16/32/64，每档 60 秒，目标 RPS 等于身份数，
每个身份持续检索已灌入的内容；每档分别执行纯召回和包含 no-recall/Commit 的混合流量。
记录实际发出请求的身份数、样本数、质量成功率、P95/P99、实际完成吞吐、
HTTP/传输错误、CPU、RSS 和 Commit 积压恢复。Commit 必须分别报告计划提交数、
收到 202 数、最终完成数、未完成/失败数、提交时间跨度，以及从 202 到终态时间区间
计算出的峰值在途数；总提交数不能冒充服务端并行度。

当前容量结论采用“请求完成合同”，不设置 P95、吞吐或召回质量门槛：
纯召回和混合负载的计划 Search 必须全部发出且无 HTTP/传输失败；混合负载中的
Commit 必须全部受理并最终 completed，积压不能持续增长。召回质量和延迟仍完整展示，
但不参与“请求是否完成”的判定。最高零错误档与紧邻的非零错误档都必须用新身份各重复
三次，只用于确认“连续三轮零错误档”；它不是最大热用户容量，单次轻微错误也不会直接写成边界。
只有出现服务崩溃、OOM 或积压无法恢复，才能报告硬容量边界；否则最高档仍只报告为
“已测试到 N”，并继续展示错误率、有效吞吐、Commit 接受数与最终完成数。
设置 `capacity_levels: [2, 4, 8, 16, 32, 64, 128]` 可扩展档位；
平台自动生成对应负载用例，并在灌种前要求至少 128 个独立凭据，不复用四个租户冒充 128 个用户。
档位必须严格递增，不会自动无限加压；每次更改档位使用新的结果目录。

这里的身份是持续活跃热用户代理，不直接等同于一天内的 DAU。
可通过 profile 的 `dau_model.requests_per_user_per_day` 与
`dau_model.peak_to_average_ratio` 显式声明业务模型：
估算 DAU = 实测可持续 RPS ×86400 ÷每人每日请求数 ÷峰值系数。
没有业务模型时 DAU 留空，不编造数字；报告必须同时列出 Search 限制值、Commit 限制值
以及二者较小的保守值，不能只展示较大的数字。
样例中的 20 次/人/日、峰均比 5 仅是演示假设，正式使用前须按业务确认。
当前测试的是“固定 1 份短记忆的活跃租户代理”，不是海量历史记忆下的线上最大容量。

## M2：单租户故障隔离

准备 stress-a/b/c/d 四个独立租户，各自检索自己的种子事实。
采集故障前样本 -> 对 A 注入 reject 或 delay -> 采集故障中样本 -> 撤销故障。
调用 PR449 `/api/inspect/test-control/fault`，携带测试 Token。
该接口的 `duration_s` 只能在 0.1 到 300 秒之间，`delay_ms` 必须为 0 到 30000 的整数。
平台在正式套件启动前校验这些参数；例如填 600 秒会直接报配置错误，不先灌种或反复运行 24 轮。
报告分别保留控制接口启用/撤销的 HTTP 状态与负载观测结果；控制请求失败不能只归因为基线不健康。
必须看到 A 实际返回 429/503，或者延迟实际增加；控制接口返回 200 不足以证明故障生效。
每个 B/C/D 的故障前和故障中至少 100 个 Search，计算
`(during_p95 - before_p95) / before_p95`，取最差旁观租户。
出现旁观租户错误或劣化 >20% 判 FAIL；控制链路/样本缺失判 INCONCLUSIVE。
报告保留逐请求行，禁止只保存截断后的摘要。
轮流对四个租户注入 reject/delay，各重复三轮，共 24 个故障用例。
每轮包含故障前、故障中、恢复后三组，目标租户与旁观租户都采集。
故障中采样必须在故障有效期内完成；超过 300 秒等配置 TTL 的样本不能算隔离通过，
避免把故障自动过期后的快速响应混成“故障中”的性能证据。
“任意单租户”在报告中明确限定为本轮测试的四个租户，不外推未测租户。
缺测试 Token 或已验证的种子查询时，不启动故障矩阵，保留 24 例分母。

## M3：同档位多租户公平性

只使用 fairness-bounded，不用 tenant-skew 的不均匀到达数据做等权判定。
四个租户同一窗口内持续 Search，共提交 32 个 Commit，每租户 8 个。
统计相同窗口各租户 Commit 完成数/秒、Search P95。
Jain(x) = (sum(x))² / (n × sum(x²))。
Commit 使用完成吞吐；Search 使用 P95 的倒数，两个指数均须 >=0.9。
零完成租户保留，缺失租户判证据不足；同时检查质量成功率，防止“大家都失败却公平”。
样本不足 100 Search/租户则不出正式通过结论。

## M4：Commit 洪泛下 Search 服务保障

recall-baseline：4 租户、16 RPS、32 Search workers、60 秒，测无 Commit 的热记忆 Search。
search-priority-blackbox：4 租户、16 RPS、120 秒，第 15 秒提交 32 个写事务。
基线与洪泛阶段复用同一租户记忆、query 池、16 RPS 和 32 Search workers，仅改变后台 Commit 负载；
旧版本基线为 8 RPS 的报告不适合用来隔离 Commit 洪泛的影响，应重新测量。
仅 HTTP 202 算已受理异步 Commit，同租户/session/archive 的重复响应不重复计数。
至少 32 个真实受理；四个租户各自至少 100 个基线及积压重叠 Search。
对积压窗口计算 P95，要求 <=5 秒且相对基线劣化 <=20%，召回质量成功率 >=99%。
输出受理时间、完成时间、积压重叠样本、基线/洪泛 P95 与比值。
逐租户核对同样的 SLO，不能用大多数快租户的整体 P95 掩盖慢租户。
这些证据证明服务保障；内部“严格先调度 Search”还需服务端排队/调度序列证据。

## M5：202 Commit 崩溃恢复

向测试会话写 12 条各约 1000 字符的消息，记录服务端 message ID 与提交顺序。
默认总量低于 20000 字符的 auto-commit 阈值；实际阈值不同需要同步调整负载。
Commit 带稳定幂等键；确认 202 且状态仍为 pending/running 等未完成态后执行真实 kill-9/start。
恢复后轮询原 archive，读取 history/archive/cursor 对账集合和顺序。
原任务自主完成之前禁止再次提交 Commit，避免客户端重试重新入队被误判成自动恢复。
自主完成之后，再用相同幂等键重试，须指向原 archive，按当前协议检查 replayed。
同时只读核对 cursor 的 last_successful：若原受理响应明确回显了请求幂等键，
恢复后的 completed receipt 也对应原 archive，但该键缺失或改变，则记录幂等持久化 FAIL。
接口不可读、未回显键或读取到其他 archive 时不推断丢键；诊断仅输出匹配布尔值，不输出键内容。
报告分别展示：收到 202 数、崩溃前未完成数、恢复 completed 数、集合一致数、顺序一致数。
多样本报告必须保留计划数、已执行数、通过/失败/证据不足数和未执行数；未执行样本不得
从计划分母消失。缺少消息对账时，总缺失消息数为未采集，同时展示已完成对账的样本数
及其已知缺失消息数。缺少 pending、cursor 或顺序检查，不能凭 completed 标记通过。
只对实际样本报告通过率，不把少量样本称为对所有故障的数学保证。
只有配置测试专用容器时才执行重启；不能指向生产容器。
未获得受理且未完成的样本时，不重启容器，记 INCONCLUSIVE。
通过必须同时满足恢复终态、崩溃前未完成、消息集合、cursor、顺序、幂等六项检查。

## M6：每层每租户四元组

公开 `/metrics` 采集全局 lane/fanout 数据，受保护
`/api/inspect/tenant-observability` 获取逐租户快照。
分母由 profile 的 expected_tenants × expected_lanes 固定。
每个单元必须有 queued / wait_seconds_total / exec_seconds_total / rejected_total。
校验数值非负、有限且计数字段为整数；缺字段、重复单元和缺租户均不能通过。
关闭的可选模型 lane 必须依据真实生效配置排除，并在报告中解释。
综合报告重新核验字段与重复单元，并逐轮展示有效、缺失、非法和重复计数。
多轮数据的主表显示最后一轮快照；不得把不同轮次的单元拼成完整覆盖，也不得将
最后一轮 PASS 直接作为所有轮次 PASS。所有计划轮次均完成验证后才可汇总为通过。
比较负载前后快照：各 tenant/lane 的 accepted_total 必须有正增量，累计字段不能倒退。
观测快照在恢复探针重启容器前读取，避免进程重启清空指标影响增量判断。

## 输出与归因

`4U8G/six-metrics.html` 为六项验收报告，`six-metrics.json` 为机器可读数据。
保留每个 case 的 records.csv、summary.json、Search/Commit CSV 与 metrics_samples.csv，
以及故障、恢复和逐租户观测原始 JSON。
`progress.json` 记录准备、灌种、当前负载场景和完成数量；它仅表示执行进度，不代表验收通过。
责任分类：测试平台、部署控制面、外部模型、Recall、调度、Commit 持久化、可观测性。
模型失败不能只凭 HTTP 200 算通过，也不能直接归因为 EchoMem 性能不足。

尚未覆盖：跨运行续跑指纹验证、内部严格调度顺序证明、所有可能的租户内部故障。
M2 的真实故障控制目前只覆盖请求级 reject/delay，不等同于模型断网或存储损坏；
M5 默认是一次故障样本，不能把一次成功外推成任意崩溃时机下 100% 保证。
这些限制不能由六项报告中的其他通过结果替代。
