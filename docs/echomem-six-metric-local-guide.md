# EchoMem 六项本机压测

这一个文件包含完整的本机部署、配置、执行和结果解释。测试直接访问本机 EchoMem，
不需要服务器，也不要求把容器限制为 4U8G。报告会记录容器实际 CPU、内存和镜像，
因此不同电脑的容量数据应分别比较。

> **当前代码位置**：本手册发布在默认主分支 `performance_refactor`，六项观测入口当前
> 位于 [PR32](https://github.com/tech-innovation-group/Memory-System-Eval-Harness/pull/32)。
> 必须先按第 1 节检出 PR32，再执行文中的 `observation_run.py` 和
> `run_six_metrics.sh`；PR32 合入后可直接使用主分支。主分支原有的历史
> `python -m performance --target echomem --six-metrics` 不是本手册对应的观测入口。

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
使用真实 LLM 和 qwen3.7-text-embedding-flash Embedding，运行完整 M1-M6。
默认容量档位为 1、2、4、8、16、32，创建 32 个独立租户凭据。需要继续寻找更高
边界时，由使用者在 profile 中追加 64、128 等档位并补足独立租户凭据。
先跑默认配置基线，再跑并发调优配置；两个结果目录和配置指纹必须分开。
测试平台不得根据 EchoMem 的 worker、queue、provider budget 或 max concurrency 自动降载。
保留超时、拒绝、Provider 异常、pending、空召回和质量失败的原始分母。
允许对本次专用容器执行 M4 delay/reject 故障注入和 M5 kill/restart。
完成后打开 report.html，逐项解释数据、分母、错误归属和 EchoMem 模块改进建议。
```

AI 必须先展示 readiness 和实际命令，再开始会消耗模型额度或重启容器的步骤。若它不能
读取文件、执行 Shell、访问 Docker 或持续跟踪长任务，就不能声称已经完成压测。若只想
先验证链路，将“运行完整 M1-M6”改成“运行 quick”；quick 的结果只能标记为 `PARTIAL`。

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

每次运行还会生成两组横向证据：关键接口调用账本按 Search、Open、Add、Commit、
Commit 状态、History、Archive、Cursor、Metrics、故障控制和租户观测分别统计实际
调用次数与错误；负向契约探针独立检查缺认证、畸形 JSON、缺必填字段、错误字段类型、
不存在资源及非法故障类型，不把这些请求混入性能分母。接口和模块耗时分开显示：
客户端记录 HTTP 端到端耗时；测试平台按 `trace_id` 关联 EchoMem 的结构化 Recall、
Engine、Rerank、Commit 和 Atomic Pipeline 日志，逐阶段统计 observations、P50、P95、
P99 与 queue wait。七组 Prometheus Histogram 使用测试窗口内累计值增量独立汇总，并与
日志覆盖交叉校验。不得通过端到端耗时相减推算模块耗时；只有日志和指标均无真实样本时，
才将对应阶段标记为“不可观测”并列明原因。

M3 同时包含均匀 Commit 洪泛和单租户洪泛。后者让一个租户承担全部 Commit，四个租户
继续独立 Search，用于观察不同租户负载与耗时是否串扰；它是异构/吵闹邻居场景，不能
拿来计算 M2 的等权 Jain 公平性。当前 EchoMem 故障控制作用于目标租户全部认证请求，
若要分别制造“仅 Search 慢”或“仅 Commit 慢”，服务端还需提供按 operation 选择的故障范围。

## 1. 准备环境

本机需要 macOS 或 Linux、Docker Compose、Git、Python 3.11+，以及可用的真实 LLM 和
Embedding 凭证。禁止使用 mock。EchoMem 被测版本还需包含故障控制和租户观测接口。

获取两个仓库。`ECHOMEM_DIR` 可以换成自己的绝对路径：

```bash
git clone https://github.com/tech-innovation-group/EchoMem.git
export ECHOMEM_DIR="$PWD/EchoMem"

git clone https://github.com/tech-innovation-group/Memory-System-Eval-Harness.git
cd Memory-System-Eval-Harness
git fetch origin pull/32/head:pr32-six-metrics
git switch pr32-six-metrics
git rev-parse HEAD
```

PR 合入后可直接切换合入后的目标分支。测试归档时保留最后一条命令输出的完整 commit。

### EchoMem 代码要求与 PR449

M1、M2、M3 和 M5 可使用提供标准 Session、Commit、Search、History、Archive 与 Cursor
接口的 EchoMem 版本。完整 M1-M6 还要求 EchoMem 包含 PR449 的黑盒测试接口：

```text
GET/POST /api/inspect/test-control/fault
GET      /api/inspect/tenant-observability
```

在 PR449 合入前，可在 EchoMem 仓库中显式检出该 PR：

```bash
cd "$ECHOMEM_DIR"
git fetch origin pull/449/head:pr449-blackbox
git switch pr449-blackbox
git rev-parse HEAD
```

若要测试“最新 develop + PR449”，必须使用已经把 PR449 独有改动同步到最新 develop 的
分支；不要让 AI 静默把旧 PR449 历史强行 rebase 或 cherry-pick。无论选择哪个版本，运行
前都要用上面的两个路径确认接口存在，并把最终 EchoMem commit 写入报告。

## 2. 本机部署 EchoMem

使用 EchoMem 仓库自带的单节点 Compose，不设置 CPU 或内存上限：

```bash
cd "$ECHOMEM_DIR/deploy/single-node"
./manage.sh init
cp ../../configs/config.example.json ./config.json
```

编辑当前目录的 `.env` 和 `config.json`：

1. 以被测 EchoMem 代码中的 `configs/config.example.json` 为准，不使用测试平台模板；
2. 保留配置中的 `api_key_env`，把真实密钥只填入 `.env`；
3. 确认 `engine.enabled` 包含本次要测的真实记忆引擎；
4. LLM 与 Embedding 都必须可用，Search 返回 HTTP 200 不能替代模型预检；
5. 为压测专用控制面设置随机 token，并启用测试控制。

Embedding 至少确认以下字段；维度必须与被测版本的索引配置一致：

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

EchoMem Core 进程需要接收下面两个环境变量：

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

启动并检查：

```bash
cd "$ECHOMEM_DIR/deploy/single-node"
./manage.sh up
./manage.sh status
./manage.sh smoke
curl -fsS http://127.0.0.1:8010/api/v1/system/ready
curl -fsS http://127.0.0.1:8010/metrics >/dev/null
```

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
ECHOMEM_TEST_CONTROL_TOKEN=<与 EchoMem Core 完全相同的 token>
ECHOMEM_LLM_API_KEY=<真实 LLM key>
ECHOMEM_EMBEDDING_API_KEY=<真实 Embedding key>
```

若 `config.json` 使用其他 `*_api_key_env` 名称，也要把对应变量加入 `test.env`。测试平台
会在发压前分别验证 LLM 和 Embedding；任何一个失败都会阻止依赖真实记忆的场景。

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
      ],
      "fault_isolation": {
        "samples": 100,
        "repeats": 3,
        "token_env": "ECHOMEM_TEST_CONTROL_TOKEN"
      },
      "tenant_observability": {
        "token_env": "ECHOMEM_TEST_CONTROL_TOKEN"
      },
      "commit_recovery": {
        "allow_container_restart": true,
        "samples": 3,
        "messages": 12,
        "content_chars": 1000,
        "recovery_timeout_s": 180
      }
    }
  ]
}
```

`require_4u8g: false` 表示不检查固定 4U8G cgroup；Docker 未设置上限时 CPU 和内存字段
可能显示为 `0`，含义是使用宿主机默认资源。为保证数据可比较，报告还会保存容器 ID、
镜像 ID 和 Docker 资源配置。

`required_embedding_model` 是硬性预检条件。本例只接受真实成功调用
`qwen3.7-text-embedding-flash`；如果服务实际使用其他 Embedding，正式发压前会直接停止。
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
bash -n performance/targets/echomem/run_six_metrics.sh
performance/targets/echomem/run_six_metrics.sh quick \
  .local-stress/six-metrics.profile.json \
  results/local-six-metrics-quick \
  .local-stress/test.env
```

