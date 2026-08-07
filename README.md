# Memory Benchmark Workbench

Unified benchmark workbench for LoCoMo, LongMemEval, and HotpotQA workflows.

> The refactored workbench is published on the `v2` branch. It is independent
> from the legacy UI on `main`.

This repository now bundles the runnable API server, task orchestration layer,
and the current OpenViking / EchoMemory import and QA runners alongside the new
frontend workbench. It still does not include benchmark datasets, model
credentials, injected memories, or run artifacts.

## EchoMemory HTTP Black-box Quick Start

The LoCoMo EchoMemory path is HTTP-only. The harness does not import the
EchoMemory Python SDK, query Neo4j, or read the EchoMemory server workspace for
QA evidence.

> **Do not modify EchoMemory source code for a black-box benchmark.**
> Start an unmodified EchoMemory release or commit as a separate service and
> connect this harness through its public HTTP API. Changes to EchoMemory
> retrieval, indexing, prompts, graph logic, or response formatting make the
> run a modified-backend experiment, not a comparable black-box result. Such
> runs must be labeled separately with the exact EchoMemory commit and patch.

```bash
git clone --branch v2 \
  https://github.com/tech-innovation-group/Memory-System-Eval-Harness.git
cd Memory-System-Eval-Harness

cp .env.example .env.local
# Edit .env.local, then:
set -a
source .env.local
set +a

bash scripts/start-workbench-stack.sh
```

Open <http://127.0.0.1:4173/>, select `EchoMemory`, and configure the
EchoMemory HTTP base URL. Import `conv-30` before launching QA.

For the exact HTTP contract, authentication behavior, CLI commands, strict
black-box guarantees, and the complete 81-question workflow, read
[README_ECHOMEMORY_BLACKBOX.md](README_ECHOMEMORY_BLACKBOX.md).

For the report command, required CSV fields, metric formulas, percentile
definitions, Token accounting, and black-box boundaries, read
[docs/blackbox-metrics-guide.md](docs/blackbox-metrics-guide.md).

## Architecture

```text
Browser
  -> Memory Benchmark Workbench (default: 127.0.0.1:4173)
  -> /api/* proxy
  -> bundled benchmark API (default: 127.0.0.1:19181)
  -> in-repo task orchestration + Python runners
```

The workbench UI still runs through `dev_server.py`, but the repository now
includes the actual backend service and Python runner code that executes
imports, QA runs, judging, and report generation.

## Requirements

- Python 3.9+
- Node.js 18+ for validation scripts
- Python environment with the dependencies required by the bundled backend
- EchoMemory / OpenViking runtime dependencies if you want to execute those
  backends against real services or SDK workspaces

No npm install is required. Browser code uses native ES modules and validation
scripts use Node.js built-ins only.

### Bring Your Own Models

This repository does not provide model credentials or a hosted model service.
Before running EchoMemory import or QA, configure models from your own
OpenAI-compatible provider:

- an LLM for EchoMemory extraction, summaries, intent routing, and graph work
- an embedding model for EchoMemory indexing and retrieval
- an answer model for benchmark QA
- a judge model for LoCoMo correctness scoring

Copy `.env.example` and replace every `your-*` value with your own endpoint,
model name, and API key. Keep the embedding dimension consistent with the
selected embedding model and the EchoMemory workspace configuration. Mock
models and placeholder credentials are suitable only for code smoke tests,
not for accuracy reporting.

Answer-model thinking mode is disabled by default
(`ANSWER_THINKING_MODE=disabled`). Enable the provider default only for an
explicitly labeled ablation, because thinking mode changes latency, token
usage, and potentially accuracy.

### Optional Graph Dependencies

EchoMemory can run without external graph retrieval, but graph-memory and
graph-diffusion tests require additional local services and packages:

- install and start Neo4j, then configure a valid URI, username, password, and
  database in the EchoMemory workspace
- install `spacy` and the language model required by your EchoMemory
  configuration, for example `python -m spacy download en_core_web_sm`

