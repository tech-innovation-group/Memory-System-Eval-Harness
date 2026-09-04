# Memory-System-Eval-Harness

记忆系统评测框架。全 CLI，无网页 UI。直接通过 Python 脚本完成数据集加载、
记忆注入、Agent 问答、Judge 评分和结果报告。

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
- **断点续跑**：QA 和 Judge 均支持 `--resume-qa` / `--resume-judge`，健康行不
  重复调用模型；`--checkpoint-interval` 定期落盘部分结果。

### 3. 简单易用 / AI 入口

直接 Python 调用，CLI 参数即配置，AI 友好。

- **直接启动**：`python benchmarks/<name>/run_eval.py` 或
  `python dynamic/run_eval.py`，一条命令完成全流程，无需额外包装层。
- **CLI 参数驱动**：所有连接地址、模型配置、记忆后端、插件选择通过 CLI 参数
 传入，可写在 `.bat` / `.sh` 脚本中固化。环境变量作为默认值，CLI 参数覆盖。
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
- **身份隔离**：每次评测新开独立 tenant / user / agent 身份，`--resume-qa` 时
  复用原有身份。身份信息（account / user_id / auth_key）记录在 resume manifest
  中，auth key 仅掩码保存。
- **数据完整性校验**：LoCoMo 在 QA 前校验数据集 SHA-256 和实际 session
  manifest，session 数量不匹配时拒绝运行，防止复用 tenant 被污染。
- **生产管线**：动态评测的 QA 阶段走 EchoAgent 完整 HTTP 管线，含 prefill /
  typing simulation / TTFT 采集，与线上行为一致。

## 目录结构

```
plugins/                     # Agent 插件 (AgentPlugin 协议)
  base.py                    #   AgentPlugin ABC + AgentResponse / TypingResult
  registry.py                #   按名动态加载，无需手动注册
  bare_llm/                  #   纯 LLM 基线 (无记忆检索)
  echo_agent/                #   EchoAgent + EchoMem 完整管线 (动态评测默认)
  vikingbot/                 #   VikingBot 工具调用 agent (LoCoMo 默认)
  echomem_mcp/               #   LLM 通过 EchoMem MCP 工具检索记忆
  openviking_mcp/            #   LLM 通过 MemoryClient 工具检索记忆
backends/                    # 记忆后端客户端
  memory_types.py            #   MemoryClient 协议 + BaseHTTPMemoryClient + NullMemoryClient
  memory_args.py             #   add_memory_backend_args() -- 后端连接 CLI 参数
  echomem/                   #   EchoMemClient (端口 8010)
  openviking/                #   OpenVikingClient (端口 19080)
benchmarks/                  # 静态数据集评测
  locomo/                    #   LoCoMo: LLM Judge (CORRECT/WRONG)
    run_eval.py              #     入口脚本
    dataset.py               #     数据集加载与解析
    import_memory.py         #     记忆导入
    qa.py                    #     QA 任务构建与执行
    judge.py                 #     LLM Judge
    reporting.py             #     结果汇总
    data/                    #     内置 locomo10.json
    results/                 #     运行结果
  hotpotqa/                  #   HotpotQA: F1/EM 官方指标
  longmemeval/               #   LongMemEval: LLM yes/no accuracy
  doc/                       #   benchmark 通用文档
dynamic/                     # 动态评测 (generate / replay)
  run_eval.py                #   入口脚本
  workflows.py               #   generate / replay 工作流
  simulator.py               #   场景与查询生成
  metrics.py                 #   动态指标和多维质量评估
  artifacts.py               #   JSON/CSV/报告输出
  model_client.py            #   动态 LLM 客户端
  prompt_config.py           #   prompt 配置加载
  configs/                   #   evaluator / user_simulator YAML 配置
  results/                   #   运行结果
performance/                 # 性能压测（多租户并发读写、注入/检索延迟、CPU/RSS）
  run_stress.py              #   入口脚本
  prepare.py                 #   租户准备 + 种子注入 + query 池
  loadgen.py                 #   读写负载注入器 + 逐请求埋点
  monitor.py                 #   /metrics 周期采样 + Prometheus 文本解析
  scenarios.py               #   场景矩阵（A 纯读 / B 纯写 / C 混合 / D 洪峰）
  metrics_calc.py            #   统计纯函数
  report.py                  #   产物与自包含 HTML 报告
  acceptance.py              #   PR421 验收门禁求值器（纯函数，消费已落盘制品）
  formal_suite.py            #   正式多租户验收套件编排（子进程跑 run_stress）
  formal_data_report.py      #   套件数据报告（suite.json → suite.html）
  probes/                    #   故障/恢复/限流/对账探针（独立 CLI，真实 HTTP）
  tenants.example.json       #   租户凭据示例
  instance-profiles.example.json  # 机器规格 profile 示例
  results/                   #   运行结果

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

### 4U8G 六项指标压测入口

PR29 的六项指标压测使用真实 EchoMem HTTP，不使用 mock 模型替代容量或延迟结论。
测试平台默认使用合成可控语料：每个租户先走真实
`open -> add -> commit -> poll completed`，再用 recall / no-recall 查询压测。
正式运行默认关闭 `soak`，不会把长稳态测试混进日常验收。

详细测试方案见
[`docs/4u8g-six-metric-stress-plan.md`](docs/4u8g-six-metric-stress-plan.md)，里面
说明了每项指标测试什么、为什么测试、请求如何产生、通过什么数据判定，以及哪些
情况下只能得到 `INCONCLUSIVE`。六项指标对应：

1. 单实例最大 DAU / 最大热用户量；
2. 任意单租户故障时，其他租户 Search P95 的劣化百分比；
3. 不同租户之间分别计算 Commit 吞吐 Jain 和 Search 延迟效用 Jain；
4. Commit 洪泛期间 Search 的优先级和 P95/P99；
5. HTTP 202 Commit 在 kill/restart 后的完成、重放、顺序和幂等对账；
6. `/metrics` 中每个调度层的 `queued/wait/exec/rejected` 四元组。

#### 1. 准备真实服务和配置

EchoMem 服务必须已经启动，并且 `ECHOMEM_CONFIG` 指向服务实际使用的
`config.json`。压测租户配置至少准备 32 个租户；公平性、故障隔离和容量场景
需要独立凭据，不能让多个租户共用同一个 API key。可从
`performance/instance-profiles.example.json` 复制一份部署配置，至少填写：

- `base_url`：EchoMem HTTP 地址；
- `tenant_config`：独立租户凭据 JSON；
- `preflight_config`：实际生效的 EchoMem `config.json`；
- `commit_recovery`：容器/PID 和重启控制；
- `fault_isolation` / `fault_plan`：真实故障控制（没有就会是 `INCONCLUSIVE`）；
- `metrics_enabled`：是否能访问 `/metrics`。

#### 2. 运行完整六指标验收

```bash
git clone https://github.com/tech-innovation-group/Memory-System-Eval-Harness.git
cd Memory-System-Eval-Harness
git fetch origin v3_mcpTool
git checkout v3_mcpTool

export ECHOMEM_CONFIG=/etc/echomem/4u8g/config.json
export STRESS_PROFILES=/etc/echomem/performance-profiles.json
export ECHOMEM_BASE_URL=http://127.0.0.1:8010
export STRESS_PROFILE_NAME=4U8G
export STRESS_PYTHON=python3  # runner 容器内使用 Python >= 3.9；也可填绝对路径

