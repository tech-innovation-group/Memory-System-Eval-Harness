# Memory-System-Eval-Harness v3

记忆系统评测框架。全 CLI, 无网页 UI。

## 目录结构

```
plugins/              # 评测插件 (AgentPlugin 协议)
  base.py             # AgentPlugin 抽象基类 (setup/inject_memories/create_session/...)
  registry.py         # 插件发现与加载
  bare_llm/           # 纯 LLM 基线 (system prompt + 上下文 + 用户查询)
  echo_agent/         # EchoAgent 外部 agent 插件
  vikingbot/          # VikingBot 历史 prompt、工具协议和 agent loop
  echomem_mcp/        # EchoMem MCP agent + 记忆客户端
  openviking_mcp/     # OpenViking MCP agent + 记忆客户端
benchmarks/
  locomo/           # LoCoMo 数据集评测
    dataset.py      # LoCoMo 数据解析
    import_memory.py qa.py judge.py diagnosis.py retry.py
    blackbox.py compare.py reporting.py
    data/ docs/ results/ run_eval.py
  hotpotqa/         # HotpotQA 数据集评测
    dataset.py import_memory.py qa.py evaluate.py recovery.py reporting.py
    data/ docs/ results/ run_eval.py
  longmemeval/      # LongMemEval 数据集评测
    dataset.py import_memory.py qa.py judge.py evaluate.py
    recovery.py parallel.py reporting.py
    data/ docs/ results/ run_eval.py
  generic/          # 非正式/自定义数据集 dry-run 解析
dynamic/
  simulator.py      # 场景与查询生成
  workflows.py      # generate/replay 工作流
  metrics.py        # 动态指标和质量评估
  artifacts.py      # JSON/CSV/报告输出
  model_client.py   # 动态 LLM 客户端
  prompt_config.py  # prompt 配置加载
  configs/ docs/ results/ run_eval.py
shared/             # 共享库
  dataset_io.py     # 通用 JSON/JSONL 读取、下载和路径解析
  llm_client.py     # LLM 客户端 (OpenAI 兼容)
  text.py           # 通用文本规范化
  qa.py             # 通用 QA 数据结构和执行辅助
  recovery.py       # QA CSV 健康判定、恢复选择和成功行合并
  eval_base.py      # 评测基础设施 (配置, 日志, 结果目录, EchoMem 日志收集)
  memory_types.py   # 记忆类型 (CommitResult, SearchResult, MemoryClient Protocol)
  memory_args.py    # 记忆后端 CLI 参数 (--memory-backend 等)
scripts/
  backend_doctor.py    # 记忆客户端健康检查
  validate_evidence.py # QA 检索证据格式检查
```

正式数据集的加载、Judge、指标、重试和报告都归属
`benchmarks/<dataset>/`。评测针对 agent 插件而非记忆后端；记忆注入通过
`AgentPlugin.inject_memories()` 统一完成，评测平台不直接感知记忆后端。
当前支持 echomem 和 openviking 两个记忆后端，由 `--memory-backend` 参数选择。

## EchoMem 事故链路压测

独立压测脚本位于
`scripts/stress_echomem_incident.py`，通过真实 HTTP 请求执行事故链路：

```text
POST /api/sessions/open
POST /api/sessions/{session_id}/messages
POST /api/sessions/{session_id}/commit
```

### 压测方式

1. 单独启动 EchoMem 服务，并确认 HTTP 地址可以访问。压测脚本不会自动
   启停 EchoMem。
2. 每个并发档位同时启动对应数量的异步工作流。每个工作流使用独立的
   session ID，并依次发送 `open -> add message -> commit` 请求。
3. 所有工作流结束后，可通过 `--context-probes` 额外发送
   `build_context` 请求。这些请求属于第二轮探针，不会与写入请求混合。
4. 按递增并发量重复测试。不同档位之间保持 EchoMem 版本、模型配置、
   数据集、请求内容、超时时间和测试机器不变。

建议测试档位：

