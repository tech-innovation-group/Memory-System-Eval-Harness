# Memory-System-Eval-Harness

记忆系统评测框架。全 CLI，无网页 UI。直接通过 Python 脚本完成数据集加载、
记忆注入、Agent 问答、Judge 评分和结果报告。

**EchoMem 4U8G 六项黑盒观测压测**：使用
`python -m performance.targets.echomem.observation_run`，详见
[随代码维护的本机部署与六项运行手册](performance/targets/echomem/README.md)，默认分支另有
[公开单文件指南](docs/echomem-six-metric-local-guide.md)。文档不绑定
特定 AI，包含通用 Agent 提示词、可选 `SKILL.md`、`performance_refactor`/PR449 代码要求、真实模型配置、
默认 32 租户及显式扩展到 64/128 的方法、默认/调优两组压测、运行命令和最终报告位置。
被测 EchoMem 不得停留在默认 `main`：M1-M3 明确使用最新 `develop`，完整 M1-M6 在
PR449 合入前使用已同步最新 `develop` 的 PR449，并在发压前校验 branch、commit 与非空
`engine.enabled`。
该入口不设置 P95、准确率、Jain、劣化比例或吞吐 PASS/FAIL 门槛；状态仅为
`MEASURED / PARTIAL / BLOCKED / EXECUTION_ERROR`，默认不运行 soak。当前六项运行的
唯一主报告是 `<OUTPUT_DIR>/report.html`。

## 设计目标

### 1. 支撑业界所有 agent 的评测

框架支撑业界所有 agent 的评测：被测 agent 通过统一插件协议接入，同一套评测流程
可在不同 agent 与记忆后端上跑出可比结果，确保结果可复现、可审计。

- **AgentPlugin 协议**：所有被测 agent 实现统一接口（`setup -> inject_memories ->
  create_session -> send_message -> getlog`），评测流程只调用接口，不接触 agent
  特定的 HTTP API。新增 agent 只需创建插件目录，无需改动框架。
- **双记忆后端**：`echomem` 和 `openviking` 两个后端实现同一 `MemoryClient`
  协议，通过 `--memory-backend` 切换，保证同一套评测可以在不同后端上跑出可比
  结果。
- **LLM Judge + provenance**：LoCoMo / LongMemEval 使用 LLM Judge 评分，
  HotpotQA 使用官方 F1/EM 指标。每次运行产出 `summary.json`、`config.json`、
  `memory_provenance.json` 和逐题 `agent_traces/*.json`，记录数据集 SHA-256、
  身份、prompt 来源和工具调用链，确保结果可复现、可审计。

### 2. 支撑内部需求

支撑压测、精度测试、定位算法改进点等内部场景。

- **动态评测**：`generate` 模式由 LLM 生成场景和提问，端到端走 EchoAgent 完整
  管线（含 prefill / TTFT）；`replay` 模式回放数据集对话，测试跨 session 召回。
- **多维质量评分**：动态评测通过 YAML 配置定义 10 个评分维度（任务完成度、
  事实覆盖、信息准确性等，满分 100），由 LLM 逐轮打分并输出诊断。
- **诊断与定位**：LoCoMo 产出 `diagnosis.json`、`retrieval_traces.jsonl` 和
  `retrieval_coverage`，标注失败题、可重试题和检索覆盖缺口。`blackbox.py` 和
  `compare.py` 支持黑盒指标导出和两次运行对比。
- **断点续跑**：QA 和 Judge 均支持 `--resume` 续跑，健康行不
  重复调用模型；`--checkpoint-interval` 定期落盘部分结果。

### 3. 简单易用 / AI 入口

直接 Python 调用，CLI 参数即配置，AI 友好。

- **直接启动**：`python run_eval.py --dataset <name>`，一条命令完成全流程，
  无需额外包装层。
- **CLI 参数驱动**：所有连接地址、模型配置、记忆后端、插件选择通过 CLI 参数
 传入，可写在 `.bat` / `.sh` 脚本中固化。环境变量作为默认值，CLI 参数覆盖。
- **Skill 交互式产品说明**：`benchmarks/skills/`（`locomo` / `hotpotqa` /
  `longmemeval`）、`dynamic/skills/`（`dynamic`）与 `performance/skills` 提供以
  skill 形式编写的交互式操作手册。AI 助手收到「跑评测 / 压测」请求时加载对应
  `SKILL.md`：
  先列出该数据集全部可配置参数，逐项向用户追问并解释参数含义、给出默认/推荐值
  （不替用户拍板），再生成评测命令并执行、交付结果。人类用户也可直接阅读
  `SKILL.md` 当作带参数说明的操作手册。