profile 文件只有一个 profile 时，脚本会自动选择 `Local`，不需要再写 `--profile`。
`quick` 使用真实 HTTP、模型、租户、故障和重启，但缩短采样时间，结果固定视为
`PARTIAL`，只用于确认整条链路能跑通。

## 7. 运行完整六项测试

```bash
performance/targets/echomem/run_six_metrics.sh full \
  .local-stress/six-metrics.profile.json \
  results/local-six-metrics-default \
  .local-stress/test.env
```

默认组结束后，将第 2.1 节的调优字段合并进 EchoMem 完整配置、重启 Core，并使用**新的
结果目录**运行第二组：

```bash
performance/targets/echomem/run_six_metrics.sh full \
  .local-stress/six-metrics.profile.json \
  results/local-six-metrics-tuned \
  .local-stress/test.env
```

两组不得共用输出目录。若主要关注前三项，可把 `full` 命令替换为：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json \
  --metrics M1,M2,M3 \
  --env-file .local-stress/test.env \
  --out-dir results/local-m1-m3-tuned
```

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
  --env-file .local-stress/test.env \
  --out-dir results/local-six-metrics-full \
  --resume
```

## 8. 查看报告

最终给人阅读的主结果始终是本次 `OUTPUT_DIR/report.html`。例如默认组和调优组分别为：

```text
results/local-six-metrics-default/report.html
results/local-six-metrics-tuned/report.html
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