./performance/run_4u8g_six_metrics.sh
```

如果宿主机的 `python3` 低于 3.9，不要降级或改写 Harness 语法；把 Harness
挂载到已有 runner 容器中执行，或设置 `STRESS_PYTHON` 为 runner 内的 Python
路径。宿主机只负责启动 EchoMem 和保存结果，避免旧解释器把环境问题误报为
业务测试失败。

如果 EchoMem 已经由部署编排固定在 4U8G 容器中，而 profile 里的
`prepare_command` 只是宿主机上的实例切换命令，可使用：

```bash
export STRESS_SKIP_PREPARE=1
./performance/run_4u8g_six_metrics.sh
```

这只跳过“切换实例规格”动作，不会跳过健康检查、真实 HTTP 请求、模型
preflight、`/metrics` 采集或六项目标判定。也可以直接运行某个场景做回归：

```bash
python3 -m performance.objective_suite \
  --profiles performance/instance-profiles.example.json \
  --profile 4U8G \
  --scenarios fairness-steady \
  --skip-prepare \
  --out-dir results/fairness-regression
```

完整 4U8G profile 会自动把公开场景名转换成正式套件内部的
`pr421__<scenario>` 名称；调用者不需要手写命名空间。

脚本会调用 `performance.objective_suite`，只运行 4U8G profile；完整模式默认
使用 PR397/report(6) 与 PR421 的场景目录，soak 不在默认场景中。每个场景会
等待服务健康、执行真实 HTTP 请求、采集 `/metrics`、保存逐请求数据，并在结束
后生成六项目标验收结果。

#### 3. 先做快速诊断

```bash
export STRESS_QUICK=1
./performance/run_4u8g_six_metrics.sh
```

快速模式会缩短场景和 Commit barrier，只用于检查服务、凭据、配置和基本请求
链路，不足以替代完整容量、公平性或崩溃恢复结论。

#### 4. 查看结果

```text
<输出目录>/objective-suite.html       总体 HTML 报告
<输出目录>/objective-suite.json       六项目标和覆盖状态
<输出目录>/4U8G/suite.json            场景总清单
<输出目录>/4U8G/formal/**/summary.json 每个场景的摘要
<输出目录>/4U8G/formal/**/requests.csv 逐请求耗时、状态、租户和召回断言
<输出目录>/4U8G/formal/**/metrics_samples.csv
                                        服务端 /metrics 原始采样
```

逐请求 `requests.csv` 还会记录 `start_ts_ms` 和 `ts_ms`：前者是请求开始时间，
后者是完成时间。Search 优先级场景会用这两个时间组成请求区间，只有 Search 与
Commit 区间真实重叠时，才允许继续判断洪泛期间的 Search P95；不能用“两个请求都
完成过”代替同时竞争证据。

重点检查 `objective-suite.json` 中的 `coverage` 和六个 `O1` 到 `O6`：

- `PASS`：有足够真实请求和对应证据，并满足阈值；
- `FAIL`：真实证据表明阈值未满足；
- `INCONCLUSIVE`：没有真实故障控制、kill/restart、独立凭据或服务端指标等必要证据；
- 场景进程退出成功但 `evidence_runs` 不足时，仍不能当作测试完成。

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
  检索阶段不写入记忆。支持 `--resume-qa` 断点续跑。
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

### Benchmark 评测

直接调用 `benchmarks/<name>/run_eval.py`，通过 `--agent-plugin` 选择被测 agent，
通过 `--memory-backend` 选择记忆后端。

#### LoCoMo + echomem_mcp（EchoMem 后端）

<div style="color: red;">

无工具调用时，测试平台仍通过 EchoMem MCP 执行每题的初始 `memory_query`：

<pre style="color: red;"><code class="language-bash">./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8010 \
  --mcp-url http://127.0.0.1:8001 \
  --sample conv-30 \
  --no-tool-calling \
  --mcp-read-mode disabled \
  --concurrency 4 \
  --judge-concurrency 4 \
  --top-k 25 \
  --memory-budget-chars 8000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --llm-temperature 0.7 \
  --question-timeout-s 600 \
  --llm-timeout-s 600 \
  --llm-retries 3</code></pre>

允许模型通过 MCP 调用工具，但禁止读取 `messages.jsonl`：

<pre style="color: red;"><code class="language-bash">./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8010 \
  --mcp-url http://127.0.0.1:8001 \
  --sample conv-30 \
  --tool-calling \
  --mcp-read-mode disabled \
  --concurrency 4 \
  --judge-concurrency 4 \
  --top-k 25 \
  --memory-budget-chars 8000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --llm-temperature 0.7 \
  --question-timeout-s 600 \
  --llm-timeout-s 600 \
  --llm-retries 3</code></pre>

允许模型通过 MCP 调用工具，并允许读取 `messages.jsonl`：

<pre style="color: red;"><code class="language-bash">./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8010 \
  --mcp-url http://127.0.0.1:8001 \
  --sample conv-30 \
  --tool-calling \
  --mcp-read-mode allow \
  --concurrency 4 \
  --judge-concurrency 4 \
  --top-k 25 \
  --memory-budget-chars 8000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --llm-temperature 0.7 \
  --question-timeout-s 600 \
  --llm-timeout-s 600 \
  --llm-retries 3</code></pre>

</div>

#### LoCoMo + vikingbot（OpenViking 后端）

```bash
python benchmarks/locomo/run_eval.py \
  --agent-plugin vikingbot \
  --memory-backend openviking \
  --echomem-url http://127.0.0.1:19080 \
  --workspace D:/.openviking/data \
  --sample conv-30 \
  --questions 0 \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash-0731 \
  --llm-api-key YOUR_KEY \
  --commit-timeout-s 0 \
  --question-timeout-s 0 \
  --llm-timeout-s 600
```

#### 断点续跑

```bash
./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8010 \
  --mcp-url http://127.0.0.1:8001 \
  --sample conv-30 \
  --no-tool-calling \
  --resume-qa benchmarks/locomo/results/20260803_143943_618591 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --question-timeout-s 600 \
  --llm-timeout-s 600 \
  --llm-retries 3
```

#### 其他 benchmark

```bash
# HotpotQA
python benchmarks/hotpotqa/run_eval.py \
  --agent-plugin bare_llm \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash-0731 \
  --llm-api-key YOUR_KEY \
  --questions 10

# LongMemEval
python benchmarks/longmemeval/run_eval.py \
  --agent-plugin bare_llm \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash-0731 \
  --llm-api-key YOUR_KEY \
  --questions 10