- **预检**：评测启动时自动验证数据集、记忆后端连通性和模型配置，通过后才进入
  正式评测流程。

正式 EchoMem 压测还支持 `--preflight-config`，会在第一条压测请求前拒绝
`fake-llm` / `fake-embedding`，并检查真实模型的 Endpoint、模型名和 API Key
环境变量。默认真实模型为 DashScope 的 `deepseek-v4-flash-0731`；
Embedding 使用 `text-embedding-v3`。

### 4. 生产一致

确保评测结果与生产环境完全一致。

- **真实记忆注入**：评测通过 `inject_memories()` 将数据集对话写入真实 EchoMem
  或 OpenViking 后端（`open_session -> add_message -> commit -> poll`），不使用
  mock 或旁路。
- **身份隔离**：每次评测新开独立 tenant / user / agent 身份，`--resume` /
  `--reuse-memory-from` 时复用原有身份。身份信息（account / user_id / auth_key）
  记录在 resume manifest 中，auth key 仅掩码保存。
- **数据完整性校验**：LoCoMo 在 QA 前校验数据集 SHA-256 和实际 session
  manifest，session 数量不匹配时拒绝运行，防止复用 tenant 被污染。
- **生产管线**：动态评测的 QA 阶段走 EchoAgent 完整 HTTP 管线，含 prefill /
  typing simulation / TTFT 采集，与线上行为一致。

## 目录结构

```
run_eval.py                 # 统一评测入口（python run_eval.py --dataset <name>）
plugins/                    # Agent 插件 (AgentPlugin 协议)
  base.py                   #   AgentPlugin ABC + AgentResponse / TypingResult
  registry.py               #   按名动态加载，无需手动注册
  bare_llm/                 #   纯 LLM 基线 (无记忆检索)
  echo_agent/               #   EchoAgent + EchoMem 完整管线 (动态评测默认)
  vikingbot/                #   VikingBot 工具调用 agent (LoCoMo 默认)
  echomem_mcp/              #   LLM 通过 EchoMem MCP 工具检索记忆
backends/                   # 记忆后端客户端
  memory_types.py           #   MemoryClient 协议 + BaseHTTPMemoryClient + NullMemoryClient
  memory_args.py            #   add_memory_backend_args() -- 后端连接 CLI 参数
  echomem/                  #   EchoMemClient (端口 8010)
  openviking/               #   OpenVikingClient (端口 19080)
benchmarks/                 # 静态数据集评测
  locomo/                   #   LoCoMo: LLM Judge (CORRECT/WRONG)
    dataset.py              #     数据集加载与解析
    import_memory.py        #     记忆导入
    qa.py                   #     QA 任务构建与执行
    judge.py                #     LLM Judge
    reporting.py            #     结果汇总
    data/                   #     内置 locomo10.json
    results/                #     运行结果
  hotpotqa/                 #   HotpotQA: F1/EM 官方指标
  longmemeval/              #   LongMemEval: LLM yes/no accuracy
  doc/                      #   benchmark 通用文档
  skills/                   #   交互式 skill 操作手册（locomo / hotpotqa / longmemeval）
dynamic/                    # 动态评测 (generate / replay)
  workflows.py              #   generate / replay 工作流
  simulator.py              #   场景与查询生成
  metrics.py                #   动态指标和多维质量评估
  artifacts.py              #   JSON/CSV/报告输出
  model_client.py           #   动态 LLM 客户端
  prompt_config.py          #   prompt 配置加载
  configs/                  #   evaluator / user_simulator YAML 配置
  skills/                   #   交互式 skill 操作手册（dynamic）
  results/                  #   运行结果
performance/        # EchoMem M1-M6 压测（多租户读写、容量、公平性、恢复与可观测性）
  engine.py                  #   通用场景引擎（场景=Python 文件，画像=YAML）
  probe.py                   #   探针执行器（PASS/FAIL/INCONCLUSIVE/NOT_IMPLEMENTED 四态）
  profile.py                 #   画像加载（YAML，${ENV:-default} 展开）
  report.py                  #   运行聚合与 summary.json / records.csv
  ctx.py                     #   场景 ctx API（请求/轮询/阶段注入/记录/断言）
  util.py                    #   通用基础设施（JSON/环境文件/输出锁/路径/模板/子进程/CSV/分布缩放）
  monitor.py                 #   服务端 Prometheus /metrics 采样与推导（系统无关）
  suite.py                   #   通用套件能力：case records 汇总 + 单 case 执行（Engine + 产物写盘）
  targets/echomem/
    observation_run.py       #   当前 M1-M6 唯一 CLI，生成 OUTPUT_DIR/report.html
    run_six_metrics.sh       #   quick / full / m6 包装脚本
    scenes/                  #   场景文件（A 纯读 / B 纯写 / C 混合 / D 洪峰 / barrier / burst-waves / capacity）
    probes/                  #   故障/恢复/限流/对账探针（真实 HTTP）
    acceptance/              #   M1-M6 预检、灌种、证据聚合、模块计时与 HTML 报告
    orchestrator/            #   内部场景矩阵、套件执行和探针编排
    profiles/                #   画像示例 + instance-profiles / tenants / fault-plan 示例
  targets/general/
    main.py                  #   通用场景引擎 CLI（python -m performance --target general：run / validate / probe / list）
    scenes/                  #   通用场景（不针对具体系统的临时压测放这里，结果落到 results/）
    probes/                  #   通用探针
  skills/echomem-stress/     #   正式压测交互式 skill 操作手册

正式套件的 barrier 场景会在正式提交屏障前只执行少量 seed warm-up；
屏障本身会按场景配置单独准备精确数量的未提交 session。不要把
`sessions_per_tenant` 配成 barrier 提交总数，否则真实模型 seed 会占满
case timeout，导致正式 barrier 尚未开始就生成 `NO_SUMMARY`。
shared/                      # 共享基础设施
  eval_base.py               #   EvalConfig / EvalRun / CLI arg helpers
  llm_client.py              #   LLM 客户端 (OpenAI 兼容, urllib)
  dataset_io.py              #   通用数据集路径解析与下载
  runtime_config.py          #   环境变量映射 + 预检
  recovery.py                #   QA CSV 健康判定与恢复
  qa.py                      #   通用 QA 数据结构
  csv_io.py                  #   CSV 读写工具
  import_guard.py            #   导入完整性校验
  benchmark_qa.py            #   benchmark QA 共享逻辑
scripts/                     # 辅助工具
  backend_doctor.py          #   记忆客户端健康检查
  validate_evidence.py       #   QA 检索证据格式检查
```

