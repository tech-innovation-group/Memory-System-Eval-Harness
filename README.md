# Memory-System-Eval-Harness

记忆系统评测框架。全 CLI，无网页 UI。直接通过 Python 脚本完成数据集加载、
记忆注入、Agent 问答、Judge 评分和结果报告。

**EchoMem 4U8G 六项黑盒观测压测**：使用
`python -m performance.targets.echomem.observation_run`，详见
[单文件本机部署与六项运行手册](performance/targets/echomem/README.md)。该文档包含交给
AI 的完整提示词、PR32/PR449 代码要求、真实模型配置、128 租户、默认/调优两组压测、
运行命令和最终报告位置。
该入口不设置 P95、准确率、Jain、劣化比例或吞吐 PASS/FAIL 门槛；状态仅为
`MEASURED / PARTIAL / BLOCKED / EXECUTION_ERROR`，默认不运行 soak。旧的
`python -m performance --target echomem --six-metrics` 保留为历史 SLO
验收入口，不要用于本轮观测结论。

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
performance/        # 性能压测与正式验收（多租户并发读写、注入/检索延迟、CPU/RSS、O1-O7）
  engine.py                  #   通用场景引擎（场景=Python 文件，画像=YAML）
  probe.py                   #   探针执行器（PASS/FAIL/INCONCLUSIVE/NOT_IMPLEMENTED 四态）
  profile.py                 #   画像加载（YAML，${ENV:-default} 展开）
  report.py                  #   运行聚合与 summary.json / records.csv
  ctx.py                     #   场景 ctx API（请求/轮询/阶段注入/记录/断言）
  util.py                    #   通用基础设施（JSON/环境文件/输出锁/路径/模板/子进程/CSV/分布缩放）
  monitor.py                 #   服务端 Prometheus /metrics 采样与推导（系统无关）
  suite.py                   #   通用套件能力：case records 汇总 + 单 case 执行（Engine + 产物写盘）
  targets/echomem/
    main.py                  #   正式验收编排器 CLI（python -m performance.run --target echomem）
    scenes/                  #   场景文件（A 纯读 / B 纯写 / C 混合 / D 洪峰 / barrier / burst-waves / capacity）
    probes/                  #   故障/恢复/限流/对账探针（真实 HTTP）
    acceptance/              #   求值器：preflight / seed / metrics / features /
                             #     evaluate(8 门禁) / scheduler(7 检查) / objectives(O1-O7) / report_html
    orchestrator/            #   编排器实现（场景矩阵 + 套件执行 + 探针编排 + objective-suite 报告）
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

## 性能压测

对运行中的 EchoMem 服务做多租户高并发**读写性能**压测（不需要 LLM）：检索
吞吐/延迟、注入四段延迟（open / add / commit 提交 / commit 完成）、读写混合与
「注入洪峰」下的劣化（**检出"注入阻塞检索"**）。设计见
`performance/docs/设计意图.md`。

场景引擎（`performance --target general run`）产出 `summary.json`（请求统计：rps、延迟
p50/p95/p99、错误分类、租户分组；场景可挂 `report` 钩子输出 `custom` 段，
如 search 质量聚合）与 `records.csv`（逐请求明细）。正式验收不直接消费
summary，而是由编排器（`echomem-acceptance`）统一灌种、跑 case，把 records
汇总成验收门禁与七项目标：

- **验收门禁**（`acceptance/evaluate.py`，8 gate）：search 成功率 / report6
  质量 / 隔离 / 公平 / commit 完成 / 拒绝 / hot tenant / 容量阶梯，逐项
  PASS / FAIL / INCONCLUSIVE，产出 `suite.json` / `acceptance.json`。
- **七项目标 O1-O7**（`acceptance/objectives.py`）：DAU/热租户容量、多规格
  配置、单租户故障隔离、Jain 公平性、Search 优先级、Commit kill-9 恢复重放、
  分层调度可观测性，产出 `objective-suite.json` + `objective-suite.html`。