```

| Benchmark | 默认插件 | 评测方式 | 数据集 |
|---|---|---|---|
| `locomo` | `vikingbot` | LLM Judge (CORRECT/WRONG) | 内置 `locomo10.json` |
| `hotpotqa` | `bare_llm` | F1/EM 官方指标 | 需设置 `HOTPOTQA_DATASET` |
| `longmemeval` | `bare_llm` | LLM yes/no accuracy | 需设置 `LONGMEMEVAL_DATASET` |

结果写入 `benchmarks/<name>/results/<timestamp>/`，主要文件：`qa_results.csv`、
`judge_results.csv`、`summary.json`、`config.json`、`agent_traces/`、
`backend_logs.json`。

### 动态评测

直接调用 `dynamic/run_eval.py`。需先启动 EchoAgent Backend（端口 31020）和
EchoMem（端口 8010）。

#### Generate 模式

LLM 生成场景和提问，端到端走 EchoAgent 完整管线：

```bash
python dynamic/run_eval.py \
  --echoagent-url http://127.0.0.1:31020 \
  --memory-engine-endpoint http://127.0.0.1:31030 \
  --echomem-url http://127.0.0.1:8010 \
  --username test_user \
  --password YOUR_PASSWORD \
  --num-memories 5 \
  --num-queries 5 \
  --new-session-ratio 0.3 \
  --typing-speed-ms 2 \
  --scenario-model deepseek-v4-flash-0731 \
  --scenario-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --scenario-api-key YOUR_KEY \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash-0731 \
  --llm-api-key YOUR_KEY
```

#### Replay 模式

回放已有数据集对话，测试跨 session 召回：

```bash
python dynamic/run_eval.py \
  --echoagent-url http://127.0.0.1:31020 \
  --memory-engine-endpoint http://127.0.0.1:31030 \
  --echomem-url http://127.0.0.1:8010 \
  --username test_user \
  --password YOUR_PASSWORD \
  --dataset dynamic/results/20260728_175544/dataset.json \
  --new-session-ratio 0.3 \
  --typing-speed-ms 2 \
  --scenario-model deepseek-v4-flash-0731 \
  --scenario-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --scenario-api-key YOUR_KEY \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash-0731 \
  --llm-api-key YOUR_KEY
```

结果写入 `dynamic/results/<timestamp>/`，主要文件：`dataset.json`、
`dynamic_results.csv`、`summary.json`、`quality_report.json`。

#### 双后端对比（EchoMem vs OpenViking）

同 agent 隔离口径下对比两个记忆后端：先 `generate` 一次产出场景
`dataset.json`，再用 `replay` 把**同一份**数据集对两个后端各回放一遍
（vikingbot 插件通过 `--memory-backend` 切换后端），最后生成自包含 HTML
图表报告：

```bash
# 1) generate 一次：LLM 模拟用户生成背景记忆 + 查询
python dynamic/run_eval.py --agent-plugin vikingbot --memory-backend echomem \
  --echomem-url http://127.0.0.1:8010 --num-memories 20 --num-queries 50 \
  --scenario-base-url ... --scenario-model ... --scenario-api-key ... \
  --llm-base-url ... --llm-model ... --llm-api-key ... \
  --out-dir dynamic/results/formal_gen

# 2) replay 同一份 dataset.json 到 EchoMem
python dynamic/run_eval.py --agent-plugin vikingbot --memory-backend echomem \
  --echomem-url http://127.0.0.1:8010 --dataset <dataset.json> \
  --llm-base-url ... --llm-model ... --llm-api-key ... --out-dir dynamic/results/formal_em

# 3) replay 同一份 dataset.json 到 OpenViking
python dynamic/run_eval.py --agent-plugin vikingbot --memory-backend openviking \
  --echomem-url http://127.0.0.1:19080 --workspace D:/.openviking/data \
  --dataset <dataset.json> --llm-base-url ... --llm-model ... --llm-api-key ... \
  --out-dir dynamic/results/formal_ov

# 4) 生成对比报告（token / 注入耗时 / 检索延迟 / 召回精度 / 答案质量）
python scripts/compare_memory_backends.py \
  --echomem-run dynamic/results/formal_em/<run> \
  --openviking-run dynamic/results/formal_ov/<run> \
  --dataset <dataset.json> --output reports/echomem_vs_openviking/index.html
```

`replay` 会自动识别 `generate` 产出的动态 v2 `dataset.json`
（含 `background_memories` + `dataset_queries`），保留每轮 `ground_facts`
的记忆 id，供召回精度计算；注入耗时记录在结果的 `config.inject_elapsed_s`。
一键流程见 `START_BAT/compare_echomem_vs_openviking.bat`。

## 性能压测

对运行中的 EchoMem 服务做多租户高并发**读写性能**压测（不需要 LLM）：检索
吞吐/延迟（客户端 + 服务端 `/metrics` 双视角）、注入四段延迟（open / add /
commit 提交 / commit 完成）、读写混合与「注入洪峰」下的劣化（**检出"注入阻塞
检索"**）、进程 CPU/RSS/线程/commit 队列水位。设计见
`docs/performance-stress-test-design.md`。

压测同时验明 EchoMem 的**四项特性保证**（见 `summary.json` 的
`signals` / `commit_durability` / `tenant_fairness` / `resources.rss_trend`）：

1. **commit 异步与成功保证**：202 接受后最终必须 completed；提交失败不重试
   （客户端 `max_retries=0`，失败分类输出）；commit 不阻塞检索——D 场景洪峰窗口
   读 P95 劣化超过阈值（默认 2x）即报信号。
2. **租户公平性**：按场景×租户分组读延迟，租户间 P95 max/min ≥ 3x 报不均衡信号。
3. **无内存泄漏**：RSS 时间序列最小二乘斜率 ≥ 5 MB/min 或冷却后未回落显著，报
   疑似泄漏信号。
4. **资源利用率随时间变化**：`report.html` 以独立子图展示 CPU%/RSS/线程/commit
   队列/inflight 的全过程曲线；`metrics_samples.csv` 含原始时序。

运行结束后 `report.html` 顶部与终端摘要会给出**逐特性结论**（通过 / 不通过 /
数据不足）与总体结论，判定依据含数据引用（见 `summary.json["feature_verdicts"]`）。

除结论外，报告还给出**特性量化分析**（`report.html`「特性量化分析」小节、
`summary.json["feature_verdicts"].features[*].measurements`、终端结论行内），
把「是否满足」扩展为「满足到什么程度」：写洪峰时 search 的 P95 比基线高多少
（绝对毫秒差 + 倍率）、最慢租户比最快租户多等的时长、RSS 增长率与每小时外推、
CPU/内存的均值与峰值。

```bash
# 快速冒烟（并发档 1,16，时长 15s）
python performance/run_stress.py --quick --tenants 4 --duration-s 60

# 全矩阵（并发档 1,4,16,64 x A/B/C/D；C 场景读:写比 8:1,4:1,1:1）
python performance/run_stress.py

# 长期满负荷（检验泄漏/公平性，建议 --duration-s 300+）
python performance/run_stress.py --duration-s 300 --scenarios A,B,D

# 外网部署：任意 IP:端口 + 静态预置身份（不创建租户）+ metrics 不可达降级
python performance/run_stress.py \
  --echomem-url http://203.0.113.10:8010 \
  --auth-mode static --auth-key XXX --tenant-id T1 --user-id U1 \
  --tenants 1 --scenarios A,D --concurrency-steps 1,8 --duration-s 30

# 只跑纯读基线 + 注入洪峰
python performance/run_stress.py --scenarios A,D --concurrency-steps 1,16,64

# 真实对话种子：locomo conv-30 复制灌入 8 个租户，压测结束后自动清理租户
python performance/run_stress.py --tenants 8 --seed-source locomo \
  --sample-filter conv-30 --cleanup-identities