正式数据集的加载、Judge、指标、重试和报告归属 `benchmarks/<dataset>/`。评测
针对 agent 插件而非记忆后端；记忆注入通过 `AgentPlugin.inject_memories()` 统一
完成，评测平台不直接感知记忆后端。

## 核心架构

### 插件生命周期

```
setup(config)
  -> inject_memories(memories, backend=...)
  -> (create_session -> [simulate_typing] -> send_message)*
  -> getlog
  -> teardown
```

评测流程只调用 `AgentPlugin` 接口方法。`setup` 初始化客户端和记忆后端；
`inject_memories` 将数据集对话写入后端；QA 阶段逐题 `create_session` ->
`send_message`（可选 `simulate_typing` 触发 prefill）；`getlog` 收集后端日志。

### Benchmark 三阶段流程

```
导入记忆 (inject_memories) -> 逐题 QA (仅检索不写入) -> Judge / Evaluate
```

- **导入**：将数据集 conversation 按 session 分批写入记忆后端，commit + poll
  直到抽取完成。LoCoMo 校验数据集 SHA-256 和 session manifest。
- **QA**：并发（`--concurrency`）逐题检索记忆 -> 构建 prompt -> LLM 回答。
  检索阶段不写入记忆。支持 `--resume` 断点续跑。
- **评测**：LoCoMo / LongMemEval 使用 LLM Judge；HotpotQA 使用官方 F1/EM。
  产出 `summary.json`、`qa_results.csv`、`judge_results.csv`、`agent_traces/`。

### 动态评测双模式

```
generate: LLM 生成场景 -> 注入 EchoMem -> 逐轮 QA (端到端 EchoAgent 管线)
replay:   回放数据集对话 -> 注入 EchoMem -> 新会话 QA (跨 session 召回)
```

两种模式的注入阶段直连 EchoMem，不经 EchoAgent；QA 阶段走 EchoAgent 完整
管线（含 prefill / TTFT）。质量评分由 YAML 配置驱动，10 个维度满分 100。

## 快速开始