- **服务端观测与特性判定**（`acceptance/metrics.py` 的 `/metrics` 采样、
  `features.py` 的 13 特性判定，含 commit_guarantee / tenant_fairness /
  memory_leak / resource_timeline 四项保证）：纯逻辑模块，由单元测试约束，
  供验收口径扩展复用（阈值见设计意图 §15.2）。

```bash
# 运行一个场景（场景=Python 文件，画像=YAML；输出 performance/targets/echomem/results/<ts>/）
# 通用引擎入口统一走 --target general；不针对具体系统的场景/探针放 targets/general/ 下
python -m performance --target general run \
  --scene performance/targets/echomem/scenes/scene_c_mixed.py \
  --profile performance/targets/echomem/profiles/echomem.yaml

# 场景清单与契约校验
python -m performance --target general list
python -m performance --target general validate --scene performance/targets/echomem/scenes/scene_a_pure_read.py
```

场景说明：`A` 纯读基线（劣化对照）· `B` 纯写注入（四段延迟 + 写后读一致性 +
commit 成功保证）· `C` 读写混合（多档 read:write）· `D` 注入洪峰（读持续 +
突发 K 个 commit，检出 search-commit 干扰与读写数据倾斜）· 另有 `barrier` /
`burst-waves` / `capacity` 三个正式场景，由编排器的场景矩阵按 case 参数驱动。
引擎、场景契约与画像格式详见 `performance/docs/设计意图.md`。

正式验收不直接调用场景引擎，而是通过编排器（`echomem-acceptance`）统一
执行：按机器规格 profile 组织场景矩阵、灌种、跑探针并汇总 O1-O7。

### 正式验收编排器（echomem-acceptance）

正式验收按机器规格逐个执行容量、稳定性、公平性、Search 优先级、Commit 恢复
和 `/metrics` 可观测性检查，对每轮套件汇总七项目标 `O1-O7`（DAU/热租户容量、
多规格配置、单租户故障隔离、Jain 公平性、Search 优先级、Commit kill-9 恢复
重放、分层调度可观测性），写 `objective-suite.json` + `objective-suite.html`。
场景矩阵：`complete` 26 例（报告 12 例 + 场景集 14 例）；`4u8g` 22 例 bounded
目录（容量档关后台 Commit，另含 fairness-bounded）。先把
`performance/targets/echomem/profiles/instance-profiles.example.json`
复制为实际 profile 配置，填写真实 `tenant_config`、`preflight_config` 和可选
`prepare_command`：

```bash
# quick bounded smoke（默认 7 例 QUICK_SCENARIOS，每场景 ≤30s、barrier ≤32）
python -m performance.run --target echomem \
  --profiles performance/targets/echomem/profiles/instance-profiles.example.json \
  --profile 4U8G \
  --out-dir performance/targets/echomem/results/objective-suite-$(date +%Y%m%d_%H%M%S) \
  --quick
```

缺少故障控制、重启控制或多规格实测时，报告保留 `INCONCLUSIVE`，不会
根据客户端延迟或 HTTP 200 推断 EchoMem 已实现对应保证。

旧完整套件的专项验收口径已收紧：容量项必须有真实完成请求且 Search/Commit
成功率达标；多规格必须有至少两种规格的实际运行记录；公平性必须同时有逐租户
Commit 完成吞吐和 Search P95，取两者 Jain 的较小值；Search 优先级只接受已完成的
洪泛场景，并直接检查 Search P95 是否不超过 5 秒；恢复项必须同时通过消息集合、
cursor 和幂等重放对账；可观测性必须验证至少两个租户在每个预期 lane 上都有
queued/wait/exec/rejected 四元组。旧报告只有单维 Jain、单一指标族或“配置已写入”
的结果，会保留为 `INCONCLUSIVE`，不会被误判为通过。