```

场景说明：`A` 纯读基线（劣化对照）· `B` 纯写注入（四段延迟 + 写后读一致性 +
commit 成功保证）· `C` 读写混合（多档 read:write）· `D` 注入洪峰（读持续 +
突发 K 个 commit，检出 search-commit 干扰与读写数据倾斜）。

`performance/` 还提供两条互补路径（设计见
`docs/performance-stress-test-design.md` §3.8–3.12）：

### 调度专项六项 4U8G 验收

截图中的调度要求使用 `scheduler_acceptance.py` 单独验收，不把普通
A/B/C/D 压测结果当作专项结论。4U8G 本轮按以下六项分别输出 `PASS`、`FAIL` 或
`INCONCLUSIVE`：DAU/热用户容量、单租户故障隔离、Jain 公平性、Search 优先级、
Commit kill-9 恢复重放、分层调度可观测性。多规格对比保留为附加诊断，不计入
本轮六项总体判定。

```bash
python -m performance.scheduler_acceptance \
  --suite results/performance/formal_<ts>/suite.json \
  --capability results/performance/probes/capability-probe.json \
  --recovery results/performance/probes/recovery.json \
  --fault results/performance/probes/fault-suite.json \
  --out results/performance/probes/scheduler-acceptance.json
```

缺少故障控制、重启控制或多规格实测时，报告保留 `INCONCLUSIVE`，不会
根据客户端延迟或 HTTP 200 推断 EchoMem 已实现对应保证。

本次 PR29 的专项验收口径已收紧：容量项必须有真实完成请求且 Search/Commit
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

正式压测会先执行真实租户鉴权门禁：对本轮选中的租户调用
`POST /api/sessions/open`。任何一个租户返回 `401`、连接失败或配置缺失，
都会在场景启动前生成 `auth-preflight.json` 并停止本轮，不再输出“0 请求”的
伪压测数据。该文件只保留租户名、状态码、耗时和 key 的 SHA-256 前缀，不保存密钥。
如果部署使用单一本地身份，可显式传入 `--local-auth`。

如果正式套件在场景启动前显示“无法测试”，先看结果目录下的
`auth-preflight.json`、每个 case 的 `command.json` 和
`suite_runner.stderr.log`。`command.json` 会记录实际使用的是
`tenant_config` 还是 `local_auth`，不会再把默认 `auth_mode=provision`
误认为本轮真的重新创建了租户；HTTP 失败会保留状态码、请求路径和截断后的服务响应，
便于区分凭据错误（401）、接口错误（4xx）、服务异常（5xx）和网络超时。
这类前置失败应归为测试环境/配置问题，不能直接判定 EchoMem 功能失败。
如果服务器只注册了部分租户，可以在 profile 中设置
`"allow_partial_tenants": true`（或给 `formal_suite.py` 传
`--allow-partial-tenants`）。平台会只运行租户数足够的场景；例如只有 1 个有效租户
时仍会执行 `baseline`，而 2/4/8 租户场景会记录为 `blocked`，公平性、故障隔离和
多租户容量保持 `INCONCLUSIVE`。这只是让可运行证据先产出，不会把单租户结果冒充
多租户通过。
另外，`--skip-seed` 只表示不重新灌入模型数据，并不会自动从 EchoMem 读取历史查询词。
正式复用已有记忆时请传入 `--search-queries "关键词1,关键词2"`；若省略，平台会使用
`hello` 作为 fallback，并在 `summary.json.data_scale.query_source` 标记为
`default_fallback`。这种运行可以验证服务调度和延迟，但不能替代真实记忆质量测试。

- **正式验收套件**（`formal_suite.py`）：以子进程方式逐 case 重跑
  `run_stress.py`（`report6` / `pr421` / `complete` 三档场景目录），把原生产物
  推导成 `acceptance.py` 验收门禁（8 个 gate：search 成功率 / report6 质量 /
  隔离 / 公平 / commit 完成 / 拒绝 / hot tenant / 容量阶梯）消费的契约摘要，
  产出 `suite.json` / `acceptance.json` / `model_analysis_input.json` /
  `suite.html`。只有每次运行都用独立租户凭据才允许做出上线结论。
- **故障 / 恢复 / 限流 / 对账探针**（`probes/`）：独立 CLI，直接以真实 HTTP
  访问 EchoMem；只在部署方显式提供故障/恢复控制时才执行真实操作，否则如实上报
  `INCONCLUSIVE`，显式 404 是「未实现」的唯一证据。

单台 4U8G 机器可以使用 `4u8g` 档快速诊断；需要完整数据时使用
`4u8g-full`。完整档会分别执行 PR397/report(6) 的 12 个场景和 PR421
的 25 个场景，共 37 个执行项，重复场景也会分别保留，不能用并集数量替代。
快速档只执行
能在一台服务实例上快速得到黑盒证据的场景：单租户基线、均衡混合、Commit
屏障、饱和、热租户偏斜、Search/Commit 同时到达，以及 2/4/8 租户容量阶梯；
不包含 7 小时 `soak`、第二种实例规格，也不会伪造 kill-9 或依赖故障控制结果。

```bash
# 4U8G 快速诊断：每场景最多 15 秒，优先级/公平性 barrier 至少保留 32 个请求，单轮
python -m performance.formal_suite \
  --profile 4u8g --quick-mode \
  --base-url http://127.0.0.1:8010 \
  --tenant-config performance/tenants-32.server.json \
  --preflight-config /etc/echomem/4u8g/config.json \
  --instance-profile 4U8G \
  --repeats 1 \
  --out-dir results/performance/4u8g-quick
```

`--quick-mode` 只缩短测试窗口，不改变验收口径：没有真实 Search/Commit
样本、第二种实例规格、真实重启/故障控制或服务端指标时，结果仍明确记为
`INCONCLUSIVE`，并在 `suite.json` / `acceptance.json` 记录缺失证据及归属
（测试平台、部署配置或 EchoMem 服务端）。因此“无法测试”不等同于
“EchoMem 功能失败”。

```bash
# 4U8G 完整黑盒矩阵：PR397 12 项 + PR421 25 项，共 37 项；不含 soak
python -m performance.formal_suite \
  --profile 4u8g-full \
  --base-url http://127.0.0.1:8010 \
  --tenant-config performance/tenants-32.server.json \
  --preflight-config /etc/echomem/4u8g/config.json \
  --instance-profile 4U8G \
  --repeats 1 \
  --out-dir results/performance/4u8g-full
```

```bash
# 正式验收套件（默认 pr421 场景目录，3 轮）
python -m performance.formal_suite \
  --base-url http://127.0.0.1:8010 \
  --tenant-config performance/tenants.example.json --repeats 3

种子数据准备默认按最多 4 个租户并行执行，以缩短真实模型 commit 的准备时间；
种子阶段不计入压测窗口。可通过 `--seed-concurrency N` 调整，正式负载阶段仍
按场景配置独立控制并发。

# 探针：真实限流阶梯扫描
python performance/probes/limit_failure_sweep.py \
  --base-url http://127.0.0.1:8010 \
  --tenant-config performance/tenants.example.json \
  --session-root <session_root> --out-dir results/performance/probes