If Neo4j credentials are missing or invalid, graph tests may fail even when
the HTTP, session, atom, and vector-memory paths are healthy. Record whether
graph retrieval was enabled when publishing benchmark accuracy.

## Start

```bash
git clone --branch v2 \
  https://github.com/tech-innovation-group/Memory-System-Eval-Harness.git
cd Memory-System-Eval-Harness

bash scripts/start-workbench-stack.sh
```

Open <http://127.0.0.1:4173/>.

### Reproduce LoCoMo `conv-30` `legacy-77`

After importing `conv-30` and starting its EchoMemory HTTP service, use these
QA-only entrypoints. They reuse the existing memory workspace and do not import
or write memory:

```bash
# legacy-77 with Agent memory tool calls
bash scripts/run_locomo_conv30_legacy77_tool.sh

# Same legacy-77 settings with Agent memory tool calls disabled
bash scripts/run_locomo_conv30_legacy77_no_tool.sh
```

An optional first argument selects the output directory:

```bash
bash scripts/run_locomo_conv30_legacy77_no_tool.sh runs/legacy77-no-tool
```

The no-tool run still performs the initial EchoMemory HTTP search and injects
the returned evidence into the answer prompt. The only ablation variable is
iterative Agent tool execution. On another machine, set `LOCOMO_DATASET`,
`ECHOMEM_ROOT`, `ECHOMEM_WORKSPACE`, and `ECHOMEM_BASE_URL`; auth and model
settings are read from the EchoMemory workspace by default. Run either script
with `--help` for every supported override.