恢复对账必须使用 EchoMem `POST /messages` 返回的服务端消息 `id`（例如
`msg_*`）；压测脚本生成的 `recovery-*` 只作为请求关联字段，不能当作持久化
消息 ID。`Commit` 返回 `completed` 只证明事务到达终态，仍需另外核对
`history`、archive 和 `echo://sessions/{session}/current/commit_cursor.json`；
没有显式幂等键时，报告会把“消息已持久化”和“重复提交幂等”分开判定。

正式压测先执行两道前置门禁：profile 配置 `preflight_config` 时先跑
模型/配置 preflight 检查（确认 EchoMem 使用真实模型而非 fake-llm，结果记录在
`suite.json` 的 `preflight` 段，失败提前返回）；随后在灌种阶段用每个租户的
真实凭据调用 `POST /api/sessions/open` 灌入种子。任何一个租户返回 `401`、
连接失败或配置缺失，`seed` 段都会记录 `ENV_ERROR` 并提前返回，不再输出
「0 请求」的伪压测数据；该段只保留错误摘要与租户计数，不保存密钥。
正式复用已有记忆时，种子按 case 的 `sessions_per_tenant` / `messages_per_session`
灌入并真实提交到 EchoMem，可被后续场景复用。

如果正式套件在场景启动前停止，先看 `suite.json` 的 `preflight` / `seed` 段和
`objective-suite.json` 的探针命令记录。`preflight` 失败说明 EchoMem 配置仍指向
fake 模型或模型凭据缺失；`seed` 段 `ENV_ERROR` 说明租户凭据错误（401）、接口
错误（4xx）、服务异常（5xx）或网络超时，错误信息保留状态码、请求路径和截断后的
服务响应，便于区分。这类前置失败应归为测试环境/配置问题，不能直接判定 EchoMem
功能失败。
如果服务器只注册了部分租户，可以在 profile 中设置 `"allow_partial_tenants": true`
（记录在 `suite.json` 的 manifest 中，表示本轮接受部分租户参与）；场景仍按
`tenant_config` 实际可用的租户数运行，公平性、故障隔离和多租户容量在没有覆盖
全部租户样本时保持 `INCONCLUSIVE`，不会把单租户结果冒充多租户通过。
`--quick` 默认把灌种降到每租户 1 个会话（`seed_sessions_per_tenant=1`），不做
真实模型灌种；需要把已有租户记忆纳入 quick 验证时可加 `--quick-include-seed`
（或 profile 配置 `quick_include_seed`）。这种运行可以验证服务调度和延迟，
但不能替代真实记忆质量测试。

- **套件执行**（`orchestrator/runner.py`）：按 case 逐场景进程内执行
  （`complete` 26 例 / `4u8g` 22 例），灌种后运行并把 records 汇总成
  `evaluate` 验收门禁（8 个 gate：search 成功率 / report6 质量 / 隔离 /
  公平 / commit 完成 / 拒绝 / hot tenant / 容量阶梯）消费的契约摘要，
  产出 `suite.json` / `acceptance.json`。只有每次运行都用独立租户凭据才允许
  做出上线结论。
- **故障 / 恢复 / 限流 / 对账探针**（`probes/`）：编排器按 profile 配置段
  （`capability_probe` / `commit_recovery` / `fault_plan` / `missing_cases` /
  `concurrent_commit` / `limit_failure_sweep`）调用，直接以真实 HTTP 访问
  EchoMem；只在部署方显式提供故障/恢复控制时才执行真实操作，否则如实上报
  `INCONCLUSIVE`，显式 404 是「未实现」的唯一证据。