```

单实例 4U8G 的完整验收使用 `performance/run_4u8g_complete.sh`；默认单轮执行
PR397/report(6) 与 PR421 的完整场景并集，不执行 `soak`，也不启动 4U16G：

```bash
export ECHOMEM_BASE_URL=http://127.0.0.1:8010
export ECHOMEM_CONFIG=/etc/echomem/4u8g/config.json
export STRESS_TENANT_CONFIG=/opt/echomem-stress/tenants-32.generated.json
export STRESS_OUTPUT_DIR=/opt/echomem-stress/results/4u8g-complete-$(date +%Y%m%d_%H%M%S)
./performance/run_4u8g_complete.sh
```

结果写入 `performance/results/<ts>/`：`summary.json`（按场景×并发档分节的延迟/
吞吐/错误/资源/劣化倍数）、`requests.csv`（逐请求）、`metrics_samples.csv`
（服务端采样时序）、`report.html`（自包含报告）。正式套件结果写入
`results/performance/formal_<ts>/`：`suite.json` / `acceptance.json` /
`model_analysis_input.json` / `summary.json` / `suite.html`，每个 case 的
`run/` 保留 run_stress 原生产物。

| 参数 | 说明 | 默认 |
|---|---|---|
| `--echomem-url` | 目标服务地址（IP:端口可配，含外网） | `http://127.0.0.1:8010` |
| `--auth-mode` | `provision` 自助创建租户 / `static` 预置身份 | `provision` |
| `--tenants` × `--concurrency-steps` | 租户数 × 每租户并发阶梯 | 8 × `1,4,16,64` |
| `--scenarios` | 场景过滤 | `A,B,C,D` |
| `--mix-ratios` | C 场景读:写比档位 | `8:1,4:1,1:1` |
| `--burst-commits` / `--burst-window-s` | D 场景洪峰事务数 / 窗口 | 32 / 10 |
| `--duration-s` | 每场景每并发档时长 | 60 |
| `--seed-source` | 种子数据源：`synthetic` 合成锚词消息 / `locomo` 真实对话 | `synthetic` |
| `--dataset-path` | locomo 数据集路径（仅 `--seed-source locomo`） | `benchmarks/locomo/data/locomo10.json` |
| `--sample-filter` | locomo 样本过滤器（单个 / 逗号分隔多个 / `all`） | `conv-30` |
| `--no-metrics` / `--skip-health` | 外网降级：不抓 /metrics、跳过预检 | 关 |
| `--cleanup-identities` | 压测结束后删除 provision 租户（身份 + 会话/记忆数据全清；static 模式拒绝） | 关 |

## 服务器测试指南

本节用于团队成员在 Linux 服务器上测试已经启动的 EchoMem。推荐把 EchoMem
和 Harness 放在同一台机器上：EchoMem 只监听 `127.0.0.1:8010`，Harness
通过本机或 Docker host network 访问，不需要把 EchoMem API 暴露到公网。

### 1. 拉取测试平台

使用测试平台 PR29 的 `v3` 分支：

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
cp performance/tenants.example.json /opt/echomem-stress/tenants.json
export ECHOMEM_TENANT_A_KEY='tenant-a 的 key'
export ECHOMEM_TENANT_B_KEY='tenant-b 的 key'
export ECHOMEM_TENANT_C_KEY='tenant-c 的 key'
export ECHOMEM_TENANT_D_KEY='tenant-d 的 key'
```

`tenants.json` 中的 `auth_key_env` 必须和当前 shell 中的变量对应。缺少独立
凭据时可以做单租户诊断，但不能据此下多租户公平性或隔离结论。

### 5. 先跑短检查

在完整测试前先跑一个 10 秒基线，确认地址、凭据、模型和工作目录都正确：

```bash
export ECHOMEM_BASE_URL=http://127.0.0.1:8010
export ECHOMEM_CONFIG=/opt/echomem-stress/workspace/config.json
export STRESS_TENANT_CONFIG=/opt/echomem-stress/tenants.json
export STRESS_OUTPUT_DIR=/opt/echomem-stress/results/smoke-$(date +%Y%m%d_%H%M%S)

python3 -m performance.formal_suite \
  --base-url "$ECHOMEM_BASE_URL" \
  --tenant-config "$STRESS_TENANT_CONFIG" \
  --preflight-config "$ECHOMEM_CONFIG" \
  --profile pr421 \
  --scenarios baseline \
  --repeats 1 \
  --duration-cap-s 10 \
  --case-timeout-s 120 \
  --commit-timeout-s 60 \
  --out-dir "$STRESS_OUTPUT_DIR"
```

必须看到：

```text
FORMAL_PROGRESS 1/1 scenario=baseline repeat=1 policy=server-observe status=completed
```

### 6. 执行 4U8G 完整测试

默认执行 PR397/report(6) 的 12 个场景与 PR421 的 25 个场景，共 37 个
bounded 场景，单轮、不执行 `soak`，只测试 4U8G：

```bash
cd /opt/Memory-System-Eval-Harness
export ECHOMEM_BASE_URL=http://127.0.0.1:8010
export ECHOMEM_CONFIG=/opt/echomem-stress/workspace/config.json
export STRESS_TENANT_CONFIG=/opt/echomem-stress/tenants-32.json
export STRESS_REPEATS=1
export STRESS_CASE_TIMEOUT_S=180
export STRESS_COMMIT_TIMEOUT_S=600
export STRESS_OUTPUT_DIR=/opt/echomem-stress/results/4u8g-$(date +%Y%m%d_%H%M%S)
mkdir -p "$STRESS_OUTPUT_DIR"

nohup ./performance/run_4u8g_complete.sh \
  >"$STRESS_OUTPUT_DIR/launcher.log" 2>&1 &
echo $! >"$STRESS_OUTPUT_DIR/launcher.pid"
```

`tenant-skew` 会一次提交 260 个 Commit，单场景可能明显慢于普通场景；
平台默认限制 barrier 同时在途数为 32（可用 `STRESS_BARRIER_WAVE_SIZE` 调整），
因此总样本仍是 260 个，但不会把 260 个真实任务一次性压入 EchoMem。
`STRESS_CASE_TIMEOUT_S=0` 表示按场景时长 + Commit 轮询预算自动计算；只有诊断时
才建议手动设置较小的超时。超时会记录为 `TIMEOUT`，不会伪装成 EchoMem 的业务失败。
正式套件的共享 seed warmup 默认使用 2 个租户并发、600 秒 Commit 终态等待；
这是为了避免真实模型 Commit 在多个租户同时灌种时排队过长。seed 只为需要
热缓存/记忆质量证据的场景准备，容量阶梯不会因为容量档位较大而额外灌种
16/32 个租户。seed 失败时，平台仍会执行容量、调度、恢复和 `/metrics` 等
不依赖预置记忆的真实黑盒场景；相关热缓存/记忆质量结论记录为 `INCONCLUSIVE`，
不会把零请求包装成“成功”，也不会把 seed 故障连带成整套 `BLOCKED`。
在这种无 seed 的运行里，正式 case 会自动给 `run_stress` 增加
`--allow-unverified-search`，让真实 Search 请求继续执行；空结果不会被当作
召回成功，`data_scale.search_evidence_status` 会保留证据缺口。

服务器系统 Python 低于 3.9，或没有 Harness 依赖时，必须使用 runner 镜像。
该镜像的默认 entrypoint 是旧版 `runner.py`，执行 PR29 的完整套件时要显式覆盖
entrypoint 为 `bash`，否则参数会被旧 runner 吃掉：

```bash
python3 performance/prepare_docker_env.py \
  /opt/echomem-stress/tenant_keys.env \
  /opt/echomem-stress/tenant_keys.docker.env