### 前置条件

- Python 3.10+
- 依赖安装：`pip install -r requirements.txt`（仅需 `tqdm` 和 `PyYAML`）
- 对应的后端服务已启动（见下表）

### 服务启动

评测前需启动对应的后端服务。以下为各服务端口说明：

| 服务 | 端口 | 用途 | 启动方式 |
|---|---|---|---|
| EchoMem | 8010 (HTTP) / 8011 (WS) | 记忆后端 (echomem) | `echomem server --host 127.0.0.1 --port 8010 --workspace <workspace>` |
| OpenViking | 19080 | 记忆后端 (openviking) | `openviking-server --config <config>` |
| EchoAgent Backend | 31020 | 动态评测 agent 后端 | `node dist/src/main.js`（EchoAgent 仓库） |
| EchoAgent Memory Engine | 31030 | EchoAgent 记忆引擎插件 | 随 EchoAgent Backend 启动 |

Benchmark 评测只需启动记忆后端（EchoMem 或 OpenViking）。动态评测还需额外
启动 EchoAgent Backend（含 Memory Engine）。

### 环境变量（可选）

CLI 参数可直接传入，也可通过环境变量设默认值：

| 变量 | 说明 |
|---|---|
| `ECHOMEM_BASE_URL` | EchoMem HTTP 地址，默认 `http://127.0.0.1:8010` |
| `ECHOMEM_ACCOUNT` / `ECHOMEM_USER_ID` / `ECHOMEM_AGENT_ID` | 记忆后端身份 |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | 回答模型配置 |
| `JUDGE_MODEL` / `JUDGE_TOKEN` / `JUDGE_BASE_URL` | Judge 模型（默认同回答模型） |
| `HOTPOTQA_DATASET` / `LONGMEMEVAL_DATASET` | 数据集路径（LoCoMo 已内置） |

### 预检

评测启动时自动执行预检：加载数据集验证非空、调用 `memory_client.health()`
检查记忆后端连通性。通过后进入正式评测流程。

## 运行评测

统一入口 `python run_eval.py --dataset <name>`，一条命令完成「导入记忆 → 逐题 QA →
Judge 评分 → 结果报告」全流程，CLI 参数即配置。命令骨架：

```bash
python run_eval.py --dataset <locomo|hotpotqa|longmemeval|dynamic> \
  --agent-plugin <vikingbot|echomem_mcp|bare_llm|echo_agent> \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash-0731 \
  --llm-api-key YOUR_KEY \
  [其余参数见下表]
```

### Benchmark 概览

| Benchmark | 默认插件 | 评测方式 | 数据集 |
|---|---|---|---|
| `locomo` | `vikingbot` | LLM Judge (CORRECT/WRONG) | 内置 `locomo10.json` |
| `hotpotqa` | `vikingbot` | F1/EM 官方指标 | 需设置 `HOTPOTQA_DATASET` |
| `longmemeval` | `vikingbot` | LLM yes/no accuracy | 需设置 `LONGMEMEVAL_DATASET` |

### 通用参数（所有数据集）

| 参数 | 取值范围 / 默认 | 含义 |
|---|---|---|
| `--agent-plugin` | 插件名；默认 `vikingbot`（`dynamic` 默认 `echo_agent`） | 被测 agent 插件 |
| `--llm-base-url` / `--llm-model` / `--llm-api-key` | 地址默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`；模型默认 `deepseek-v4-flash-0731`；Key 必填 | 回答 LLM 地址、模型、Key |
| `--llm-temperature` / `--llm-max-tokens` | `0.7` / `2048` | 采样温度 / 最大输出 token 数 |
| `--llm-timeout-s` / `--llm-retries` | `120` / `3` | 单次 LLM 请求超时（秒）/ 重试次数 |
| `--top-k` | `10`（`echomem_mcp` 为 `25`） | 检索记忆条目数 |
| `--memory-budget-chars` | `8000` | 注入 prompt 的记忆最大字符数 |
| `--question-timeout-s` | `120`（`0`=不额外限制） | 每题检索 + 回答超时（秒） |
| `--concurrency` | `4`（`dynamic` 不适用） | QA 并发数 |
| `--out-dir` | `results` | 结果根目录 |
| `--judge-model` / `--judge-api-key` / `--judge-base-url` | 默认同 `--llm-*` | Judge LLM（仅 `locomo` / `longmemeval`） |
| `--memory-backend` | `echomem` / `openviking`，默认 `echomem` | 记忆后端（`vikingbot` / `echo_agent` 支持） |
| `--echomem-url` | 默认 `http://127.0.0.1:8010` | 记忆后端地址 |
| `--echomem-auth-key` / `--echomem-log-access-key` | 空 | 后端鉴权 Key / 特权日志查询 Key |
| `--account` / `--user-id` / `--agent-id` | `default` | 记忆后端身份 |
| `--workspace` | 空 | 后端工作区路径 |
| `--commit-timeout-s` / `--commit-poll-interval-s` | `0`（无限等待）/ `2` | commit 轮询超时（秒）/ 轮询间隔（秒） |
| `--timeout-s` / `--max-retries` | `60` / `3` | 后端 HTTP 请求超时（秒）/ 重试次数 |