`--quick` 在 4U8G profile 上走 `4u8g` bounded 目录（22 例，
capacity-2/4/8 强置 `quick_commit_rpm=0`，公平性/优先级等场景 barrier ≤32）；
不传 `--scenarios` 时只跑默认 7 例 quick 子集（baseline /
fairness-bounded / search-priority-blackbox / saturation / capacity-2 /
capacity-4 / capacity-8）。正式模式（去掉 `--quick`）按 `complete` 目录
（26 例）执行，包含 7 小时 `soak` 等长时场景；单台 4U8G 不想等 `soak` 时，
用 `--scenarios` 显式挑选 bounded 场景。两种模式都不会伪造 kill-9 或依赖
未提供的故障控制结果。

`--quick` 只缩短测试窗口，不改变验收口径：没有真实 Search/Commit
样本、第二种实例规格、真实重启/故障控制或服务端指标时，结果仍明确记为
`INCONCLUSIVE`，并在 `suite.json` / `acceptance.json` 记录缺失证据及归属
（测试平台、部署配置或 EchoMem 服务端）。因此“无法测试”不等同于
“EchoMem 功能失败”。

```bash
# 正式验收（去掉 --quick；complete 26 例）
python -m performance.run --target echomem \
  --profiles performance/targets/echomem/profiles/instance-profiles.example.json \
  --profile 4U8G \
  --out-dir performance/targets/echomem/results/objective-suite-$(date +%Y%m%d_%H%M%S)
```

种子数据准备默认按最多 4 个租户并行执行，以缩短真实模型 commit 的准备时间；
种子阶段不计入压测窗口。种子并发在 `acceptance/seed.TenantPreparer` 的
`seed_concurrency` 配置，正式负载阶段仍按场景配置独立控制并发。

单实例 4U8G 的正式验收即上述命令（complete 26 例，含 `soak`）；不想等
`soak` 时用 `--scenarios` 排除它。在服务器上以后台方式运行并跟踪日志（先按
「服务器测试指南」第 5 步把示例 profile 复制为实际配置）：

```bash
cd /opt/Memory-System-Eval-Harness
export STRESS_OUTPUT_DIR=/opt/Memory-System-Eval-Harness/performance/targets/echomem/results/objective-suite-$(date +%Y%m%d_%H%M%S)
nohup python -m performance.run --target echomem \
  --profiles /opt/echomem-stress/instance-profiles.json \
  --profile 4U8G \
  --out-dir "$STRESS_OUTPUT_DIR" \
  >"$STRESS_OUTPUT_DIR/launcher.log" 2>&1 &
```

结果写入 `objective-suite-<ts>/`：`objective-suite.json`（逐 profile 的
suite 摘要 + 探针制品 + O1-O7 汇总）与 `objective-suite.html`（自包含报告）；
每个 profile 目录下是 `suite.json` / `acceptance.json`，每个 case 目录保留该
场景的 `summary.json` / `records.csv` / `commit_results.csv` /
`search_results.csv`。

| 参数 | 说明 | 默认 |
|---|---|---|
| `--profiles` | instance-profiles JSON（`{"profiles":[...]}`） | 必填 |
| `--profile` | 只运行指定 name 的 profile | 全部 |
| `--out-dir` | 输出目录（objective-suite.json / html） | 必填 |
| `--quick` | bounded smoke：每场景 ≤30s、barrier ≤32、容量档关 Commit、灌种降到每租户 1 会话 | 关 |
| `--scenarios` | 覆盖场景列表（逗号分隔，按 case label 过滤） | quick 默认 7 例子集，正式默认全量 |
| `--quick-duration-cap-s` | quick 每场景时长上限 | 30 |
| `--quick-case-timeout-s` | quick 单 case 超时 | 120 |
| `--quick-barrier-count-cap` | quick barrier Commit 上限（<32 不能验收严格优先级） | 32 |
| `--quick-include-seed` | quick 也保留真实模型灌种 | 关 |
| `--timeout-s` | 正式单 case 超时 | 7200 |
| `--skip-run` | 只读已有 suite.json 重新生成总报告（审计） | 关 |
| `--suite-path` | 配合 `--skip-run` 指定 suite.json | profile 目录 |
| `--env-file` | 加载 KEY=VALUE 环境文件供探针使用（密钥不写报告） | 无 |