docker run --rm --network host --entrypoint bash \
  --env-file /opt/echomem-stress/tenant_keys.docker.env \
  --env-file /opt/echomem-stress/formal-run.env \
  -v /opt/Memory-System-Eval-Harness:/harness \
  -v /opt/echomem-stress:/opt/echomem-stress \
  -w /harness \
  -e ECHOMEM_BASE_URL=http://127.0.0.1:8010 \
  -e ECHOMEM_CONFIG=/opt/echomem-stress/workspace/config.json \
  -e STRESS_TENANT_CONFIG=/opt/echomem-stress/tenants-32.json \
  -e STRESS_OUTPUT_DIR=/opt/echomem-stress/results/4u8g-docker-$(date +%Y%m%d_%H%M%S) \
  echomem-stress-runner:latest \
  -lc 'export STRESS_CASE_TIMEOUT_S=180 STRESS_COMMIT_TIMEOUT_S=600; ./performance/run_4u8g_complete.sh'
```

如果镜像有不同的 entrypoint，仍要确保最终执行的是 `/harness/performance/run_4u8g_complete.sh`：

```bash
docker run --rm --network host --entrypoint bash \
  -v /opt/Memory-System-Eval-Harness:/harness \
  -v /opt/echomem-stress:/opt/echomem-stress \
  -w /harness echomem-stress-runner:latest \
  -lc 'pwd; python --version; export STRESS_CASE_TIMEOUT_S=180 STRESS_COMMIT_TIMEOUT_S=600; ./performance/run_4u8g_complete.sh'
```

注意：脚本会在发送任何业务请求前检查 Python 版本；系统 Python 3.6 会直接退出，
并标记为测试平台运行时错误。进入正式压测前还会执行真实租户鉴权预检，若 32 个
租户中只有 1 个通过，只能做单租户诊断，不能宣称多租户公平、隔离或容量结果。

`--env-file` 只能接受 `NAME=value`，不能直接传入包含 `export` 的 shell
文件；上面的转换命令只读取赋值，不执行其中的 shell 代码，也不会打印变量值。

### 7. 查看进度和结果

```bash
tail -f "$STRESS_OUTPUT_DIR/launcher.log"
cat "$STRESS_OUTPUT_DIR/suite.json"
cat "$STRESS_OUTPUT_DIR/acceptance.json"
find "$STRESS_OUTPUT_DIR" -name summary.json -type f | sort
```

最终应确认 `suite.json` 中 37 个场景均有结果；`acceptance.json` 中的
`PASS`、`FAIL`、`INCONCLUSIVE` 要逐项查看，不能只看总准确率或退出码。
完整 4U8G 套件还必须检查 `suite.json.finalization`：
`run_count == expected_run_count` 只表示每个场景有记录，仍需同时查看
`completed_run_count`、`timeout_run_count`、`blocked_run_count` 和每个场景的
真实请求样本；`TIMEOUT`/`BLOCKED` 不能当作通过。

完整套件对每个场景默认允许 2 次环境级重试（总共最多 3 次尝试），只针对
`ENV_ERROR`、`NO_SUMMARY`、`HARNESS_ERROR` 这类在未产生业务请求前退出的情况；
每次失败尝试会保存在该场景目录的 `attempt-01/`、`attempt-02/` 中，最终尝试
仍写入场景主目录。功能性 `FAIL`、已有真实请求的 `TIMEOUT` 和成功结果不会被
重试或覆盖。可用 `--case-retries N` 与 `--case-retry-backoff-s` 调整。

### 六项 4U8G 目标统一自动化入口

使用 `performance/objective_suite.py` 可以按实例规格逐个执行容量、稳定性、
公平性、Search 优先级、Commit 恢复和 `/metrics` 可观测性检查。真实服务器上先
把 `performance/instance-profiles.example.json` 复制为实际 profile 配置，并填写
真实的 `tenant_config`、`preflight_config` 和可选 `prepare_command`：

```bash
python3 -m performance.objective_suite \
  --profiles performance/instance-profiles.example.json \
  --profile 4U8G \
  --out-dir results/objective-suite-$(date +%Y%m%d_%H%M%S) \
  --full
```

`--full` 是正式 4U8G 入口：会运行 PR397 的 12 个场景和 PR421 的 25 个场景，
总计 37 个场景（默认不包含 soak），默认总墙钟预算为 6 小时。37 个场景需要逐个
保留真实模型和真实 HTTP 样本；可以通过 `--max-wall-clock-s` 显式缩短，但缩短后的
结果只能按实际完成覆盖解读。不要在正式验收
命令中加 `--quick`；`--quick` 仅用于快速诊断子集，不能作为完整测试结果。
报告中的场景覆盖必须显示 `37/37`，否则本轮只能算部分结果。

正式入口默认从总预算中预留 900 秒给 formal 结束后的能力、限流、故障和
kill-9 恢复补测，避免 formal 场景耗尽时间后 O2/O5/O6 没有机会执行。可用
`--probe-budget-s` 调整；设为 `0` 才会取消预留。报告的“补测计划”会列出
每个探针对应的目标、是否配置和实际状态；没有真实控制端点的项目仍显示
`INCONCLUSIVE`，不会伪造通过。

服务器如果把 EchoMem 的真实模型凭据放在 Docker env 文件中，评测入口也要加载同一份
env 文件，保证 formal suite 和探针使用的模型环境与 EchoMem 服务一致：

```bash
python3 -m performance.objective_suite \
  --profiles performance/instance-profile-4u8g.audit.server.example.json \
  --profile 4U8G \
  --env-file /opt/echomem-stress/formal-run-4u8g.env \
  --out-dir results/objective-suite-$(date +%Y%m%d_%H%M%S) \
  --full