### LoCoMo 参数

| 参数 | 取值范围 / 默认 | 含义 |
|---|---|---|
| `--dataset-path` | 空（自动查找/下载） | 数据集 JSON 路径 |
| `--sample` | `all` 或 sample_id | 筛选 sample |
| `--questions` | `0`=全部 | 限制 QA 题数 |
| `--question-ids` | 逗号分隔 | 指定题目（优先于 `--questions`） |
| `--session-mode` | `auto` / `locomo` / `single`，默认 `auto` | 会话组织方式 |
| `--max-sessions` | `0`=全部 | 每个 sample 最多导入的原始 session 数 |
| `--qa-profile` | `vikingboat0411` / `vikingboat0411-natural-no-tools`，默认按 `--tool-calling` 推断 | QA 执行 profile |
| `--checkpoint-interval` | `10`（`0`=关） | 每 N 题落盘 QA CSV |
| `--resume` | 空 | 续跑（run 目录或 qa_results CSV） |
| `--reuse-memory-from` | 空 | 复用身份 + 已导入记忆，QA/Judge 全量重跑 |
| `--judge-concurrency` | `4` | Judge 并发数 |
| `--judge-checkpoint-interval` | `10` | 每 N 题落盘 Judge CSV |

### HotpotQA 参数

| 参数 | 取值范围 / 默认 | 含义 |
|---|---|---|
| `--dataset-path` | 空（自动查找/下载） | 数据集 JSON 路径 |
| `--sample` | `all` 或 index/id | 筛选 sample |
| `--questions` | `0`=全部 | 限制 QA 题数 |
| `--question-ids` | 逗号分隔 | 指定题目 |
| `--import-mode` | `per_question` / `global` / `documents`，默认 `per_question` | 导入模式：每题各自导入 / 合并共享 session / 文档语料 RAG |
| `--checkpoint-interval` | `10`（`0`=关） | 每 N 题落盘 QA CSV |
| `--resume` | 空 | 续跑（run 目录或 qa_results CSV） |
| `--reuse-memory-from` | 空 | 复用身份 + 已导入记忆，QA 全量重跑 |

### LongMemEval 参数

| 参数 | 取值范围 / 默认 | 含义 |
|---|---|---|
| `--dataset-path` | 空（自动查找/下载） | 数据集 JSON 路径 |
| `--sample` | `all` 或 index/id | 筛选 sample |
| `--questions` | `0`=全部 | 限制 QA 题数 |
| `--question-ids` | 逗号分隔 | 指定题目 |
| `--random-count` / `--random-seed` | `0` / `30` | 随机抽题数（0=不随机）/ 随机种子 |
| `--parallel-shards` / `--parallel-workers` | `1` / `2` | 分片进程数 / 并发分片进程数 |
| `--parallel-dry-run` | 关 | 只写分片清单，不启动评测进程 |
| `--checkpoint-interval` | `10`（`0`=关） | 每 N 题落盘 QA CSV |
| `--resume` | 空 | 续跑（run 目录或 qa_results CSV） |
| `--reuse-memory-from` | 空 | 复用身份 + 已导入记忆，QA/Judge 全量重跑 |

### 动态评测（dynamic）

`--dataset-path` 不指定则进入 `generate` 模式（LLM 生成背景记忆和提问，端到端走
EchoAgent 完整管线，含 prefill / TTFT）；指定则进入 `replay` 模式（回放数据集
对话，测试跨 session 召回）。需先启动 EchoAgent Backend（端口 31020）和 EchoMem
（端口 8010）。

