# EchoMem 本机 M1-M3 压测指南（完整六项可选）

**首次使用请只执行 M1-M3：容量、多租户公平性、Commit 洪泛下 Search 性能。**
先按第 1～5 节部署、注册独立租户并准备 profile，再执行第 6 节快速检查和第 7 节正式命令。
M1-M3 不要求 PR449、故障控制接口、测试控制 token，也不执行故障注入或容器重启。
本文后续 M4-M6 部分只适用于明确选择完整六项的使用者。

**Embedding 强制使用 `qwen3.7-text-embedding-flash`。** EchoMem 实际加载配置与
profile 的 `required_embedding_model` 必须一致，并通过真实 Provider 调用预检。
模型不匹配或预检失败时停止测试，不自动回退到其他模型。

所有命令显式传递 `--metrics`。不同版本的程序默认值不同，不能依靠省略参数选择前三项；
PR34 的代码默认六项，裸用 `quick` 包装脚本也会选中六项。

交付前必须核对 `summary.json` 的 `selected_metrics` 为 `["M1", "M2", "M3"]`，
并逐项查看状态和样本分母。独立并发探针、HTTP 2xx 数或服务 readiness 均不能代替 M1-M3。

这一个文件包含完整的本机部署、配置、执行和结果解释。测试直接访问本机 EchoMem，
不需要服务器，也不要求把容器限制为 4U8G。报告会记录容器实际 CPU、内存和镜像，
因此不同电脑的容量数据应分别比较。