See
[`README_ECHOMEMORY_BLACKBOX.md`](README_ECHOMEMORY_BLACKBOX.md#locomo-legacy-77-tool-on-vs-tool-off)
for the black-box contract and required result checks.

### EchoMem `atomic_engine` and MCP prompt reference

For EchoMem runs that use the atom-based engine, configure the EchoMem service
workspace with:

```json
{
  "engine": {
    "enabled": ["atomic_engine"]
  }
}
```

The `engine.enabled` setting belongs to EchoMem, not to this harness. Record
the EchoMem commit, workspace configuration, and engine id in the benchmark
report so results from `atomic_engine` are not mixed with `echo0_plugin`.

For an MCP host, use one of the following answer-model system prompts.

#### No tool calling

The host performs the initial `memory_query` retrieval and injects the returned
excerpts into the answer context. The model receives no tool definitions and
must not make tool calls:

```text
You are a helpful assistant answering a question from the memory excerpts
included in the conversation. Answer concisely and directly from those
excerpts. Prioritize the supplied EchoMem memory evidence over general
knowledge or unsupported inference. Preserve exact names, dates, order, and
values when the memory provides them. Do not emit tool calls, function-call
markup, XML tool tags, or a plan to search. Use the available memory to answer
as helpfully as possible.
```

#### Tool calling enabled

The host provides the EchoMem MCP tools. The model may search with
`memory_query` and inspect source evidence with `read`, `list`, or `glob`:

```text
You are a helpful assistant with access to EchoMem long-term memory through
the MCP tools provided in this request. If context is insufficient, use the
available EchoMem MCP tools or memory context to find more information. Answer
the question directly.
```

Status and stop commands:

```bash
bash scripts/status-api-server.sh
bash scripts/status-v2-server.sh
bash scripts/stop-api-server.sh
bash scripts/stop-v2-server.sh
```

Runtime files are written under `.runtime/` and are ignored by Git.

To start only the API server:

```bash
bash scripts/start-api-server.sh
```

If the default ports are already in use, override them explicitly:

```bash
BENCHMARK_CONSOLE_API_PORT=19183 \
BENCHMARK_CONSOLE_API_BASE=http://127.0.0.1:19183 \
BENCHMARK_CONSOLE_V2_PORT=4174 \
  bash scripts/start-workbench-stack.sh
```

For foreground development:

```bash
python3 server.py --host 127.0.0.1 --port 19181

python3 dev_server.py \
  --host 127.0.0.1 \
  --port 4173 \
  --api-base http://127.0.0.1:19181
```

## Validate

Static and smoke validation:

```bash
node scripts/validate.mjs
```

Runtime validation with the bundled API service running:

```bash
BENCHMARK_CONSOLE_API_BASE=http://127.0.0.1:19181 \
BENCHMARK_CONSOLE_V2_ORIGIN=http://127.0.0.1:4174 \
  node scripts/check-v2-runtime.mjs --start-server
```

## Repository Layout

- `index.html`: single HTML entrypoint for the new workbench UI
- `app.js`: browser application entrypoint
- `styles.css`: single design-system and component stylesheet
- `dev_server.py`: static server and `/api/*` reverse proxy for the new UI
- `server.py`: bundled benchmark API server and task controller
- `memory/`: backend services, task specs, reporting, adapters, and plugins
- `benchmark/locomo/`: packaged LoCoMo benchmark entrypoints
- `scripts/`: runnable import/QA/judge/report scripts used by the backend
- `web/`: backend package manifest, API helpers, and legacy compatibility UI assets
- `src/action/`: API payload and workflow actions
- `src/render/`: benchmark-specific rendering
- `src/form-readers.js`: form-to-payload boundary
- `src/benchmark-registry.js`: benchmark registration and run ownership
- `scripts/check-v2.mjs`: architecture, syntax, and workflow smoke checks
- `scripts/check-v2-runtime.mjs`: live static/API proxy check
- `docs/api-contract.md`: bundled API surface
- `docs/blackbox-metrics-guide.md`: strict black-box metric inputs, commands, formulas, and boundaries

## Supported Workflows

- LoCoMo: memory import, HTTP black-box QA, judge, retry, report, and diagnostics
- LongMemEval: import, QA, official-style summary, and report artifacts
- HotpotQA: import, QA, answer/supporting-fact metrics, and report artifacts
- EchoMemory and OpenViking backend selections

For LoCoMo, EchoMemory QA and retry tasks use the EchoMemory HTTP API only.
EchoMemory import also uses its HTTP session APIs. The workbench does not query
Neo4j directly, read workspace memory files, or
inject platform-generated graph/atom evidence. Optional `overview.md`
enrichment is also black-box: session URIs are derived from the native search
response and read only through EchoMemory HTTP `/fs/read`. Any graph retrieval
used by a run is controlled internally by the EchoMemory service.

## Bundled Backend Layout

- New workbench UI: `index.html`, `app.js`, `styles.css`, `src/`
- Bundled API server: `server.py`, `memory/`, `web/api/`
- Runnable LoCoMo / EchoMemory / OpenViking scripts: `scripts/`, `benchmark/locomo/`
- Backend compatibility assets kept in a single directory: `web/static/`

The compatibility assets are not the primary UI anymore. They are vendored so
the API server keeps its current contract and self-check routes while the new
workbench becomes the main entrypoint.

## Data and Secrets

Do not commit:

- API keys or model-provider credentials
- Neo4j credentials
- benchmark datasets with restricted distribution terms
- injected memory workspaces
- run outputs, recall logs, reports, or archives
- local account and task state

The repository intentionally excludes all of these categories.

## Publishing Checklist

Before publishing:

1. Choose and add an approved open-source license.
2. Review the vendored backend code and remove any machine-specific defaults you
   do not want to expose publicly.
3. Run `node scripts/validate.mjs`.
4. Start `scripts/start-api-server.sh` and `scripts/start-v2-server.sh`, then
   smoke `/api/config`, `/api/tasks`, and `/api/readiness`.
5. Run the secret and artifact boundary checks in `scripts/validate.mjs`.
6. Review `git status` and commit only source, tests, and documentation.

## Status

This repository now contains the workbench frontend plus the currently used
benchmark backend and runner code. The integration is transitional: the backend
is vendored largely intact so the repo is runnable in one place before deeper
refactoring.

## EchoAgent Live Test (大模型交互评测)

`scripts/echoagent_live_test.py` 提供端到端的大模型交互评测能力，用于测试：
- **记忆召回能力**：跨会话记忆召回的质量
- **Prefill 预发送效果**：打字期间预取记忆，降低首 token 延迟（TTFT）

### 功能特点

1. **模拟真实用户交互**：以均匀速率逐字符输入查询，触发 prefill 预发送
2. **跨会话测试**：支持中途新开会话继续话题，验证跨会话记忆召回
3. **场景自动生成**：调用 LLM 随机生成日常生活主题、事实和查询
4. **数据集回放**：支持从 LoCoMo 格式数据集回放对话，测试跨会话记忆
5. **完整指标采集**：TTFT、cached tokens、prompt tokens、回复质量评估

### 前提条件

1. EchoMem、EchoAgent 插件(31030)、EchoAgent 后端(31020) 均已启动
2. EchoAgent 后端存在测试用户账号
3. 场景生成需要 LLM API（火山引擎方舟或 DashScope）

### 快速执行

```bash
# 火山引擎方舟
python scripts/echoagent_live_test.py \
  --echoagent-url http://127.0.0.1:31020 \
  --username test_user --password test_password \
  --num-batches 3 \
  --queries-per-batch 5 \
  --scenario-base-url https://ark.cn-beijing.volces.com/api/v3 \
  --scenario-model doubao-seed-2.0-pro \
  --scenario-api-key YOUR_API_KEY \
  --out-dir runs/echoagent_test

# DashScope（默认）
export DASHSCOPE_API_KEY=sk-xxx
python scripts/echoagent_live_test.py \
  --echoagent-url http://127.0.0.1:31020 \
  --username test_user --password test_password \
  --out-dir runs/echoagent_test
```

### 主要参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--echoagent-url` | `http://127.0.0.1:31020` | EchoAgent 后端地址 |
| `--username` / `--password` | `test_user` / `test_password` | 登录凭证 |
| `--num-batches` | 3 | 测试批次数，每批次独立生成一个场景 |
| `--queries-per-batch` | 5 | 每批次查询轮数（不含事实注入轮） |
| `--new-session-ratio` | 0.3 | 每轮随机新开会话的概率，测试跨会话召回 |
| `--typing-speed-ms` | 200 | 模拟打字每字符间隔（毫秒） |
| `--typing-jitter-ms` | 20 | 打字间隔随机抖动 |
| `--memory-engine-endpoint` | `http://127.0.0.1:31030` | 记忆引擎 endpoint |
| `--dataset` | （空） | 数据集名称或路径，指定后进入回放模式 |
| `--out-dir` | （必填） | 结果输出目录 |

### 输出文件

执行后在 `--out-dir` 下生成：

| 文件 | 说明 |
|------|------|
| `echoagent_live_test_results.json` | 完整结果，含每轮对话详情 |
| `echoagent_live_test_results.csv` | 汇总表，每行一轮 |
| `summary.json` | 汇总统计：平均 TTFT、cached_tokens、回复长度等 |
| `quality_report.json` | LLM 评估的记忆召回质量报告 |
| `run.log` | 运行日志 |

### 质量评估报告

测试结束后自动调用 LLM 评估记忆召回质量：

| 维度 | 评分 | 说明 |
|------|------|------|
| `recall_score` | 0-2 | 是否正确使用了 ground-truth 事实 |
| `factual_accuracy` | 0-2 | 回复是否事实准确无幻觉 |
| `relevance` | 0-2 | 回复是否与查询相关 |

汇总指标包括 `overall_score`、`cross_session_score`、`same_session_score`。

### 数据集回放模式

```bash
python scripts/echoagent_live_test.py \
  --echoagent-url http://127.0.0.1:31020 \
  --username test_user --password test_password \
  --dataset locomo10 \
  --dataset-limit 10 \
  --out-dir runs/replay_locomo10
```

回放流程：将数据集对话注入 EchoAgent 会话，在新会话中提问测试跨会话召回。

### 详细文档

参见项目根目录 `.agent/基础测试/` 下的设计文档：
- `大模型测试方案.md`：测试场景描述
- `大模型测试插件需求.md`：功能需求
- `大模型测试插件实现方案.md`：技术架构
- `大模型测试脚本执行方法.md`：执行参数详解