## 服务器测试指南

本节用于团队成员在 Linux 服务器上测试已经启动的 EchoMem。推荐把 EchoMem
和 Harness 放在同一台机器上：EchoMem 只监听 `127.0.0.1:8010`，Harness
通过本机或 Docker host network 访问，不需要把 EchoMem API 暴露到公网。

### 1. 拉取测试平台

以下为既有 `v3` 套件入口；基于 PR31 的无性能门槛观测请使用文首运行手册中的新 PR 分支：

```bash
git clone -b v3 git@github.com:noi031/Memory-System-Eval-Harness.git
cd Memory-System-Eval-Harness
git rev-parse --short HEAD
```

已有目录执行：

```bash
git fetch origin
git checkout v3
git pull --ff-only origin v3
```

测试代码不要在服务器上临时修改；需要修改时先提交到测试平台 PR。

### 2. 启动并检查 EchoMem

EchoMem 必须先启动，Harness 不负责拉起被测服务：

```bash
cd /opt/echomem
export ECHOMEM_AUTO_COMMIT_THRESHOLD=20000
echomem server \
  --workspace /opt/echomem-stress/workspace \
  --host 127.0.0.1 \
  --port 8010
```

另开终端检查：

```bash
curl -fsS http://127.0.0.1:8010/health
curl -fsS http://127.0.0.1:8010/metrics >/dev/null
```

如果这里失败，先查看 EchoMem 日志，不要直接启动压测。

### 3. 配置真实模型

正式压测禁止使用 `fake-llm` 或 `fake-embedding`。模型 endpoint、模型名和
API Key 环境变量必须与 EchoMem 的 `config.json` 一致：

```text
LLM endpoint:       https://dashscope.aliyuncs.com/compatible-mode/v1
LLM model:          deepseek-v4-flash-0731
Embedding endpoint: https://dashscope.aliyuncs.com/compatible-mode/v1
Embedding model:    text-embedding-v3
MCP:                关闭
Rerank:             关闭，除非本轮测试明确要求开启
```

示例环境变量如下，实际变量名以 `config.json` 中的 `api_key_env` 为准：

```bash
export ECHOMEM_LLM_API_KEY='你的模型 key'
export ECHOMEM_EMBEDDING_API_KEY='你的模型 key'
export ECHOMEM_ATOMIC_ENGINE_LLM_API_KEY="$ECHOMEM_LLM_API_KEY"
export ECHOMEM_EPISODE_ENGINE_LLM_API_KEY="$ECHOMEM_LLM_API_KEY"
export ECHOMEM_BASE_ENGINE_LLM_API_KEY="$ECHOMEM_LLM_API_KEY"
export ECHOMEM_MEMORY_UNIT_ENGINE_LLM_API_KEY="$ECHOMEM_LLM_API_KEY"
export ECHOMEM_INTENT_LLM_API_KEY="$ECHOMEM_LLM_API_KEY"
export ECHOMEM_MEMROUTER_LLM_API_KEY="$ECHOMEM_LLM_API_KEY"
```

不要把真实 API Key 写入 Git、README、日志或结果报告。

### 4. 准备独立租户凭据

公平性和隔离测试必须使用不同租户凭据，不能让所有租户共用一个 Key：

```bash
cp performance/targets/echomem/profiles/tenants.example.json /opt/echomem-stress/tenants.json
export ECHOMEM_TENANT_A_KEY='tenant-a 的 key'
export ECHOMEM_TENANT_B_KEY='tenant-b 的 key'
export ECHOMEM_TENANT_C_KEY='tenant-c 的 key'
export ECHOMEM_TENANT_D_KEY='tenant-d 的 key'
```

`tenants.json` 中的 `auth_key_env` 必须和当前 shell 中的变量对应。缺少独立
凭据时可以做单租户诊断，但不能据此下多租户公平性或隔离结论。