```

`--env-file` 只读取简单的 `KEY=VALUE`/`export KEY=VALUE` 行，密钥不会写入
`objective-suite.json`、HTML 或命令记录。`commit_recovery.tenant` 如果已经不在
当前 `tenant_config` 中，入口会自动选用该配置中的第一个租户，避免动态租户 ID
更新后仍因旧 profile 名称导致恢复探针在启动阶段失败。

`--quick` 只做 bounded smoke，并默认跳过真实模型灌种，专门快速验证调度、延迟和
可观测性链路；因此不能用它证明记忆质量。需要把真实租户记忆和 active-user/session
证据纳入测试时，可加 `--quick-include-seed`。打开该选项后，平台先为本轮需要的
租户做一次最小真实 warm-up，后续场景复用这批已完成记忆，避免每个场景重复等待
模型抽取；报告会记录 `seed_reused` 和 `seed_evidence_status`。单场景默认最多运行
30 秒、总墙钟 180 秒、barrier 默认最多 32 个
Commit；正式场景 Commit 默认单次最多等待 180 秒，并发波次默认 4，可通过
`--quick-commit-timeout-s` 和 `--quick-barrier-wave-size` 调整。真实模型 warm-up
另外使用 `--quick-seed-commit-timeout-s`（默认 180 秒），外层窗口至少为该值的 2 倍。
warm-up 成功后所有场景复用同一批真实记忆；warm-up 失败时后续场景会禁用
本轮 seed 但继续采集可独立验证的真实请求，并在每个 run 记录
`seed_status`、`seed_dependency` 和 `seed_evidence_status`。少于 32 个真实
Commit 只能作为 smoke 数据，不能验收 O5 严格优先级：

正式优先级和容量测试建议使用 `--mode fixed-rps --rps <读速率>`，并按需设置
`--commit-rpm <写速率>`；`max-throughput` 仅用于单独的客户端极限诊断。为避免
无上限建连先耗尽压测机临时端口，默认单场景累计 100 次 `connection` 错误后停止
发压，并在 `summary.json` 写入 `client_diagnostics.verdict=CLIENT_RESOURCE_EXHAUSTED`。
可通过 `--client-connection-error-abort-threshold 0` 关闭熔断，或传入其他非负阈值。

默认 quick 场景为 `baseline`、`fairness-bounded`、`search-priority-blackbox`、
`saturation`、`capacity-2`、`capacity-4`、`capacity-8`。它们用于快速拿到单租户基线、
公平性、Search/Commit 并发、饱和和小规模容量阶梯的真实 HTTP 证据；其中
`capacity-8` 用于快速诊断；完整 4U8G 目录另外保留 `capacity-16`、`capacity-32`，
用于正式运行寻找容量边界。quick 结果不能据此宣称最大容量。PR397 的完整
A/B/C/D 矩阵以及大规模 barrier 不属于 quick，不能用 quick 结果替代完整验收。

```bash
python3 -m performance.objective_suite \
  --profiles performance/instance-profiles.example.json \
  --profile 4U8G \
  --scenarios baseline,mixed,search-priority-blackbox,capacity-2,capacity-4 \
  --quick-duration-cap-s 60 \
  --quick-case-timeout-s 300 \
  --quick-barrier-count-cap 32 \
  --out-dir results/objective-suite-custom
```

正式数据去掉 `--quick`。O1 按活跃 session 数对应的活跃用户代理和 Search SLO 评估容量，Commit 洪泛由 O5 单独验收；
容量档位超时只有在超时前已实际发出 Search 请求时才算边界；准备阶段或 Commit 阶段卡住不能冒充 Search 容量上限。
O1 只有在“Search 成功容量档位 + 更高一档真实失败/超时/资源边界”
同时存在时才会判定为 PASS；如果所有已跑档位都成功，报告只给出“至少支持到 N”
的容量下界并标记 `INCONCLUSIVE`，不会把最后一个成功档位冒充最大用户量。O1 的“最大用户量”是压测
窗口内完成的容量阶梯上限，不直接等同于业务 DAU；O6 必须额外提供真实 container
重启和 cursor/message-set 对账配置，并在 `commit_recovery` 中设置
`"require_accepted_202": true`，否则没有在崩溃前明确收到 HTTP 202 的操作不能进入恢复验收；
O6 必须实际抓到服务端 `/metrics` 四元组。
O4 会在 `search-priority-blackbox`、`tenant-skew` 等候选负载中选择租户覆盖最完整
的一轮计算公平性，避免 quick 模式的小 barrier 结果遮蔽更完整的真实证据；如果
该轮仍有租户没有 Commit 或 Search 样本，结果仍会保留为 `FAIL` 或 `INCONCLUSIVE`。
报告输出 `objective-suite.json` 和 `objective-suite.html`，不会把缺失证据算成通过。
如需把已有运行结果整理成更适合评审的六项指标页面，可执行：

```bash
python3 scripts/build_pr29_six_metric_report.py \
  results/objective-suite-20260904 \
  --output results/objective-suite-20260904/pr29-six-metric-report.html
```

该报告会区分“已配置场景”“实际发出请求”和“证据足够的结论”，并链接
`objective-suite.json`、能力/恢复探针以及公平性和优先级场景的原始 Search/Commit
CSV。找不到的证据不会生成伪造链接，也不会被提升为 PASS。
 profile 中配置 `capability_probe`、`commit_recovery`、`fault_plan` 后，入口会自动
执行真实 HTTP 能力探针、Commit 中途 kill-9 恢复探针和故障套件，并把每个检查项写入
HTML 明细。可从 `performance/instance-profiles.example.json` 与
`performance/fault-plan.example.json` 复制后按实际服务地址、租户和容器名修改。
Commit cursor 优先使用 EchoMem 已有的
`echo://sessions/{session}/current/commit_cursor.json`，由平台通过公开的
`GET /fs/read?uri=...` 读取；套件会自动绑定本轮真实完成 Commit 的 session。
因此不需要为压测新增 EchoMem 接口，也不会把一个旧 session 的 cursor 当成本轮证据。
另外，profile 可配置 `missing_cases` 和 `concurrent_commit`，入口会自动执行
PR397 的写后可见性/持久化对账、Commit 状态机、冷暖 Search，以及并发 Commit
探针；这些检查直接调用 EchoMem 已有 HTTP 接口，不需要修改 EchoMem。建议 quick
先把 `max_tenants` 设为 `1`、并发设为 `4`，正式验收再扩大租户和并发窗口。
探针结果会写入 profile 目录下的 `missing-cases.json`、`concurrent-commit.json`
并同步展示在 `objective-suite.html`；探针自身没有足够证据时仍显示
`INCONCLUSIVE`，不会被包装成 PASS。
没有真实故障控制端点时，故障项必须显示 `INCONCLUSIVE`，不能用测试平台自身缺少
适配器来判定 EchoMem 未实现。
正式套件会把 profile 的 `auth_header` 贯穿到租户预检、真实 workload、限流阶梯、
故障隔离、恢复和指标探针；支持 `X-Auth-Key`、`X-API-Key` 以及带 `Bearer` 前缀
的 `Authorization`。未配置时默认使用 `X-Auth-Key`，避免主压测和补测探针因鉴权头
不一致产生假失败。
报告文件为 `suite.html`，逐请求和资源时序通常位于各场景的 `run/` 目录。
如果只想重新审计已有结果而不重新发请求，可在 profile 中填写
`suite_path`，然后执行 `objective_suite.py --skip-run`；该模式只读取
`suite.json` 和已有探针制品。

如果完整 formal suite 已经跑完，只需要补测故障隔离、拒绝响应、kill-9
恢复或 `/metrics` 能力，可以使用缺口入口。它复用已有的 `suite.json`，不会
重新发送 37 个真实模型场景：

```bash
STRESS_SUITE_PATH=/data/formal/suite.json \
STRESS_PROFILES=performance/instance-profile-4u8g.audit.server.example.json \
STRESS_ENV_FILE=/opt/echomem-stress/formal-run-4u8g.env \
./performance/run_4u8g_gaps.sh
```

`commit_recovery.container` 支持 `${ECHOMEM_CONTAINER}` 环境变量；服务器必须
填写当前实际的 EchoMem 容器名，不能沿用历史任务的容器名。故障隔离只有在
`fault_isolation.enabled=true` 且提供真实故障控制 URL/命令时才会执行；没有
控制面时报告为 `INCONCLUSIVE`，不会把测试平台自身缺少控制能力判定为 EchoMem
故障。

### 飞书机器人启动 4U8G 压测

服务器 Web/飞书入口已将“压测”映射为真实 EchoMem 压测任务，并使用单并发队列。
在群里 `@pr测试 压测` 会测试当前已部署的 4U8G EchoMem；也可以发送
`@pr测试 压测 develop` 或 `@pr测试 压测 PR 274`，分别测试最新 develop
或指定 PR 的临时实例。机器人先返回任务 ID 和详情页，运行中展示准备、压测、
报告阶段，完成后回传 HTML 报告与原始结果；异常会回传任务详情、容器日志和
可重试动作。