| 参数 | 取值范围 / 默认 | 含义 |
|---|---|---|
| `--dataset-path` | 空=generate，指定=replay | 模式切换 |
| `--sample` / `--questions` | `all` / `0` | 筛选 sample / 限制题数 |
| `--evaluator-config` | `configs/evaluator_template.yaml` | 评测器 YAML（10 个评分维度） |
| `--num-memories` / `--num-queries` | `5` / `10` | 生成的背景记忆数 / 提问数（generate） |
| `--new-session-ratio` | `0.3`（0~1） | 新 session 占比 |
| `--typing-speed-ms` | `200`（`<50`=快速模式：单 tick + finalize，无逐字延迟） | 打字模拟速度（毫秒/字符） |
| `--typing-jitter-ms` | `20` | 打字间隔抖动（毫秒） |
| `--user-simulator-config` | `configs/user_simulator_default.yaml` | 用户模拟器 YAML |
| `--scenario-model` / `--scenario-base-url` / `--scenario-api-key` | 默认 `deepseek-v4-flash-0731` / 空 / 空（env `ECHOAGENT_TEST_SCENARIO_*`） | 场景生成 LLM（generate） |
| `--echoagent-url` | `http://127.0.0.1:31020` | EchoAgent 后端地址 |
| `--username` / `--password` | `test_user` / 空 | EchoAgent 登录凭证 |
| `--memory-engine-endpoint` | `http://127.0.0.1:31030` | EchoAgent 记忆引擎插件地址 |
| `--out-dir` | `results` | 结果目录 |

### Agent 插件参数

`--tool-calling`（默认**关**）控制是否把记忆工具暴露给回答模型：关闭时模型不做
工具调用，只完成初始记忆检索后的单轮回答；开启时才允许通过 MCP / 工具调用迭代
检索。旧版 `--tool-calling` / `--no-tool-calling` 成对出现且默认开启（由 argparse
`BooleanOptionalAction` 生成），现已统一为单个 `--tool-calling`，默认关闭。

| 插件 | 参数 | 取值范围 / 默认 | 含义 |
|---|---|---|---|
| 全部 | `--tool-calling` | 关 | 是否启用 LLM 工具调用 |
| `echomem_mcp` | `--mcp-url` | `http://127.0.0.1:8001` | EchoMem MCP 地址 |
| | `--mcp-auth-key` | 空（回退 `--echomem-auth-key`） | MCP 鉴权 Key |
| | `--mcp-max-iterations` | `50` | 每题最大工具调用轮数 |
| | `--mcp-read-mode` | `disabled` / `allow`，默认 `allow` | 是否保留读取 `messages.jsonl` 的 read 工具 |
| | `--user-memory-budget-chars` / `--agent-memory-budget-chars` | `4000` / `2000` | 注入的用户 / agent 记忆最大字符数 |
| | `--answer-style` | `factoid` / `natural`，默认按 benchmark 推断 | 回答风格 |
| `vikingbot` | `--vikingbot-workspace` | 插件内置 bootstrap 目录 | SOUL.md / TOOLS.md 工作区 |
| | `--tool-search-limit` / `--tool-search-pool-multiplier` | profile 默认 | 工具搜索数量上限 / 搜索池倍数 |
| | `--user-memory-budget-chars` / `--agent-memory-budget-chars` | profile 默认 | 注入的用户 / agent 记忆最大字符数 |
| | `--max-iterations` | profile 默认 | 最大迭代轮数 |
| | `--initial-min-score` / `--tool-min-score` | profile 默认 | 初始检索 / 工具调用最低分 |
| | `--tool-set` | `search_read` / `vikingbot_native_safe` / `vikingbot_echo_native`，默认按 profile | 工具集 |

### 断点续跑与记忆复用

- `--resume <run目录 或 qa_results.csv>`：复用原身份，跳过已完成的导入批次、复用
  健康 QA 答案（`locomo` 还复用 Judge 结论），只跑缺失/不健康部分；指标按合并后的
  整轮计算。
- `--reuse-memory-from <run目录>`：复用原身份和已完成的记忆导入，但 QA（含 Judge）
  全量重跑，适合更换插件 / 参数后对同一批记忆重新评测。

### 结果文件

结果写入 `benchmarks/<name>/results/<timestamp>/`（动态为 `dynamic/results/<timestamp>/`）。
静态 benchmark 主要产出 `qa_results.csv`、`judge_results.csv`、`summary.json`、
`config.json`、`agent_traces/`、`backend_logs.json`；动态评测产出 `dataset.json`、
`dynamic_results.csv`、`summary.json`、`quality_report.json`。