> **当前代码位置**：六项观测入口已通过
> [PR32](https://github.com/tech-innovation-group/Memory-System-Eval-Harness/pull/32)
> 合入 `performance_refactor`。请直接检出并更新 `performance_refactor`，再执行文中的
> `observation_run.py` 和 `run_six_metrics.sh`。这是当前 M1-M6 仅有的两个启动入口。

> **报告出口自检**：当前入口无论完成、阻塞还是执行异常，都会在指定
> `OUTPUT_DIR` 写出 `report.html`。如果运行结束后没有该文件，本轮不能作为当前
> 六项结果，应核对实际命令并使用本文入口重跑。

## 开始前先确认版本与配置

| 对象 | 本机测试要求 |
| --- | --- |
| 测试平台 | 使用 `performance_refactor` 分支，记录实际 commit；不要检出 EchoMem 的同名分支来代替 |
| EchoMem | 从明确更新并锁定的 `develop` 开始；完整六项还需检查下文 PR449 接口要求，不能只凭分支名判断可测 |
| 单机后端 | `deployment.mode = "local"` 可以用于本机多租户压测，不需要为此改成云模式或安装 MySQL |
| 租户认证 | `auth.mode = "x_auth_key"`，每个租户使用独立凭据；不能把同一个 key 重复填写当成多个租户 |
| 记忆引擎 | 从被测 commit 的 `configs/config.example.json` 生成配置，保留真实引擎；不能拿空引擎部署模板直接测召回 |
| 实际运行版本 | 更新源码后重新构建专用容器，核对挂载配置、镜像与源码 commit；只执行 git pull 不代表运行中的服务已更新 |

如果本机失败而别人可以运行，先比较以上项目以及模型、并发设置、实际错误日志。
不能仅根据 `deployment.mode = "local"` 就认定本机不支持压测。
预检通过只代表可以开始测，不代表六项指标已经通过。

### 本机能启动，但压测不能运行时

**本机部署不等于 `auth.mode=local`。** `deployment.mode` 选择存储/运行后端，
`auth.mode` 决定请求如何识别租户。本文保留前者为 `local`，将后者设为
`x_auth_key`；只改其中一个并不能完成多租户配置。

| 现象 | 先核对什么 | 不能直接得出的结论 |
| --- | --- | --- |
| 不同人的本机结果不同 | 两个仓库的 commit、最终加载配置、启动命令和镜像 | 不能只凭都叫 develop 就认为版本一致 |
| 租户请求认证失败 | `auth.mode`、独立租户注册结果、请求的 `X-Auth-Key` | 不是 local 后端一定不支持多租户 |
| Search 返回 200 但没有记忆 | 真实引擎是否启用、模型错误、Commit 终态、预注入事实是否能召回 | 不是接口 200 就说明召回链路可用 |
| 故障注入或租户观测不可用 | 下文 PR449 能力检查、测试控制开关和控制 token | 不代表全部六项都无法运行，也不能把缺失项判为通过 |

请先完成下文的版本、配置和 readiness 检查，再开始发压。不要为解决启动错误
盲目切换云模式、删掉引擎或替换成 mock；应保留错误日志并定位具体配置或代码问题。

## 交给任意 AI 助手的完整任务

这份手册不依赖 Codex。让 AI 在测试平台仓库中工作，并把下面一段直接发给它；替换两个
绝对路径，不要把密钥写进提示词：

```text
EchoMem 仓库：<EchoMem 绝对路径>
测试平台仓库：<Memory-System-Eval-Harness 绝对路径>

先完整阅读：
1. <测试平台仓库>/performance/targets/echomem/README.md
2. <测试平台仓库>/performance/skills/echomem-stress/SKILL.md
3. <测试平台仓库>/performance/skills/echomem-stress/references/interactive-workflow.md

将这三份文件作为本次 EchoMem 压测的执行规范。即使当前 AI 没有 Skill 安装机制，
也必须按照其中的 Discover、Configure、Preview、Validate、Execute、Explain 流程执行。
先核对两个仓库的 branch、commit 和 dirty state，不要静默 fetch、switch、reset。
测试平台目标分支为 performance_refactor；EchoMem 从更新并锁定的 develop 开始。
本次 M1-M3 使用锁定的 develop，不以 PR449 控制接口是否存在作为启动条件。
使用 deployment.mode=local、auth.mode=x_auth_key 和独立租户凭据；
基于被测 commit 的完整 config.example.json 配置真实引擎，不使用空引擎模板。
更新源码后重新构建本次专用容器，并记录实际运行镜像与配置指纹。
使用真实 LLM，Embedding 强制使用 qwen3.7-text-embedding-flash，显式传入 --metrics M1,M2,M3。
EchoMem 的 model.embedding.model 和 profile 的 required_embedding_model 均设为
qwen3.7-text-embedding-flash；真实预检失败或模型不匹配时停止，不允许回退到其他模型。
默认容量档位为 1、2、4、8、16、32，创建 32 个独立租户凭据。需要继续寻找更高
边界时，由使用者在 profile 中追加 64、128 等档位并补足独立租户凭据。
先跑默认配置基线，再跑并发调优配置；两个结果目录和配置指纹必须分开。
测试平台不得根据 EchoMem 的 worker、queue、provider budget 或 max concurrency 自动降载。
保留超时、拒绝、Provider 异常、pending、空召回和质量失败的原始分母。
本次只运行 M1-M3；后续明确选择完整六项时再检查控制接口及故障/重启授权。
完成后打开 report.html，逐项解释数据、分母、错误归属和 EchoMem 模块改进建议。
报告顶部必须列出实际预检的 LLM、Embedding、Endpoint、真实请求状态和配置指纹。
预检成功只证明模型可用；只有同时采到压测期间的模型阶段日志或 Provider 指标，才可
说明负载调用了模型。`mock=false`、HTTP 200 或配置中写了模型名都不能作为调用证据。
```

AI 必须先展示 readiness 和实际命令，再开始会消耗模型额度或重启容器的步骤。若它不能
读取文件、执行 Shell、访问 Docker 或持续跟踪长任务，就不能声称已经完成压测。若只想
先验证链路，使用第 6 节显式指定 M1-M3 的 quick 命令；quick 的结果只能标记为 `PARTIAL`。

> 完整测试会对专用 EchoMem 容器注入租户故障，并在 M5 中执行真实 `kill -9` 和重启。
> 请勿指向日常开发、共享或生产容器。

## 可选：加载交互式 Skill

仓库自带的 `performance/skills/echomem-stress/` 是可移植的 `SKILL.md` 目录。支持项目
Skill、自定义 Agent 指令或上下文文件的 AI，可以按自身产品的方式加载整个目录；不支持
Skill 的 AI 直接阅读上一节列出的三个文件即可，测试命令和结果完全相同。

Codex 用户可选择安装到个人 Skill 目录：

```bash
mkdir -p ~/.codex/skills/echomem-stress
cp -R performance/skills/echomem-stress/. ~/.codex/skills/echomem-stress/
```

其他 AI 不需要执行这段安装命令。无论使用哪种 AI，执行规范都要求先展示 readiness；在
新机器上先跑 quick，链路通过后再选择 M1-M3、完整 M1-M6、单项、续跑或仅重建报告。
M4 故障注入、M5 容器重启以及远程/共享资源操作仍需获得操作者明确授权。

## 测试内容

### 磁盘空间与异常续跑

完整运行除记忆数据外，还会持续保存结构化日志、每次请求记录、Prometheus
采样、租户观测快照及 HTML 汇总。启动前检查结果目录和 Docker 虚拟机所在磁盘：

```bash
df -h .
docker system df
```

建议至少预留 5 GiB 可用空间，并随运行监控增长；这是本机执行的预留建议，
不是完整运行的空间上限，也不是平台已经自动强制执行的检查。
不要在空间接近耗尽时启动长运行或同时构建镜像。`No space left on device`
属于测试环境故障，不是 EchoMem 性能边界，也不能据此判定 OOM。

遇到异常先确认进程退出状态，再保留原目录并检查 JSON 是否完整。HTML 中的
旧进度不代表进程仍在运行；输出文件存在也不代表六项已经完成。
清理时优先处理确认可重建的重复汇总或无损归档已结束运行的日志，不删除
唯一原始证据，不执行无范围的 Docker / 用户目录清理。

只有 EchoMem 镜像、模型、有效配置、租户和请求计划未改变，才考虑 `--resume`。
当前入口可复用已有 M1 报告和完成的 M2/M3 场景，故障/恢复探针仍可能重跑。
续跑前核对原任务终态、残留故障已撤销以及磁盘空间；不要直接重复完整命令。
若升级镜像或改变并发参数，应新建输出目录，分别标注版本和测试窗口，
不能把跨版本证据合并成“同一配置的一次完整验收”。

接口契约探针还必须使用产生该 Session 的租户凭据。当前结果 CSV 的字段是
`tenant_idx`，旧结果可能使用 `tenant`；不能因字段未识别而默认使用第一个
租户的 key。Session 接口返回 400、Cursor 返回 404 时，应先核对身份与资源，
不能仅凭这些状态码推断 EchoMem 未实现接口。

### 六项指标概览

| 指标 | 测试动作 | 主要输出 |
| --- | --- | --- |
| M1 单实例容量与 DAU | 预注入真实记忆，按租户数和租户内热用户数逐档增加 Search、Commit 与混合负载，直到出现持续阻塞、失败、崩溃或积压不能恢复 | 每档 P95、吞吐、错误类型、质量分母、CPU、内存、最后正常档、首个拥塞档、三种业务画像 DAU 换算 |
| M2 多租户公平性 | 4 租户、8 租户使用独立凭证，以同档位请求并发 Search 和独立 Session Commit | 每租户 Commit 吞吐、Search P95、Commit Jain 指数、Search Jain 指数和零完成租户 |
| M3 Commit 洪泛优先级 | 先测热记忆 Search 基线，再运行均匀洪泛和单租户洪泛；仅统计与未完成 Commit 确认重叠的 Search | 基线/洪泛 Search P95、错误、召回质量、Commit 计划/202/拒绝/完成/未终态数量 |
| M4 单租户故障隔离 | 依次给一个租户注入 `delay` 和 `reject`，其余租户持续执行真实记忆 Search | 旁观租户故障前/中/后的 Search P95、错误率和劣化百分比 |
| M5 202 Commit 崩溃恢复 | Commit 返回 202 且尚未完成时 kill 容器，重启后只轮询原任务并重复提交幂等键 | 恢复终态，以及 history、archive、cursor 的消息集合、顺序、丢失和重复对账 |
| M6 分层分租户可观测性 | 全程采集受保护观测接口，并覆盖 NORMAL、QUEUE、REJECT、RESET | 每个 `tenant × lane` 的 queued、wait、exec、rejected 四元组，缺失帧、非法值和重启代际 |

测试是**观测型**的，不内置“P95 必须小于多少”之类性能门槛。`MEASURED` 表示规定的
数据分母已采集完整，不等于性能优秀；失败、超时、HTTP 200 但召回质量失败、Provider
异常和长期 pending Commit 都会原样进入报告。

### M1 的人数、QPS 与耗时口径

本手册在 profile 中显式选择 `semantic_seed_kind: synthetic`：M2/M3 的共享 seed
和 M4 复用查询使用 5 段自然语言笔记、20 项日期/时间/地点/联系人事实、40 道查询，
种子阶段校验其中 4 道。M1 使用同类合成事实语料，正式默认校验 40 道。
所有记忆仍须经过真实 Commit 抽取与 Search 召回，合成语料不等于 mock 模型。

为了兼容历史任务，未设置该字段时仍使用 `locomo-single-session`：
LoCoMo conv-30 的 session_1，不是完整 81 题准确率评测。该旧契约还要求记忆保留
随机证据标识，真实模型可能在提炼事实时丢弃标识，造成种子校验失败；不能因此
断言事实丢失，也不能跳过校验启动性能测试。
最终以 suite.json 中的 seed_source、corpus_source、corpus_counts_by_tenant_index
和种子校验结果为准，不能混合两种语料或沿用另一种语料的缓存。

M1 每个档位依次测 Search、Commit、混合与热点四种负载：

| 负载 | 发压方式 |
| --- | --- |
| Search | 每个用户按 profile 的 Search RPS 发出纯记忆召回问题 |
| Commit | 每个用户默认每 30 秒追加内容并提交，独立轮询终态 |
| 混合 | 同时进行 Search 与 Commit，查询计划约 70% 为记忆召回 |
| 热点 | 在混合负载上，将首个用户的 Search RPS 放大到 8 倍 |

因此，1 个用户、基准 1 QPS 在热点场景会产生约 8 Search QPS，而不是 1 QPS。
必须同时看 load_mode、planned/sent、窗口长度和 peak_inflight_requests，
不能把一个热点场景的拥塞直接写成“服务最多支持 1 个用户”。

默认完整 M1 有两个拓扑：跨租户递增，以及固定 4 租户、每租户用户数递增。
1/2/4/8/16/32 全档位若均运行，每档四种负载、每种 300 秒测量加 30 秒预热，
仅这些窗口合计约 4.4 小时；实际还包括种子注入、召回校验、排空和恢复观察。
观察到持续拥塞或故障时会提前停止后续档位，所以这不是固定运行时长。
其余 M2-M6 另需时间，默认组与调优组也分别执行；不要承诺完整两组一小时完成。
这些时间不是 soak，手册没有启用长稳态 soak。

还要预留种子阶段：当前实现先按最大档位准备身份，再开始该拓扑的阶梯发压，
不是到某一档才追加记忆。默认跨租户 32 个身份，租户内为 4×32 个用户，
每个身份 40 道校验，共计划 6,400 次初始召回，外加每个身份的记忆 Commit。
种子最多 4 个身份并行。若每次召回约 5 秒，仅召回的理想估算已约 2.2 小时，
还未计入抽取、长尾和重试；实际以每个身份的 elapsed_s 与查询记录为准。
容量早期停止不会退还已经完成的最大档位种子准备时间；quick 只适合链路检查，
不能把小样本结果标成完整容量报告。执行前应明确接受此规模和模型额度开销。

每次运行还会生成两组横向证据：关键接口调用账本按 Search、Open、Add、Commit、
Commit 状态、History、Archive、Cursor、Metrics、故障控制和租户观测分别统计实际
调用次数与错误；负向契约探针独立检查缺认证、畸形 JSON、缺必填字段、错误字段类型、
不存在资源及非法故障类型，不把这些请求混入性能分母。接口和模块耗时分开显示：
客户端记录 HTTP 端到端耗时；测试平台按 `trace_id` 关联 EchoMem 的结构化 Recall、
Engine、Rerank、Commit 和 Atomic Pipeline 日志，逐阶段统计 observations、P50、P95、
P99 与 queue wait。七组 Prometheus Histogram 使用测试窗口内累计值增量独立汇总，并与
日志覆盖交叉校验。不得通过端到端耗时相减推算模块耗时；只有日志和指标均无真实样本时，
才将对应阶段标记为“不可观测”并列明原因。

以下是可选扩展配置，不属于 M1-M3 启动前提。**PR34（36ee91e）不包含这两个探针及
编排逻辑，不能直接启用下面配置并期待生成结果。** 使用包含 PR33 实现的版本时，先核对
`performance/targets/echomem/probes/` 中存在 `concurrency_topology.py` 和
`payload_boundary.py`，且编排器确实调用它们；最终仍需检查报告中的实际请求分母。
普通 M1-M3 可以省略这两个配置段；交付“16/64并发四类拓扑与0至1MiB边界”时必须启用，
它们会作为 M1-M3 报告的补充探针执行。不能用 M1-M3 三张通过卡片代替扩展场景完成。

```json
{
  "concurrency_topology": {
    "enabled": true,
    "levels": [16, 64],
    "max_concurrency": 64,
    "stop_after_boundary": false,
    "requests_per_level": 128,
    "sessions_per_user": 2,
    "within_session_concurrency": 4
  },
  "payload_boundary": {
    "enabled": true,
    "sizes_bytes": [0, 1, 1024, 65536, 262144, 524288, 1048576],
    "commit_content_chars": 1048576,
    "commit_chunk_chars": 262144,
    "mcp_base_url": "http://127.0.0.1:8001",
    "mcp_add_memory_tool": "add_memory",
    "mcp_add_memory_chars": 1048576
  }
}
```

`concurrency_topology` 依次测四种真实 HTTP 拓扑：多用户单 Session 串行、
多用户多 Session 串行、多用户单 Session 内并发，以及不同租户的短 Search 与长
Message+Commit 异构负载。档位表示目标总在途并发，不等于用户数；报告分别写出目标并发、
实际/所需用户数、Session 数、每 Session 并发、P50/P95/P99、2xx 吞吐、错误类型和 Jain。
独立租户凭据不足时不复用同一 Key 冒充多租户，该档明确标为 `INCONCLUSIVE`。

`payload_boundary` 对 Message、Commit、Search 发出 0 到 1 MiB 的文本和二进制请求，
同时把 1 MiB 文本分块写入 Session 后提交真实 Commit 并轮询终态。配置 MCP 地址时，
它还会执行真正的 Streamable HTTP `add_memory`；未配置时报告 `BLOCKED`，不会用 HTTP
Message 假冒 MCP 调用。HTML 表格展示内容字节、实际 wire bytes、HTTP/传输结果、稳定
reason code 与耗时，原始 JSON 保留完整分母。

16/64完整对照需要至少64个独立租户凭据，不能把32个租户重复使用成64个。上述标准探针中，
第一类分别为16/64用户，每用户1会话、会话内1并发；第二类默认8/32用户，每用户2会话、
会话内1并发；第三类默认4/16用户，每用户1会话、会话内4并发。第四类使用4用户，
部分用户发Search、部分用户发长Message后Commit。以上是标准探针的布局；独立短测脚本
可能固定4个用户再增加会话数，不能将两种布局混称为同一组实验，必须以报告实际用户/会话列为准。

边界测试分母为7个长度档乘6种API/编码组合，共42项；0至1MiB表示这些离散档位，
不是逐字节穷举。JSON文本字段长度与整个HTTP请求体长度分别记录；Commit是控制接口，
其任意文本/二进制请求仅测试输入处理，真正的长Commit是先分块写入再提交并轮询。
HTTP400/413/415/422记录为输入拒绝，HTTP5xx为服务错误，401/403/404/405为环境或接口阻塞，
429为限流，不得把“收到响应”当作通过。HTTP202只代表受理；只有成功的状态查询返回
completed等终态且全部预期字符已受理，才能记为长Commit完成。MCP非空工具返回只能证明
工具有响应，不单独证明1MiB全文无截断落盘；需另核对history/archive，缺少时必须说明证据范围。

慢请求诊断先使用每条HTTP的请求标识关联内部阶段，超时请求仍保留发送时的标识。
采集器 `scripts.collect_commit_diagnostics --scene-root <场景样本目录>` 按 `*-samples.json`
分组聚合全日志流，再由 `scripts.build_quick_topology_report --root <结果目录>` 展示
16/64阶段图与最慢20个请求明细；它是拓扑诊断报告，不替代正式M1-M3报告。
语义路由、画像匹配是阶段墙钟耗时，不等于模型生成时间；阶段P95不能相加，
若只能定位到阶段，应继续采集该阶段内部矩阵计算、评分与等待计时，而不是直接修改限流参数。

M3 同时包含均匀 Commit 洪泛和单租户洪泛。后者让一个租户承担全部 Commit，四个租户
继续独立 Search，用于观察不同租户负载与耗时是否串扰；它是异构/吵闹邻居场景，不能
拿来计算 M2 的等权 Jain 公平性。当前 EchoMem 故障控制作用于目标租户全部认证请求，
若要分别制造“仅 Search 慢”或“仅 Commit 慢”，服务端还需提供按 operation 选择的故障范围。

### Commit 状态轮询也产生请求

M2/M3 当前的 `poll_commit` 默认轮询间隔为 0.2 秒，每个未终态任务独立轮询。
这不是每租户 0.2 秒只请求一次；任务越多，状态查询流量越大。
例如 64 个任务同时未完成时，忽略请求本身耗时的理论轮询频率上限约为 320 次/秒，
实际值以接口调用账本和 `poll_count/poll_http_errors` 为准。

因此，Search 的计划 QPS 不等于服务收到的总 QPS。解释租户限流和排队时，要同时列出
Search、Commit 提交、状态查询的次数与错误，不能把轮询触发的 429 直接归因于模型、
原子引擎或硬件容量。改变轮询节奏属于测试负载变化，应单独记录，不与原组直接混算。

### 场景之间的 Commit 排空

M2/M3 每个场景的发压窗口结束后，平台继续轮询本场返回 202 的原 Commit，
默认最多观察 300 秒（`post_case_drain_timeout_s`）。结果写入该场景的
`post-case-drain.json` 和 `summary.json`，不再次提交、不修改原始请求记录，
也不把排空期间的完成数计入窗口内吞吐或 Jain 公平指数。

只有确认全部受理任务达到终态后才进入下一场；`failed` 也是终态，
“已排空”不代表 Commit 成功。如果仍有未确认终态的任务，平台保留当前场景数据，
停止后续负载和故障探针，记录 `previous-case-commit-backlog-unresolved`。
状态查询超时或 429 不能视为任务已完成。此时不能用下一场的 Search 作为干净基线。
续跑不会自动忽略已记录的排空阻塞；应先确认原任务终态、查明原因，再新建结果目录复测。

### M4 发压端与样本口径

设置 `phase_duration_s > 0` 时，故障前、故障中、恢复后三个窗口分别按固定到达率发压：
每个旁观租户请求数为 `ceil(phase_duration_s × search_rps_per_tenant)`，目标租户使用
`target_rps`；此时不是由 `samples` 控制请求总数。例如 quick 默认每阶段 15 秒、
旁观租户 2 QPS，每个旁观租户每阶段计划 30 次，而不是 10 次。
`samples` 仅在不配置持续时间时控制采样次数，正式证据另检查样本量是否完整。

各租户使用独立发压线程池。定时发压时，线程池至少覆盖“到达率 × HTTP 超时”
对应的同时在途请求，且不超过本阶段计划请求数，避免测试端自身排队造成隐性降载。
报告保留实际 `generator_workers`、计划/实际发出次数和 `max_generator_lag_s`；
这不是调高 EchoMem 内部并发，也不保证操作系统不会产生发压延迟。

故障必须覆盖整个故障中采样窗口，完成后显式关闭并检查恢复。
如果发压延迟过大或故障 TTL 提前结束，保留数据但不能认定隔离测试有效；
不能仅凭旁观租户 P95 没变差就判定通过。

## 1. 准备环境

先执行 `docker compose version` 和 `docker info`。二者均成功后再执行
`manage.sh`；只有 `docker-compose` 命令可用不代表 `docker compose` 插件可用。
Homebrew 安装时应将实际的 CLI 插件目录配置到 Docker 客户端
`cliPluginsExtraDirs`。使用独立 `DOCKER_CONFIG` 时还需选择正确 context，
或显式指定由 `docker context inspect` 查到的 `DOCKER_HOST`；
不要默认使用 `/var/run/docker.sock`，Colima 的 socket 通常位于用户目录。

本机需要 macOS 或 Linux、Docker Compose、Git、jq、Python 3.11+，以及可用的真实 LLM 和
Embedding 凭证。禁止使用 mock。仅完整六项要求故障控制和租户观测接口。

获取两个仓库。`ECHOMEM_DIR` 可以换成自己的绝对路径。EchoMem 必须显式检出
`develop`；不要依赖 `git clone` 当时的默认分支，因为默认分支可能是 `main`：

```bash
git clone --branch develop https://github.com/tech-innovation-group/EchoMem.git
cd EchoMem
git fetch origin develop
git switch develop
git pull --ff-only origin develop
export ECHOMEM_DIR="$PWD"
printf 'EchoMem branch=%s commit=%s\n' \
  "$(git branch --show-current)" "$(git rev-parse HEAD)"
test "$(git branch --show-current)" = develop
cd ..

git clone https://github.com/tech-innovation-group/Memory-System-Eval-Harness.git
cd Memory-System-Eval-Harness
git fetch origin performance_refactor
git switch performance_refactor
git pull --ff-only origin performance_refactor
git rev-parse HEAD
```

PR32 仅保留为历史评审记录。测试归档时保留最后一条命令输出的完整 commit。

### EchoMem 代码要求与 PR449

M1、M2、M3 和 M5 可使用提供标准 Session、Commit、Search、History、Archive 与 Cursor
接口的 EchoMem 版本。完整 M1-M6 还要求 EchoMem 包含 PR449 的黑盒测试接口：

```text
GET/POST /api/inspect/test-control/fault
GET      /api/inspect/tenant-observability
```

只运行 M1-M3 时使用上一步锁定的最新 `develop`。运行完整 M1-M6 时，在 PR449 合入
`develop` 前必须显式检出 PR449，并验证它已同步当前 `origin/develop`：

```bash
cd "$ECHOMEM_DIR"
git fetch origin develop pull/449/head:pr449-blackbox
git switch pr449-blackbox
git merge-base --is-ancestor origin/develop HEAD || {
  echo 'BLOCKED: PR449 尚未同步当前 origin/develop，请使用已同步分支后再测完整 M1-M6。'
  exit 1
}
printf 'EchoMem branch=%s commit=%s develop=%s\n' \
  "$(git branch --show-current)" "$(git rev-parse HEAD)" "$(git rev-parse origin/develop)"
git rev-parse HEAD
```

若上面的祖先校验失败，停止测试；不要让 AI 静默把旧 PR449 历史强行 rebase 或
cherry-pick。PR449 合入后，完整 M1-M6 也直接使用最新 `develop`。无论选择哪个版本，
都必须把最终 EchoMem branch、commit 和 `origin/develop` commit 写入报告。

## 2. 本机部署 EchoMem

### 单机部署不等于单租户认证

`deployment.mode` 和 `auth.mode` 是两个不同的配置，不能混为一谈：

| 配置 | 本手册要求 | 含义 |
| --- | --- | --- |
| EchoMem 代码 | 显式更新并记录 `develop`；完整六项按上一节检查 PR449 接口 | 不使用 clone 默认分支代替版本确认 |
| `deployment.mode` | `local`，各 overrides 保持当前版本默认值 | 使用单机后端；不意味着不能多租户压测，也不要求为了压测启动 MySQL |
| `auth.mode` | `x_auth_key` | 每个业务请求通过各租户自己的凭据解析身份 |
| `engine.enabled` | 非空，使用源码完整示例中的真实引擎 | 防止只启动空引擎服务 |

在复制完整示例后，修改其中的认证字段：

```json
{
  "auth": {
    "mode": "x_auth_key"
  }
}
```

这只是字段示例，不要覆盖整个配置或删除其他 auth 字段。源码示例可能默认
`auth.mode=local`；不能因为已经复制了完整示例就跳过认证检查。
业务请求使用各租户独立的 `X-Auth-Key`；PR449 受保护控制面使用
`X-EchoMem-Test-Token`，两者不能互相替代，也不能把同一个业务 key 重复当作多个租户。

启动前检查实际要挂载的配置，不输出任何密钥：

```bash
jq -e '
  .deployment.mode == "local" and
  .auth.mode == "x_auth_key" and
  (.engine.enabled | length > 0)
' config.json >/dev/null || {
  echo 'BLOCKED: 请检查单机部署模式、多租户认证模式和真实引擎配置。'
  exit 1
}
```

这段检查应在下方 init、复制配置、编辑配置之后执行。修改后重建或重新创建 Core，
确认容器挂载的是本次配置；仅修改宿主机文件不能证明运行进程已经加载新配置。

### 种子失败先排查，不直接进入压测

正式负载前，每个独立租户都必须走通 Open Session → Add → Commit → 轮询终态 →
Search 命中预期事实。202 只表示接受，completed 也不能代替实际召回验证。

若出现 `seed commit failed ... status=failed error=`，即使 error 为空也必须保留失败：

1. 核对运行容器的代码 commit、配置挂载、端口和认证模式，避免请求打到旧实例。
2. 用创建 Session 时的同一租户凭据调用后续接口；404/not_found 先检查路径、
   Session ID 与租户归属，不直接推断为单机模式不支持。
3. 保存原 Commit 状态响应、Session/Archive 标识及对应时间窗口的 Core 日志，
   检查真实引擎、模型错误、存储错误；分享前脱敏凭据与用户内容。
4. 分别预检 LLM、Embedding，以及启用的 Rerank；单次 API 成功不代表 Commit
   全链路正常，更不代表并发时没有限流。
5. 没有足够错误证据时记录“原因待确认”，停止依赖种子的性能场景，不把失败隐藏或
   改成通过，也不要仅为绕过错误切换 cluster_shared/MySQL。

使用 EchoMem 仓库自带的单节点 Compose。本机测试需要显式核对资源限制：
新版本 Compose 可能默认设置 Core 为 3.5 CPU、6GB，不能把默认启动描述成无上限。

```bash
cd "$ECHOMEM_DIR/deploy/single-node"
./manage.sh init
cp ../../configs/config.example.json ./config.json
```

在专用测试的 `.env` 中设置 `ECHOMEM_CORE_CPUS=0` 和
`ECHOMEM_CORE_MEM_LIMIT=0`，并在启动后检查 Docker 的 `HostConfig.NanoCpus`、
`CpuQuota`、`Memory`；只有实际限制均为零才可报告容器未设置上限。
Docker Desktop/Colima 虚拟机仍有自己的 CPU/内存边界，也必须记录。
如果本机已经存在 EchoMem，给本轮设置独立的 `COMPOSE_PROJECT_NAME`、
`ECHOMEM_CORE_IMAGE` 和空闲的 Core/Plugin/Web 端口，并把 profile 的
`base_url`、`resource_container` 改为本轮实例，避免 M5 重启其他实例。

这里必须复制仓库根目录的完整 `configs/config.example.json`，不能使用
`deploy/single-node/config.json.example`；后者允许 `engine.enabled=[]`，只能启动空引擎服务，
不能完成真实记忆 Commit/Search 压测。启动前执行硬校验：

```bash
test "$(jq '.engine.enabled | length' config.json)" -gt 0 || {
  echo 'BLOCKED: engine.enabled 为空，未启用任何真实记忆引擎。'
  exit 1
}
git -C "$ECHOMEM_DIR" branch --show-current
git -C "$ECHOMEM_DIR" rev-parse HEAD
```

编辑当前目录的 `.env` 和 `config.json`：

1. 以被测 EchoMem 代码中的 `configs/config.example.json` 为准，不使用测试平台模板；
2. 保留配置中的 `api_key_env`，把真实密钥只填入 `.env`；
3. 确认 `engine.enabled` 包含本次要测的真实记忆引擎；
4. LLM 与 Embedding 都必须可用，Search 返回 HTTP 200 不能替代模型预检；
5. 仅完整六项需要设置随机测试控制 token 并启用控制面；M1-M3 跳过此项。

Compose 的 `.env` 主要用于变量替换，不会自动把所有变量传入 Core。检查
`compose.yaml` 的 `core.environment` 或 `env_file` 是否包含配置中每个 `api_key_env`。
尤其注意独立的 Intent、Rerank 和各记忆引擎密钥；只填通用 LLM key 不保证全部路径可用。
测试平台的 `test.env` 也必须包含同一组被配置引用的模型变量。不要打印变量值，
不要假设 Rerank、Embedding 与 LLM 可以共用凭据，应分别验证实际 Provider。

Embedding 必须使用以下模型和 Endpoint。维度必须与被测版本的索引配置及实际
Provider 返回维度一致；下列配置使用 1024 维：

```json
{
  "model": {
    "embedding": {
      "provider": "openai_compatible",
      "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key_env": "ECHOMEM_EMBEDDING_API_KEY",
      "model": "qwen3.7-text-embedding-flash",
      "dimensions": 1024
    }
  }
}
```

这里是字段核对示例，不要用这段不完整 JSON 覆盖整个 `config.json`。必须从当前 EchoMem
代码自己的 `config.example.json` 开始，只修改对应值。真实 key 只写 `.env`。

在 EchoMem 的 `deploy/single-node` 目录中检查配置：

```bash
jq -e '.model.embedding.model == "qwen3.7-text-embedding-flash"' config.json >/dev/null || {
  echo 'BLOCKED: Embedding 必须使用 qwen3.7-text-embedding-flash。'
  exit 1
}
```

这只检查配置值，启动后的真实模型预检仍必须通过。更换模型后使用新的测试记忆与
匹配模型、Endpoint、维度的缓存，不能沿用其他模型生成的向量，也不能把旧结果续跑为本轮结果。

仅运行完整六项时，EchoMem Core 进程需要接收下面两个环境变量：

```dotenv
ECHOMEM_TEST_CONTROL_ENABLED=true
ECHOMEM_TEST_CONTROL_TOKEN=<本机随机长字符串>
```

同时确认 `deploy/single-node/compose.yaml` 的 `core.environment` 已透传这两个变量。被测版本
还必须提供：

```text
GET/POST /api/inspect/test-control/fault
GET      /api/inspect/tenant-observability
```

缺少这些接口时，M4 和 M6 会明确报告 `BLOCKED`，不能用客户端模拟结果替代。

若路由日志持续出现 `recall_llm_failed / invalid_output`，检查模型是否把推理
token 计入输出预算。曾实测分类只允许 1 个 token 时，模型返回
`finish_reason=length`、content 为空，即使 Search 通过保守降级召回事实，
也不是完整链路通过。应修复被测代码的路由预算兼容性并重新验证，不能关闭质量
检查或把普通模型连通性预检当作路由成功。

启动并检查：

首次更换 Embedding 模型时，路由静态示例可能需要重新生成数万条向量，
这是服务启动阶段，不计入 Search 压测时延。查看初始化日志中的
`required/hits/missing`，在 ready 成功前不要开始发压。
可以复用相同模型、endpoint 和维度指纹的路由静态缓存，但须确认当前版本接受
其 JSON 格式并实际命中；例如额外的 `schema_version` 字段可能使旧解析器拒绝缓存。
不得改写指纹或伪造向量。租户测试记忆仍应独立注入，并验证真实召回。

```bash
cd "$ECHOMEM_DIR/deploy/single-node"
./manage.sh up
./manage.sh status
./manage.sh smoke
curl -fsS http://127.0.0.1:8010/api/v1/system/ready
curl -fsS http://127.0.0.1:8010/metrics >/dev/null
```

如果只测本手册的 Core HTTP 接口、不需要 Web/Plugin，可在已完成 init、
确认所有 deployment 组件使用单机后端后，仅构建并启动 Core：

```bash
docker compose build core
docker compose up -d --no-build --no-deps core
curl -fsS http://127.0.0.1:8010/api/v1/system/ready
```

`--no-deps` 避免启动 Compose 中声明的其他依赖；不能用于需要 MySQL 等
外部后端的配置。非默认端口应同步替换上述 URL。不要把初始化中的连接拒绝
当成最终失败，检查容器状态与启动日志，等待 ready 后再测试。

取得 Core 容器名，后面填入 profile：

```bash
docker inspect --format '{{.Name}}' "$(docker compose ps -q core)"
```

输出通常类似 `/echomem-core-1`；profile 使用去掉开头 `/` 后的 `echomem-core-1`。

### 2.1 默认基线与并发调优必须分开

不要只跑一份改过的配置。容量测试至少保留两组：

1. **默认基线**：只替换 endpoint、模型名和 key，保留被测版本所有调度默认值。它回答
   “用户按默认配置部署后能承受多少”。
2. **并发调优组**：提高会提前截断请求的本地并发和队列旋钮。它回答“解除保守配置后，
   当前机器、EchoMem 和 Provider 组合能承受多少”。

当前 4U8G `small` 默认通常包含 `model.max_concurrent=4`、Retrieval admission=8、
`llm_max_concurrent=4`、`embed_max_concurrent=4`、Recall LLM/Embedding=1/2，以及较小的
Recall 队列。这些值可能在默认 32 客户端并发前先形成排队，不能把该现象描述成硬件极限。

若团队已经测过 `qwen3.7-text-embedding-flash` 的 8/16/32/64 Provider 并发，可在运行
备注中引用该证据并跳过重复阶梯；仍需做一次真实鉴权、模型名、返回维度和单条向量检查。
没有既有证据的机器不得假定 Provider 支持 64 并发。

下面是 4U8G 的**起始调优方向**，不是所有机器通用的最终值：

```json
{
  "instance": {"profile": "small"},
  "scheduling": {
    "http": {"max_workers": 128},
    "retrieval": {"admission_permits": 32},
    "commit": {
      "executor_workers": 5,
      "gate_workers": 3,
      "queue_max": 320,
      "tenant_quota": 80
    },
    "llm_gateway": {
      "llm_max_concurrent": 6,
      "recall_llm_max_concurrent": 1,
      "episode_llm_max_concurrent": 3,
      "workers_llm_share": 2,
      "provider_budget_llm": 12,
      "embed_max_concurrent": 8,
      "recall_embed_max_concurrent": 16,
      "episode_embed_max_concurrent": 3,
      "workers_embed_share": 2,
      "provider_budget_embed": 29
    },
    "tenant": {"qps": 128, "concurrency": 32}
  },
  "recall": {
    "concurrency": {
      "engine": {"max_concurrent": 32, "queue_capacity": 256, "max_queued_per_tenant": 32},
      "intent_llm": {"max_concurrent": 8, "queue_capacity": 256, "max_queued_per_tenant": 32},
      "query_embedding": {"max_concurrent": 16, "queue_capacity": 256, "max_queued_per_tenant": 32},
      "rerank": {"max_concurrent": 8, "queue_capacity": 256, "max_queued_per_tenant": 32}
    }
  }
}
```

将这些字段合并进完整 `config.json`，不要覆盖其他引擎配置。`provider_budget_llm` 和
`provider_budget_embed` 必须分别不小于所有 LLM/Embedding 消费方份额之和。4U8G 下
`http.max_workers=128` 与 `retrieval.admission_permits=32` 满足 EchoMem 的 4:1 约束；
Commit executor+gate 为 `5+3=8`，不超过 4 核的 2 倍约束。

注意区分两层名称相似的限制：`scheduling.llm_gateway.recall_llm_max_concurrent`
用于原子引擎的 Recall 模型网关；路由意图识别使用
`recall.concurrency.intent_llm.max_concurrent`，还受共享 Provider 预算约束。
当前 EchoMem 的 V4 校验要求前者不大于 `llm_max_concurrent / 4`；
不能只提高前者而不核对对应约束，也不能把它当成整个 Search 的并发上限。

还要检查外层 `recall.max_inflight`：当前验证版本默认 16，且环境变量
`ECHOMEM_RECALL_MAX_INFLIGHT` 优先于 JSON。调优目标为 32 时，应同时核对并设置
这个外层上限，不能只提高 `recall.concurrency.*`；保留默认组原值。
出现 `RETRIEVAL_BUSY` 时结合日志中的实际 in_flight/max_inflight 判断，不把配置拒绝
直接当成硬件容量极限，也不要无条件把上限设为 0 关闭保护。

不要为了展示“32 热租户”把租户常驻缓存硬改成 32。4U8G 的租户缓存有真实内存预算，
启动校验拒绝超出预算的配置也属于有效容量证据。32 个独立凭据表示测试平台会产生
最多 32 租户流量，不代表 32 个租户必须同时常驻；报告要分别展示活动租户、峰值在途请求、
常驻缓存上限、淘汰以及首个持续积压档。

每次改配置后重启专用 Core，并在日志中保存 `instance_profile_resolved` 和
`provider_budget_configured`，确认实际生效值。默认测试平台仍然发送配置的 32 客户端并发，
不会读取这些服务端值后自动减压。

## 3. 安装测试平台

回到测试平台仓库根目录：

```bash
cd /absolute/path/to/Memory-System-Eval-Harness
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p .local-stress
```

## 4. 创建独立测试租户

M2 必须使用不同租户凭证；重复使用同一个 key 只能测到并发，不能证明租户公平。

先核对被测版本的注册鉴权要求。PR34 的 `provision.py` 使用公开租户注册和返回的
bootstrap key，尚未读取 `ECHOMEM_PROVISIONING_AUTH_KEY`；仅 export 此变量无法给
这个版本增加鉴权能力。若注册返回 401/403，保留错误并使用已支持目标版本注册协议的
测试平台，或通过 EchoMem 官方注册流程预先创建独立租户后提供 `tenants.json` 与
`test.env`。不要关闭业务鉴权来绕过错误。

以下输出文件必须尚不存在，程序使用排他创建防止覆盖凭据。重跑注册应使用新的目录；
复用已验证凭据时跳过注册命令，不删除旧文件强行重建。

```bash
.venv/bin/python -m performance.targets.echomem.provision \
  --base-url http://127.0.0.1:8010 \
  --count 32 \
  --out .local-stress/tenants.json \
  --env-file .local-stress/test.env
chmod 600 .local-stress/tenants.json .local-stress/test.env
```

`tenants.json` 只保存 `auth_key_env` 名称，真实租户 key 位于 Git 忽略的 `test.env`。
继续编辑 `test.env`，加入：

```dotenv
ECHOMEM_LLM_API_KEY=<真实 LLM key>
ECHOMEM_EMBEDDING_API_KEY=<真实 Embedding key>
```

若 `config.json` 使用其他 `*_api_key_env` 名称，也要把对应变量加入 `test.env`。测试平台
会在发压前分别验证 LLM 和 Embedding；任何一个失败都会阻止依赖真实记忆的场景。
`ECHOMEM_TEST_CONTROL_TOKEN` 仅完整六项需要，M1-M3 不填写也可运行。

M1-M3 要采集真实内部阶段耗时，EchoMem 配置必须启用 DEBUG JSON 日志：

```json
{
  "runtime": {"log_level": "DEBUG"},
  "logging": {"level": "debug", "format": "json"}
}
```

profile 还要设置准确的 `resource_container` 和 `require_stage_observability: true`。测试平台
只保存白名单阶段字段和哈希后的 trace 引用，不保存原始 trace id 或请求正文。

## 5. 创建唯一的本机 profile

新建 `.local-stress/six-metrics.profile.json`，只放下面这一个 profile。将三处绝对路径和
容器名替换为本机实际值：

```json
{
  "profiles": [
    {
      "name": "Local",
      "semantic_seed_kind": "synthetic",
      "base_url": "http://127.0.0.1:8010",
      "resource_container": "echomem-core-1",
      "require_4u8g": false,
      "tenant_config": "/absolute/path/to/Memory-System-Eval-Harness/.local-stress/tenants.json",
      "preflight_config": "/absolute/path/to/EchoMem/deploy/single-node/config.json",
      "m1_tenant_levels": [1, 2, 4, 8, 16, 32],
      "m1_user_levels": [1, 2, 4, 8, 16, 32],
      "required_concurrency": 32,
      "required_embedding_model": "qwen3.7-text-embedding-flash",
      "require_stage_observability": true,
      "m1_duration_s": 300,
      "m1_search_rps_per_user": 1,
      "dau_scenarios": [
        {"name": "read-heavy", "searches_per_user_day": 50, "commits_per_user_day": 5, "peak_to_average_ratio": 3},
        {"name": "balanced", "searches_per_user_day": 20, "commits_per_user_day": 20, "peak_to_average_ratio": 5},
        {"name": "write-heavy", "searches_per_user_day": 5, "commits_per_user_day": 50, "peak_to_average_ratio": 8}
      ]
    }
  ]
}
```

`require_4u8g: false` 表示不检查固定 4U8G cgroup；Docker 未设置上限时 CPU 和内存字段
可能显示为 `0`，含义是使用宿主机默认资源。为保证数据可比较，报告还会保存容器 ID、
镜像 ID 和 Docker 资源配置。

`required_embedding_model` 是硬性预检条件，固定为 `qwen3.7-text-embedding-flash`。
EchoMem 实际使用其他模型时必须停止，先修改部署配置、重建匹配的向量与缓存并重新预检；
不得修改 profile 来放行其他模型。模型与索引维度必须一致，且必须通过真实调用预检。
`required_concurrency: 32` 表示首轮需要观察到至少 32 个同时在途请求，不等同于仅配置了
32 个用户。测试平台不会读取 EchoMem 的 `max_concurrency`、队列容量或 worker 数后主动
降低负载；这些服务端限制会原样写入报告，用来解释排队、拒绝或容量边界。

首轮得到 32 租户数据后，如需继续寻找 64/128 的边界，先用 provision 命令将 `--count`
提高到目标值，再把 `m1_tenant_levels`、`m1_user_levels` 追加到目标档位，并将
`required_concurrency` 改为 64 或 128。只改档位而没有补足独立租户凭据时，正式运行应
直接失败；这能避免把重复 key 误报成多租户容量。

M3 除等负载场景外还会运行异构租户场景：四个独立租户的 Search 权重为 `8:4:2:1`，
Commit 权重为 `1:2:4:8`。报告逐租户展示计划速率、实际请求数、Search P95/错误/召回质量
与 Commit 完成量，用于验证读多写少、读写均衡、写多读少租户能在同一轮被真实压测。

## 6. 先运行快速链路检查

所有命令均在测试平台仓库根目录执行：

```bash
jq '
  .profiles[0].m1_tenant_levels = [1,2] |
  .profiles[0].m1_user_levels = [1,2]
' .local-stress/six-metrics.profile.json > .local-stress/quick.profile.json
OUTPUT_DIR="results/local-m1-m3-quick-$(date +%Y%m%d-%H%M%S)"
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/quick.profile.json \
  --metrics M1,M2,M3 --quick \
  --out-dir "$OUTPUT_DIR" \
  --env-file .local-stress/test.env
```

profile 文件只有一个 profile 时，脚本会自动选择 `Local`，不需要再写 `--profile`。
本命令使用真实 HTTP、模型和租户，缩短采样时间，只选择 M1-M3，结果按
`PARTIAL` 解读。先确认预检、记忆注入与召回校验成功，再判断能否正式运行。
quick 仍包含真实模型抽取、场景测量与 Commit 排空，并不保证几分钟结束。

注意：quick 不会覆盖 profile 显式指定的容量档位及探针采样配置，因此这里单独
生成小规模 quick profile。正式测试继续使用原始 six-metrics.profile.json，
不能拿 quick 的 1/2 档位、短窗口或单次恢复结果声称完成最大容量或完整六项。

## 7. 正式运行 M1-M3

quick 链路验证通过且前序 Commit 已排空后，使用原始 profile 和新输出目录：

```bash
OUTPUT_DIR="results/local-m1-m3-default-$(date +%Y%m%d-%H%M%S)"
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json \
  --metrics M1,M2,M3 \
  --env-file .local-stress/test.env \
  --out-dir "$OUTPUT_DIR"
```

默认组保留实际调度默认值；调优后新建 `local-m1-m3-tuned-时间戳` 输出目录，
使用同样的显式 `--metrics M1,M2,M3` 命令。不要把两组数据混算。

### 可选：扩展为完整六项

仅在 PR449 能力、控制 token、专用容器故障/重启条件均已满足时执行。
先在 profile 中增加 `fault_isolation.token_env` 与 `tenant_observability.token_env`，
值均为 `ECHOMEM_TEST_CONTROL_TOKEN`，并显式设置
`commit_recovery.allow_container_restart: true`。前三项流程不需要这些配置。

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json \
  --metrics M1,M2,M3,M4,M5,M6 \
  --out-dir results/local-six-metrics-default \
  --env-file .local-stress/test.env
```

默认组结束后，将第 2.1 节的调优字段合并进 EchoMem 完整配置、重启 Core，并使用**新的
结果目录**运行第二组：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json \
  --metrics M1,M2,M3,M4,M5,M6 \
  --out-dir results/local-six-metrics-tuned \
  --env-file .local-stress/test.env
```

两组不得共用输出目录。若主要关注前三项，可把 `full` 命令替换为：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json \
  --metrics M1,M2,M3 \
  --env-file .local-stress/test.env \
  --out-dir results/local-m1-m3-tuned
```

始终显式填写 `--metrics M1,M2,M3`，确保只运行前三项，不依赖不同版本的默认范围。

执行顺序为 `M1 → M2 → M3 → M4 → M5`，M6 从开始到结束持续采样。默认不运行 soak。
机器速度、模型限流和容量边界不同会影响总时长，M1 的逐档容量测试通常最耗时。

只测一项：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json \
  --metrics M1 \
  --env-file .local-stress/test.env \
  --out-dir results/local-m1
```

`--metrics` 可使用 `M1` 到 `M6`，也可传 `M1,M2,M3`。只测 M6：

```bash
performance/targets/echomem/run_six_metrics.sh m6 \
  .local-stress/six-metrics.profile.json \
  results/local-m6 \
  .local-stress/test.env
```

中断后使用原 profile、原输出目录和 `--resume`：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json \
  --metrics M1,M2,M3 \
  --env-file .local-stress/test.env \
  --out-dir "$OUTPUT_DIR" \
  --resume
```

## 8. 查看报告

最终给人阅读的主结果始终是本次 `OUTPUT_DIR/report.html`。例如默认组和调优组分别为：

```text
results/local-six-metrics-default/report.html
results/local-six-metrics-tuned/report.html
```

运行结束后先执行出口检查，避免打开旧报告：

```bash
test -f "$OUTPUT_DIR/report.html" || {
  echo "错误：没有生成 M1-M6 report.html，请使用本文入口重跑"
  exit 2
}
jq -e '.selected_metrics == ["M1", "M2", "M3"]' "$OUTPUT_DIR/summary.json"
jq '{status, selected_metrics, metrics}' "$OUTPUT_DIR/summary.json"
```

不能只交付 HTML；同目录的结构化分母和逐请求证据必须一起保留：

| 文件 | 内容 |
| --- | --- |
| `report.html` | 六项总体结论、图表、测试方式、失败类型与 EchoMem 模块建议 |
| `summary.json` | 报告使用的结构化汇总和每项状态 |
| `suite.json` | 场景、探针与分母明细 |
| `records.csv` | 每个请求的延迟、状态和结果 |
| `metrics_samples.csv` | 测试期间 CPU、内存及 Prometheus 采样 |
| `structured-stage-events.jsonl` | 脱敏后的真实阶段日志样本，不含请求正文 |
| `execution-manifest.json` | 测试平台 commit、profile、模型预检和执行状态 |

| 状态 | 含义 |
| --- | --- |
| `MEASURED` | 该项要求的场景和分母完整，数据可用于分析 |
| `PARTIAL` | 有真实数据，但场景、重复次数或分母不完整 |
| `BLOCKED` | 配置、模型、租户、受保护接口或容器条件未满足 |
| `EXECUTION_ERROR` | 测试平台或执行过程发生异常 |

不要删除失败样本后重算，也不要只保留 HTML。容量结论必须同时给出最后正常档和首个持续
拥塞档；Provider 失败、EchoMem admission 拒绝、原子引擎质量失败和客户端传输错误会按
不同责任域拆开展示。

## 9. 本机清理与恢复

```bash
cd "$ECHOMEM_DIR/deploy/single-node"
./manage.sh status
./manage.sh smoke
./manage.sh down
```

`down` 不会删除 `deploy/single-node/data/workspace`。复测应使用新的结果目录，不要覆盖旧
目录，否则原始分母和版本证据会丢失。

## 常见阻塞

| 现象 | 处理 |
| --- | --- |
| `resource-container` 阻塞 | 核对 profile 容器名、Docker 是否运行以及当前用户是否可访问 Docker |
| `fault_isolation` 或 `tenant_observability` 阻塞 | 核对 EchoMem 接口、启用开关及 Core/runner 两侧 token 是否一致 |
| 模型预检失败 | 同时检查 LLM 与 Embedding endpoint、模型名、余额、限流和 `api_key_env` |
| Search 200 但无召回 | 检查种子 Commit、自然语言查询命中和返回正文中的预期事实 |
| M2 不能证明公平性 | 确认租户凭证各不相同，不能让多个 tenant 复用同一个 key |
| M5 未执行重启 | 只有 Commit 已返回 202 且仍未完成时才会 kill；确认目标是专用本机容器 |
| 容器 CPU/内存显示 0 | 本机模式下表示 Docker 未设 cgroup 上限，测试使用宿主机默认资源 |