该入口默认关闭 soak，不使用 fake/mock 模型，沿用服务器配置的真实 LLM、
Embedding、`ECHOMEM_AUTO_COMMIT_THRESHOLD=20000` 和 4U8G 资源档位。测试结果
保留三天，过期结果由服务器定时清理。正式六项结论以
`objective-suite.html` / `suite.json` 为准，缺少真实故障控制或恢复证据时会标记
`INCONCLUSIVE`，不会把缺失数据当作通过。

一键入口还会在输出目录先写入 `run-manifest.json`，记录本次实际使用的
EchoMem `config.json` SHA-256、模型名、目标地址、4U8G profile、是否启用
soak 以及 runner 退出码（不保存任何 API key）。套件即使返回 FAIL 或
INCONCLUSIVE，也会继续生成 `pr29-six-metric-report.html`，便于直接查看
部分结果和缺失证据；不要因为 runner 退出码非 0 就删除这份报告。

容量场景（quick 和 full）都把 Commit 负载固定为 0，避免容量测量被后台写入拖住；
容量场景的 `K` 负载在 `commit-rpm=0` 时严格只启动 Search worker，不会因为
默认的读写线程拆分而偷偷发起 Commit；这保证容量档位测的是活跃用户的 Search
边界，而不是异步 Commit 的完成时间。
容量场景中的 `capacity_active_users` 是目标活跃用户代理数，`tenants` 是真实独立租户数，
`active_sessions_per_tenant` 是每租户创建的真实空 session 数。比如 `capacity-32`
使用 4 个独立租户、每租户 8 个 session，得到 32 个活跃用户代理；它不要求准备 32
个 API key，也不把“租户数”冒充“DAU”。结果中的 `data_scale.active_user_count`、
`active_users_by_tenant` 和 `tenant_details[*].active_session_ids` 用于复核实际创建和参与
压测的身份数量。
`fairness-bounded`、`search-priority-blackbox`、`saturation` 等需要真实竞争样本的
场景会使用有界 barrier 上限（默认 32 个 Commit）；少于 32 个只能报告
`INCONCLUSIVE`，不能验收严格 Search 优先级。quick 模式如果打开真实模型灌种，
会先为本轮所需的最大租户数执行一次 warm-up，后续场景复用这批已提交记忆，不会每个
场景重复等待模型抽取；报告中的 `seed_reused=true` 表示该场景使用了复用数据。
这仍是快速诊断，不替代正式长窗口验收。快速运行仍显示 `INCONCLUSIVE` 的常见原因不是 EchoMem 一定失败：

| 目标 | 还需要的真实证据 | 归属 |
|---|---|---|
| O1 | 至少一档成功的 `capacity-*`，再逐步增加租户直到 SLO 失败 | 测试平台场景与机器资源 |
| O2 | 故障期间每个旁观租户都有前后 Search P95 配对 | EchoMem/部署必须提供真实故障控制，平台负责采集 |
| O3 | 同一稳态窗口内至少两个租户同时有 Commit 吞吐和 Search P95 | 测试平台负载与独立租户凭据 |
| O4 | `search-priority-blackbox` 中同时存在 Commit 洪泛、同套件基线和 Search P95 | 测试平台场景，服务端负责实际调度 |
| O5 | 202 Commit、真实 kill/restart、history/archive/cursor/幂等重放对账 | 部署提供 kill/restart 权限，EchoMem提供既有读接口 |
| O6 | 每个实际 lane 都采到 queued/wait/exec/rejected 四元组，并有 engine fan-out 的 exec/skipped 证据；不要求把 `tenant_id` 放进指标标签 | EchoMem `/metrics` 暴露 bounded-label 指标，测试平台负责按 lane/fan-out 对账 |

配置文件可以进一步声明验收时的预期集合，避免“只要出现过一个
lane/租户就算覆盖”的误判：

```json
{
  "fairness_expectations": {
    "tenant_ids": ["stress-a", "stress-b", "stress-c", "stress-d"]
  },
  "observability": {
    "lanes": ["recall_engine", "recall_intent_llm", "recall_query_embedding", "recall_rerank", "commit"],
    "fanout_engines": ["recall", "commit"]
  }
}
```

`fairness_expectations.tenant_ids` 会被放入 Jain 指数分母。声明的租户没有
同一公平性窗口的 Commit 或 Search 样本时，结果保持 `INCONCLUSIVE`，不会被
自动排除。`observability.lanes` 和 `fanout_engines` 用于列出本次部署应该
暴露的 bounded-label 集合；缺失项会在 `observed.missing_lanes` 或
`observed.missing_fanout_engines` 中明确列出。

formal suite 生成的每个场景 `search_results.csv` 还会保留请求级的
`end_to_end_s`、`error_type`、`error_class`、`error_detail`、`Retry-After`、
`reason_code`、查询类型和召回命中字段；`commit_results.csv` 会保留 HTTP 状态、
重试次数和拒绝原因。这样限流/拒绝验收使用的是原始 HTTP 证据，而不是只看
场景退出码。`limit_failure_sweep` 中的 `workers` 是所有阶梯的最大并发上限，
不是固定并发值；例如 levels 为 `16,64,128,256` 且上限为 256 时，实际会依次
使用 16、64、128、256 并发，避免把四档测试误跑成同一档。

故障隔离的 `endpoint` 会收到
`{"action":"enable|disable","target_tenant":"...","tenant":"..."}`；命令控制
支持 `{action}`、`{target_tenant}` 和 `{tenant}` 占位符。故障采样即使发生
客户端异常也会进入 `finally` 执行 disable，防止真实故障状态污染后续场景。

公平性计算还要求被选中的同一负载窗口覆盖所有参与租户：每个租户都必须有
Search 样本和 Commit 提交样本。缺少某个租户时只报告 `INCONCLUSIVE`，不会把
没有到达或没有完成的租户从 Jain 分母中删除。

故障计划中的 `${BASE_URL}` 会被替换为 profile 当前地址，并写入运行目录的
`fault-plan.resolved.json`；不要再把旧服务器端口直接复制到通用示例中。

### 8. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `Connection refused :8010` | EchoMem 已退出或端口未监听 | 检查 `docker ps` 和 `/health` |
| `ModuleNotFoundError: performance` | runner 当前目录不是 Harness 根目录 | 使用 `-w /harness` |
| 长时间停在 `tenant-skew` | 260 个 Commit 屏障等待或服务异常 | 停止本轮，查看场景 `summary.json`，缩短 case timeout 后重跑 |
| 容器 `exit 137` | 容器被终止，常见于内存压力 | 检查 `docker inspect`、宿主机内存和 RSS 曲线 |
| `fake-llm` / `fake-embedding` | EchoMem 配置仍是 fake 模型 | 修正 `config.json` 和 `*_API_KEY` |
| 只有 `suite.json` 没有场景结果 | 首个场景前退出或目标服务不可达 | 查看 `launcher.log` 和 `run/*/summary.json` |

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
无需手动注册。运行：`python benchmarks/locomo/run_eval.py --agent-plugin <name> ...`

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
   - `run_eval.py` - 入口脚本
3. 复用 `shared/` 基础设施：`EvalConfig` / `EvalRun` /
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