### 双后端对比（EchoMem vs OpenViking）

同 agent 隔离口径下对比两个记忆后端：先 `generate` 一次产出场景 `dataset.json`，
再用 `replay` 把同一份数据集对 `echomem` / `openviking` 各回放一遍，最后生成
自包含 HTML 图表报告。一键流程见 `START_BAT/compare_echomem_vs_openviking.bat`。

## EchoMem M1-M6 性能压测

当前唯一入口是 `performance.targets.echomem.observation_run`；也可以使用同一入口的
包装脚本 `performance/targets/echomem/run_six_metrics.sh`。测试在使用者本机部署的
EchoMem 上运行，不要求服务器，也不强制限制为 4U8G。完整部署、配置字段和指标口径见
[EchoMem 本机部署与六项压测手册](performance/targets/echomem/README.md)。

### 1. 准备本机配置

使用 `performance_refactor` 分支，并从当前 EchoMem 代码自己的
`configs/config.example.json` 生成运行配置。使用真实 LLM、真实 Embedding、真实记忆引擎，
并为各租户准备独立凭据；密钥只写入 Git 之外的环境文件。

需要准备：

- `PROFILE_JSON`：本机服务地址、容器名、租户文件、M1 档位和所选参数；
- `ENV_FILE`：模型密钥、租户密钥和测试控制 token；
- `OUTPUT_DIR`：本次全新的结果目录，续跑时才允许复用。

第一次运行前安装项目依赖，并确认入口存在：

```bash
test -f performance/targets/echomem/observation_run.py
bash -n performance/targets/echomem/run_six_metrics.sh
```

### 2. 先跑快速链路检查

```bash
performance/targets/echomem/run_six_metrics.sh quick \
  PROFILE_JSON OUTPUT_DIR ENV_FILE
```

Quick 使用真实 HTTP 和真实模型，但缩短采样，只用于确认部署、租户、模型、记忆注入、
Search、Commit 和报告链路能够执行。它的结论是 `PARTIAL`，不能当作正式容量边界。

### 3. 运行前三项或指定指标

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE_JSON \
  --metrics M1,M2,M3 \
  --env-file ENV_FILE \
  --out-dir OUTPUT_DIR
```

`--metrics` 可填写 `M1` 到 `M6` 的任意逗号分隔组合。M4 会执行租户故障注入，M5 会
重启指定的压测专用容器；执行这两项前必须确认目标不是共享或生产实例。

### 4. 运行完整六项

```bash
performance/targets/echomem/run_six_metrics.sh full \
  PROFILE_JSON OUTPUT_DIR ENV_FILE
```

默认不运行 soak。正式运行保留超时、拒绝、Provider 异常、pending、空召回和质量失败的
完整分母，不会因为 EchoMem 内部并发配置较小而自动降低客户端负载。

### 5. 续跑

只有 EchoMem 版本、配置、模型、租户和请求计划均未变化时，才能复用原目录：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles PROFILE_JSON \
  --env-file ENV_FILE \
  --out-dir OUTPUT_DIR \
  --resume
```

修改服务版本或并发参数后必须创建新目录，不能混合成同一次测试。

### 6. 查看结果

当前六项主报告始终生成在：

```text
OUTPUT_DIR/report.html
```

同时保留：

- `execution-manifest.json`：代码版本、配置指纹和模型预检；
- `summary.json`：M1-M6 结构化结果与完整分母；
- `suite.json`：场景和探针索引；
- `records.csv`、`metrics_samples.csv`：逐请求与资源证据。

运行结束后执行：

```bash
test -f "OUTPUT_DIR/report.html"
```

若该文件不存在，本轮没有生成当前 M1-M6 报告，应查看命令和进程退出信息后修复，不能用
其他 HTML 文件替代。

## 扩展指南

### 新增 Agent 插件

1. 创建 `plugins/<name>/` 目录
2. 创建 `__init__.py`（空即可）
3. 创建 `plugin.py`，实现 `AgentPlugin` 子类：

```python
from plugins.base import AgentPlugin, AgentResponse

class MyAgentPlugin(AgentPlugin):
    def setup(self, config: dict) -> None:
        # 初始化客户端、创建 memory_client
        ...

    def inject_memories(self, memories, *, backend="echomem", session_id=""):
        # 写入记忆后端 (不支持的插件不覆盖，默认 no-op)
        ...

    def create_session(self, title=""):
        # 创建 QA 会话，返回 session_id
        ...

    def send_message(self, session_id, message, context_path="/", *, extra=None):
        # 发送消息，返回 AgentResponse
        return AgentResponse(text="...")

    def getlog(self) -> str:
        # 返回日志 JSON 字符串
        return "{}"
```