```text
10 -> 50 -> 100 -> 300 -> 1000
```

每个档位输出到独立目录，避免结果被覆盖：

```bash
for n in 10 50 100 300 1000; do
  python3 scripts/stress_echomem_incident.py \
    --url http://127.0.0.1:18101 \
    --concurrency "$n" \
    --context-probes 10 \
    --output "results/echomem_incident_$n"
done
```

压测客户端不会自动重试失败请求，以保留真实错误率，并暴露连接失败、
超时、HTTP 4xx/5xx 和工作流中途失败等问题。按照当前 API 约定：
`open` 和 `messages` 预期返回 `200`，`commit` 通常返回 `202`，表示后台
提交任务已被异步接收。

以下情况应视为异常：

- 返回非预期状态码；
- 工作流缺少某个步骤；
- 出现 `ConnectError`、超时或其他异常；
- `commit` 返回 `202` 但不代表后台索引已经完成，只代表任务已被接收。

每个输出目录包含：

- `client_results.json`：每个工作流的状态码、异常、响应片段、耗时和可选
  的上下文探针结果；
- `client_request.log`：每个写入工作流一行，包含正常状态和错误信息；
- `build_context_request.log`：启用上下文探针后，每个探针请求一行。

分析每个档位时，重点比较完成的工作流数量、状态码分布、异常数量、
连接/超时错误、总耗时和 `duration_ms`。EchoMem 服务端日志需要单独收集，
并使用相同的并发档位和测试时间进行对应。

## LoCoMo 测试

LoCoMo 数据集已经包含在
`benchmarks/locomo/data/locomo10.json`，测试者不需要另外下载或指定
`--dataset`。

### 1. 配置

`conv-30` 默认注入记忆到新创建的独立身份。先复制配置模板：

```bash
cp .env.example .env
```

然后至少设置以下内容：

```dotenv
ECHOMEM_BASE_URL=http://127.0.0.1:8010
ECHOMEM_ROOT=/absolute/path/to/EchoMem-repository
ECHOMEM_WORKSPACE=/absolute/path/to/existing-conv30-memory-workspace
ECHOMEM_AUTO_START=1

ECHOMEM_ACCOUNT=default
ECHOMEM_USER_ID=default
ECHOMEM_AGENT_ID=default

ANSWER_BASE_URL=https://provider.example.com/compatible-mode/v1
ANSWER_MODEL=deepseek-v4-flash
ANSWER_TOKEN=YOUR_API_KEY
```

`ECHOMEM_WORKSPACE` 指向 EchoMem 的 workspace 目录。保持 `ECHOMEM_AUTO_START=1`
时，`eval.sh` 会使用该目录启动 EchoMem，并在评测结束后关闭服务。若
`ECHOMEM_BASE_URL` 上已有服务，必须确认它也是由同一 workspace 启动的。

### 2. 运行检查

```bash
./eval.sh locomo --check --sample conv-30
```

正常输出应包含：

```text
samples=1
questions=81
session_mode=locomo
```

### 3. 运行全量 81 题

#### EchoMem MCP 三种对照模式

下面三条命令都使用 `echomem_mcp`、同一个 `conv-30` 数据集和真实
EchoMem 记忆注入。先设置 DashScope 兼容 API 环境变量；API key 只放在
本地环境中，不要写入仓库：

```bash
export LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export LLM_MODEL=deepseek-v4-flash
export LLM_API_KEY="$DASHSCOPE_API_KEY"
```

1. **不带 MCP 工具调用**：仍执行 EchoMem 记忆注入和手动召回，但回答阶段只
   进行一次模型调用。

   ```bash
   ./eval.sh locomo \
     --agent-plugin echomem_mcp \
     --sample conv-30 \
     --no-tool-calling \
     --mcp-read-mode disabled
   ```