### 5. 先跑短检查

在完整测试前先跑一个 quick bounded smoke（单场景 `baseline`），确认地址、
租户凭据、模型环境和工作目录都正确。先把示例 profile 复制为实际配置，填写
真实 `tenant_config` / `preflight_config`，并按需删除或调整 `prepare_command`
（示例里的 `/usr/local/bin/echomem-select-profile` 是部署专用命令）：

```bash
cd /opt/Memory-System-Eval-Harness
cp performance/targets/echomem/profiles/instance-profiles.example.json \
  /opt/echomem-stress/instance-profiles.json
# 编辑 /opt/echomem-stress/instance-profiles.json：
#   tenant_config  -> 你复制出的 tenants.json
#   preflight_config -> /etc/echomem/4u8g/config.json（或删除该键跳过模型门禁）
#   prepare_command -> 删除或改为真实命令

export STRESS_OUTPUT_DIR=/opt/Memory-System-Eval-Harness/performance/targets/echomem/results/smoke-$(date +%Y%m%d_%H%M%S)

python3 -m performance.run --target echomem \
  --profiles /opt/echomem-stress/instance-profiles.json \
  --profile 4U8G \
  --scenarios baseline \
  --quick \
  --out-dir "$STRESS_OUTPUT_DIR"
```

必须看到 `objective-suite.json` 中该 profile 的
`profile_execution_status=completed`，且 `$STRESS_OUTPUT_DIR/4U8G/` 下生成了
`suite.json` 和 `baseline/summary.json`。

### 6. 执行 4U8G 完整测试

正式验收执行 `complete` 目录（26 例，含 7 小时 `soak`）。在服务器上以后台
方式运行并跟踪日志：

```bash
cd /opt/Memory-System-Eval-Harness
export STRESS_OUTPUT_DIR=/opt/Memory-System-Eval-Harness/performance/targets/echomem/results/4u8g-$(date +%Y%m%d_%H%M%S)

nohup python3 -m performance.run --target echomem \
  --profiles /opt/echomem-stress/instance-profiles.json \
  --profile 4U8G \
  --env-file /opt/echomem-stress/formal-run-4u8g.env \
  --timeout-s 7200 \
  --out-dir "$STRESS_OUTPUT_DIR" \
  >"$STRESS_OUTPUT_DIR/launcher.log" 2>&1 &
echo $! >"$STRESS_OUTPUT_DIR/launcher.pid"
```

不想等 7 小时 `soak` 时，用 `--scenarios` 显式挑选场景（例如只跑
`baseline,mixed,commit-barrier,saturation,tenant-skew,search-priority-blackbox,
capacity-2,capacity-4,capacity-8`）。`tenant-skew` 会一次提交 260 个 Commit，
单场景可能明显慢于普通场景；平台默认限制 barrier 同时在途数为 32，因此总样本
仍是 260 个，但不会把 260 个真实任务一次性压入 EchoMem。`--timeout-s` 是单个
case 的超时（默认 7200s），超时会记录为 `TIMEOUT`，不会伪装成 EchoMem 的业务
失败。

模型凭据如果放在 Docker env 文件中，用 `--env-file` 加载同一份文件（支持
`KEY=VALUE` 与 `export KEY=VALUE` 行，密钥不写入报告）。进入正式压测前会先
执行 `preflight_config` 对应的模型/配置门禁，再在灌种阶段用每个租户的真实
凭据调用 `POST /api/sessions/open`；任一租户失败都会提前停止本轮（见
`suite.json` 的 `preflight` / `seed` 段），不会输出「0 请求」的伪压测数据。

### 7. 查看进度和结果