4. 实现 `add_arguments` classmethod 声明 CLI 参数，可复用
   `backends/memory_args.py` 中的 `add_memory_backend_args()`。

`registry.py` 自动扫描 `plugins.<name>.plugin` 模块中 `AgentPlugin` 的子类，
无需手动注册。运行：`python run_eval.py --dataset locomo --agent-plugin <name> ...`

### 新增记忆后端

1. 创建 `backends/<name>/` 目录
2. 创建 `client.py`，实现 `BaseHTTPMemoryClient` 子类，覆盖 `_headers()` 和
   `_fetch_commit_status()` 等抽象方法，并实现 `search` / `fs_read` /
   `fs_list` / `fs_glob` 等检索方法
3. 在 `backends/memory_args.py` 的 `add_memory_backend_args()` 中添加连接参数
4. 在使用该后端的插件 `setup()` 中实例化客户端

```python
from backends.memory_types import BaseHTTPMemoryClient

class MyBackendClient(BaseHTTPMemoryClient):
    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}
    # 实现 search / commit / fs 等方法...
```

### 新增 Benchmark 数据集

1. 创建 `benchmarks/<name>/` 目录
2. 实现核心模块：
   - `dataset.py` - 数据集加载与解析
   - `import_memory.py` - 记忆导入逻辑
   - `qa.py` - QA 任务构建与执行
   - `judge.py` 或 `evaluate.py` - 评测逻辑
   - `reporting.py` - 结果汇总
3. 在根级 `run_eval.py` 中注册：新增 `build_<name>_parser` / `run_<name>`，
   并挂到 `_DATASET_PARSERS` / `_RUNNERS`
4. 复用 `shared/` 基础设施：`EvalConfig` / `EvalRun` /
   `add_agent_plugin_args` / `add_eval_args` / `add_judge_args` / `LLMClient`

## 评测流程概览

| Benchmark | 导入方式 | QA 方式 | 评测方式 |
|---|---|---|---|
| LoCoMo | 集中导入所有 session | 仅检索不写入 | LLM judge (CORRECT/WRONG) |
| HotpotQA | per_question 或 global | 仅检索不写入 | answer/supporting-fact/joint F1/EM |
| LongMemEval | 逐题隔离导入 haystack | 仅检索不写入 | 官方 accuracy (LLM yes/no) |
| 动态 (generate) | LLM 生成场景 | 端到端 EchoAgent | 配置驱动质量评估 (0-100) |
| 动态 (replay) | 先注入对话再 QA | 跨 session 检索 | 配置驱动质量评估 (0-100) |

> **指标变更同步约定**：如果任何一个 benchmark（locomo / hotpotqa / longmemeval）
> 或 dynamic 的评估指标、产物字段（`summary.json` / `quality_report.json` /
> `eval_results.csv` / `dynamic_results.json` 等），或 performance 压测的产物字段
> （`summary.json` / `requests.csv` / `metrics_samples.csv` 等）发生增删或含义改变，
> 必须同步更新 `scripts/memory-eval-improve` skill 中对应的 benchmark/dynamic/
> performance **特有字段描述**
> （`references/benchmark-specific-fields.md` 与 `references/analysis-dimensions.md`），
> 避免分析报告基于过时的字段定义得出结论。

## 辅助工具

```bash
# 记忆客户端健康检查
python scripts/backend_doctor.py --format json

# QA 检索证据格式检查
python scripts/validate_evidence.py --input /path/to/qa_results.csv --strict

# LoCoMo 黑盒指标导出
python benchmarks/locomo/blackbox.py \
  --qa /path/to/run/qa_results.csv \
  --judge /path/to/run/judge_results.csv \
  --import-results /path/to/run/import_results.csv \
  --summary /path/to/run/summary.json \
  --out-dir /path/to/report

# 两次运行结果对比
python benchmarks/locomo/compare.py \
  --left /path/to/run-a \
  --right /path/to/run-b \
  --out-dir /path/to/comparison
```

各 benchmark 详细参数见对应 `docs/usage.md`。插件设计细节见
`plugins/README.md`，记忆后端设计细节见 `backends/README.md`。