2. **带 MCP 工具调用，但不读取 `messages.jsonl`**：保留
   `memory_query`、`list`、`glob`，从工具 schema 中移除 `read`；即使模型返回
   未暴露的 `read`，harness 也会拒绝转发该调用。

   ```bash
   ./eval.sh locomo \
     --agent-plugin echomem_mcp \
     --sample conv-30 \
     --tool-calling \
     --mcp-read-mode disabled
   ```

3. **带 MCP 工具调用并读取 `messages.jsonl`**：保留全部 MCP 工具，并追加
   transcript evidence prompt。模型每题先查询记忆，再在回答前对至少一个相关
   session/URI 读取具体的 `current/messages.jsonl`；涉及精确日期、顺序、原话
   或多个事件时，以完整 transcript 为准。

   ```bash
   ./eval.sh locomo \
     --agent-plugin echomem_mcp \
     --sample conv-30 \
     --tool-calling \
     --mcp-read-mode require
   ```

结果目录中的 `summary.json` 会额外记录：

- `messages_jsonl_read_questions`：至少成功发起一次 transcript 读取的题数
- `messages_jsonl_read_calls`：识别到的 `messages.jsonl` 读取调用数
- `messages_jsonl_read_rate`：读取题数 / QA 题数
- `tool_call_total`、`avg_iterations`、`accuracy`：工具使用和准确率对照指标

`require` 是提高 transcript 调用率的评测 prompt 候选，不保证准确率必然达到
85%；是否达到目标必须以同一批题、同一模型和 Judge 结果为准。

带 VikingBoat 0.4.11 对齐工具调用：

```bash
./eval.sh locomo --sample conv-30 --tools
```

不暴露工具，仅使用初始注入的完整记忆正文：

```bash
./eval.sh locomo --sample conv-30 --no-tools
```

本地验证额外 prompt 时，通过文件参数追加到所选 profile 的 system prompt：

```bash
./eval.sh locomo \
  --sample conv-30 \
  --tools \
  --qa-prompt-file /path/to/local-prompt.txt
```

prompt 文件不会进入代码仓；`summary.json` 和 resume manifest 仅记录文件名和
SHA-256。

两种模式使用相同的数据集和已有记忆：

| 命令 | 默认 QA profile | 记忆行为 |
|---|---|---|
| `--tools` | `vikingboat0411` | 新开身份注入记忆，初始检索，并允许只读 `memory_*` 工具循环 |
| `--no-tools` | `vikingboat0411-natural-no-tools` | 新开身份注入记忆，只使用完整初始记忆正文，不暴露工具 schema |

结果写入 `results/<run-name>/<timestamp>/`，主要文件包括
`qa_results.csv`、`judge_results.csv`、`summary.json` 和
`agent_traces/`。

### 4. 可选操作

只运行一题做 smoke test：

```bash
./eval.sh locomo --sample conv-30 --questions 1 --tools
```

## 其他用法

使用根目录统一入口；首次运行会自动创建 `.venv` 并安装依赖。

```bash
# EchoMem 未启动时自动启动；结束后自动关闭
./eval.sh locomo --start-echomem --sample conv-30 --questions 1

# 保留自动启动的 EchoMem 服务
./eval.sh locomo --start-echomem --keep-echomem --sample conv-30

# 从中断运行继续，复用已有身份和已完成 session，健康 QA/Judge 行不会再次调用模型
./eval.sh locomo \
  --sample conv-30 \
  --resume-qa /path/to/interrupted-run \
  --resume-judge /path/to/interrupted-run

# 其他静态数据集
./eval.sh hotpotqa --questions 10
./eval.sh longmemeval --questions 10
```

默认情况下，只要任何记忆导入没有完成，评测会在 QA 前退出并生成失败
`summary.json`。`--allow-diagnostics` 仅用于排障，不能用于正式分数。