```bash
tail -f "$STRESS_OUTPUT_DIR/launcher.log"
cat "$STRESS_OUTPUT_DIR/objective-suite.json"
cat "$STRESS_OUTPUT_DIR/4U8G/suite.json"
cat "$STRESS_OUTPUT_DIR/4U8G/acceptance.json"
find "$STRESS_OUTPUT_DIR" -name summary.json -type f | sort
```

最终应确认 `objective-suite.json` 中该 profile 的 `profile_execution_status`
为 `completed`；`4U8G/acceptance.json` 中的 `PASS`、`FAIL`、`INCONCLUSIVE`
要逐项查看，不能只看总准确率或退出码。

### 七项目标统一自动化入口

按实例规格逐个执行容量、稳定性、公平性、Search 优先级、Commit 恢复和
`/metrics` 可观测性检查、汇总 O1-O7 的入口就是上面的编排器
（`python -m performance.run --target echomem`），完整用法见
「性能压测」章节。服务器上先把
`performance/targets/echomem/profiles/instance-profiles.example.json` 复制为
实际 profile 配置，填写真实 `tenant_config`、`preflight_config` 和可选
`prepare_command`（示例里的 `/usr/local/bin/echomem-select-profile` 是部署
专用命令，普通环境应删除）。EchoMem 的真实模型凭据放在 Docker env 文件中时，
用 `--env-file` 加载同一份 env 文件（支持 `KEY=VALUE` 与 `export KEY=VALUE`
行，密钥不写入 `objective-suite.json`、HTML 或命令记录）。
`commit_recovery.tenant` 如果已经不在当前 `tenant_config` 中，入口会自动选用
该配置中的第一个租户，避免动态租户 ID 更新后仍因旧 profile 名称导致恢复探针
在启动阶段失败。

`--quick` 只做 bounded smoke（默认 7 例场景，barrier ≤32、容量档 Commit 置 0、
灌种降到每租户 1 会话），专门快速验证调度、延迟和可观测性链路，不能证明记忆
质量；`--quick-include-seed` 可把已有租户记忆纳入测试。少于 32 个真实 Commit
只能作为 smoke 数据，不能验收 O5 严格优先级；PR397 的完整 A/B/C/D 矩阵以及
大规模 barrier 不属于 quick，不能用 quick 结果替代完整验收。

正式数据去掉 `--quick`。O1 按活跃用户的 Search SLO 评估容量，Commit 洪泛由
O5 单独验收；容量档位超时只有在超时前已实际发出 Search 请求时才算边界，准备
阶段或 Commit 阶段卡住不能冒充 Search 容量上限。O1 只有在「Search 成功容量
档位 + 更高一档真实失败/超时/资源边界」同时存在时才会判定为 PASS；如果所有
已跑档位都成功，报告只给出「至少支持到 N」的容量下界并标记 `INCONCLUSIVE`，
不会把最后一个成功档位冒充最大用户量。O1 的「最大用户量」是压测窗口内完成的
容量阶梯上限，不直接等同于业务 DAU；O6 必须额外提供真实 container 重启和
cursor/message-set 对账配置，并在 `commit_recovery` 中设置
`"require_accepted_202": true`，否则没有在崩溃前明确收到 HTTP 202 的操作不能
进入恢复验收；O7 必须实际抓到服务端 `/metrics` 四元组。O4 会在
`search-priority-blackbox`、`tenant-skew` 等候选负载中选择租户覆盖最完整的一轮
计算公平性，避免 quick 模式的小 barrier 结果遮蔽更完整的真实证据；如果该轮
仍有租户没有 Commit 或 Search 样本，结果仍会保留为 `FAIL` 或 `INCONCLUSIVE`。