LoCoMo 不指定 `--resume-qa` 时，总是新开身份并从零注入全部记忆；
指定 `--resume-qa` 时，复用原有身份，跳过已注入完成的 batch，只继续注入
未完成部分，然后恢复 QA。所有身份均不会在评测结束时自动删除，需要清理时
由用户在 EchoMem 侧手动操作。其他静态数据集（HotpotQA、LongMemEval）始终
新开身份注入。运行结果会记录 `memory_source=existing|injected` 和实际身份，
但 auth key 只会以掩码形式保存。
LoCoMo 在进入 QA 前还会校验数据集 SHA-256 和实际 session manifest；session
数量与当前 `session-mode` 不一致时默认拒绝运行，防止复用 tenant 被额外注入
污染。`--allow-diagnostics` 仅用于诊断。

LoCoMo 未显式指定 profile 时，`--tools` 自动选择 `vikingboat0411`，
`--no-tools` 自动选择 `vikingboat0411-natural-no-tools`。高级复现仍可通过
`--qa-profile legacy-77` 显式选择 77% 历史复现口径。
prompt/loop 来源与 EchoMemory 适配边界见
`docs/v2-source-provenance.md`，并会写入 `summary.json` 和逐题
`agent_traces/*.json`。

LoCoMo 默认按数据集原始 session 分批导入，避免把整段长对话压成一个
超大 commit。快速检查可增加 `--max-sessions 1`；兼容旧单 session 行为时
可显式使用 `--session-mode single`。

`--question-timeout-s` 是单题检索和回答共享的总 deadline；设为 `0` 表示
不增加单题总限制，但底层 HTTP 请求仍使用各自的连接超时。

### 直接运行 Python

```bash
# LoCoMo
python benchmarks/locomo/run_eval.py \
  --dataset /path/to/locomo.json \
  --llm-api-key YOUR_KEY

# HotpotQA
python benchmarks/hotpotqa/run_eval.py \
  --dataset /path/to/hotpotqa.json \
  --llm-api-key YOUR_KEY

# LongMemEval
python benchmarks/longmemeval/run_eval.py \
  --dataset /path/to/longmemeval.json \
  --llm-api-key YOUR_KEY
```

### 动态评测

```bash
# 先检查本地配置、EchoAgent 登录、credential 映射和 EchoMem 健康状态
./eval.sh dynamic --check \
  --username test_user --password YOUR_PASSWORD

# Generate 模式
./eval.sh dynamic \
  --username test_user --password YOUR_PASSWORD \
  --scenario-api-key YOUR_KEY \
  --llm-api-key YOUR_KEY

# Replay 模式
./eval.sh dynamic \
  --username test_user --password YOUR_PASSWORD \
  --dataset /path/to/locomo.json \
  --llm-api-key YOUR_KEY
```

通常优先使用 `./eval.sh`; 各 benchmark 详细参数见对应 `docs/usage.md`。

辅助检查均可独立运行：

```bash
python scripts/backend_doctor.py --format json
python scripts/validate_evidence.py --input /path/to/qa_results.csv --strict
python benchmarks/locomo/blackbox.py \
  --qa /path/to/run/qa_results.csv \
  --judge /path/to/run/judge_results.csv \
  --import-results /path/to/run/import_results.csv \
  --summary /path/to/run/summary.json \
  --out-dir /path/to/report
python benchmarks/locomo/compare.py \
  --left /path/to/run-a \
  --right /path/to/run-b \
  --out-dir /path/to/comparison
python benchmarks/hotpotqa/recovery.py --help
python benchmarks/longmemeval/recovery.py --help
```

## 评测流程概述

| Benchmark | 导入方式 | QA 方式 | 评测方式 |
|---|---|---|---|
| LoCoMo | 集中导入所有 session | 仅检索不写入 | LLM judge (CORRECT/WRONG) |
| HotpotQA | per_question 或 global | 仅检索不写入 | answer/supporting-fact/joint F1/EM |
| LongMemEval | 逐题隔离导入 haystack | 仅检索不写入 | 官方 accuracy (LLM yes/no) |
| 动态 (generate) | LLM 生成场景 | 端到端 EchoAgent | 配置驱动质量评估 (0-100) |
| 动态 (replay) | 先注入对话再 QA | 跨 session 检索 | 配置驱动质量评估 (0-100) |