报告输出 `objective-suite.json` 和 `objective-suite.html`，不会把缺失证据算成
通过。profile 中配置 `capability_probe`、`commit_recovery`、`fault_plan` 后，
入口会自动执行真实 HTTP 能力探针、Commit 中途 kill-9 恢复探针和故障套件，并
把每个检查项写入 HTML 明细；`missing_cases` 和 `concurrent_commit` 会执行
PR397 的写后可见性/持久化对账、Commit 状态机、冷暖 Search 与并发 Commit 探针。
没有真实故障控制端点时，故障项必须显示 `INCONCLUSIVE`，不能用测试平台自身
缺少适配器来判定 EchoMem 未实现。如果只想重新审计已有结果而不重新发请求，
可在 profile 中填写 `suite_path`，然后执行
`python -m performance.run --target echomem --skip-run`；该模式只读取
已有 `suite.json` 和探针制品。

quick 的容量场景 Commit 负载置 0（`quick_commit_rpm=0`，只启动 Search
worker），保证容量档位测的是活跃用户的 Search 边界，而不是异步 Commit 的
完成时间。快速运行仍显示 `INCONCLUSIVE` 的常见原因不是 EchoMem 一定失败：

| 目标 | 还需要的真实证据 | 归属 |
|---|---|---|
| O1 | 至少一档成功的 `capacity-*`，再逐步增加租户直到 SLO 失败 | 测试平台场景与机器资源 |
| O2 | 至少两种规格实际启动并各自完成同一组场景 | 部署调度与测试平台 profile |
| O3 | 故障期间旁观租户的前后 Search P95 配对 | EchoMem/部署必须提供真实故障控制，平台负责采集 |
| O4 | 同一稳态窗口内至少两个租户同时有 Commit 吞吐和 Search P95 | 测试平台负载与独立租户凭据 |
| O5 | `search-priority-blackbox` 中同时存在 Commit 洪泛和 Search P95 | 测试平台场景，服务端负责实际调度 |
| O6 | 202 Commit、真实 kill/restart、history/archive/cursor/幂等重放对账 | 部署提供 kill/restart 权限，EchoMem提供既有读接口 |
| O7 | 每个实际 lane 都采到 queued/wait/exec/rejected 四元组，并有 engine fan-out 的 exec/skipped 证据；不要求把 `tenant_id` 放进指标标签 | EchoMem `/metrics` 暴露 bounded-label 指标，测试平台负责按 lane/fan-out 对账 |

公平性计算还要求被选中的同一负载窗口覆盖所有参与租户：每个租户都必须有
Search 样本和 Commit 提交样本。缺少某个租户时只报告 `INCONCLUSIVE`，不会把
没有到达或没有完成的租户从 Jain 分母中删除。

故障计划中的 `${BASE_URL}` 会被替换为 profile 当前地址，并写入运行目录的
`fault-plan.resolved.json`；不要再把旧服务器端口直接复制到通用示例中。

### 8. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `Connection refused :8010` | EchoMem 已退出或端口未监听 | 检查 `docker ps` 和 `/health` |
| `ModuleNotFoundError: performance` | 当前目录不是 Harness 根目录 | 先 `cd /opt/Memory-System-Eval-Harness` 再执行 `python -m performance...` |
| `suite.json` 的 `preflight` 段失败 | EchoMem 配置仍指向 fake 模型或模型凭据缺失 | 修正 `config.json` 和 `*_API_KEY`，或用 `--env-file` 加载真实凭据 |
| `suite.json` 的 `seed` 段 `ENV_ERROR` | 租户凭据错误（401）、接口错误或网络超时 | 检查 `tenants.json` 与 `ECHOMEM_TENANT_*_KEY` 环境变量，查看错误信息 |
| 长时间停在 `tenant-skew` | 260 个 Commit 屏障等待或服务异常 | 停止本轮，查看场景 `summary.json`，缩短 case timeout 后重跑 |
| 只有 `suite.json` 没有场景结果 | 首个场景前退出或目标服务不可达 | 查看 `launcher.log` 和 `4U8G/*/summary.json` |

结果建议只保留 3 天：

```bash
find /opt/echomem-stress/results -mindepth 1 -maxdepth 1 \
  -type d -mtime +3 -exec rm -rf -- {} +
```

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
