# LoCoMo 评测

## 评测流程

1. **记忆注入**: 总是新开用户身份并执行 open -> add_messages -> commit -> poll_commit；指定 `--resume` 时复用已有身份，跳过已完成的 session 仅注入缺失部分（逐 batch 增量落盘，中断可续）
2. **逐题 QA**: 默认使用历史 VikingBot prompt 和 `memory_search` / `memory_read_many` 多轮工具循环，仅检索不写入
3. **LLM Judge**: 用 LLM 判定回答 CORRECT / WRONG

## 使用方法

先在 `.env` 中将 `ECHOMEM_WORKSPACE` 指向 EchoMem
workspace，并保留 `ECHOMEM_AUTO_START=1`。

### EchoMem MCP 三种对照模式

使用 `echomem_mcp` 时，三种模式共享 LoCoMo `conv-30` 的记忆注入流程，
区别只在回答阶段是否暴露 MCP 工具以及是否允许读取完整 transcript。模型
配置通过环境变量提供：

```bash
export LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export LLM_MODEL=deepseek-v4-flash-0731
export LLM_API_KEY="$DASHSCOPE_API_KEY"
```

EchoMem's `config.json` must also use a real provider for both
`model.llm` and `model.embedding`:

```json
{
  "provider": "openai_compatible",
  "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "api_key_env": "DASHSCOPE_API_KEY",
  "model": "deepseek-v4-flash-0731"
}
```

For `model.embedding`, use the same endpoint and key environment with
`"model": "text-embedding-v3"` and `"dimensions": 1024`. Do not use
`fake-llm` or `fake-embedding` for an official run.

不带 MCP 工具调用。平台仍通过 MCP `memory_query` 做初始召回，但回答阶段不向
模型暴露工具，只进行一次模型调用：

```bash
./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8110 \
  --mcp-url http://127.0.0.1:8111 \
  --echomem-auth-key "$ECHOMEM_AUTH_KEY" \
  --mcp-auth-key "$ECHOMEM_AUTH_KEY" \
  --sample conv-30 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --no-tool-calling
```

带 MCP 工具调用但不读 `messages.jsonl`：

```bash
./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8110 \
  --mcp-url http://127.0.0.1:8111 \
  --echomem-auth-key "$ECHOMEM_AUTH_KEY" \
  --mcp-auth-key "$ECHOMEM_AUTH_KEY" \
  --sample conv-30 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --tool-calling \
  --mcp-read-mode disabled
```

该模式会从工具 schema 中移除 `read`，并拒绝执行模型返回的未暴露 `read` 调用，
因此不会把 transcript 请求转发给 MCP。

带 MCP 工具调用并允许读取 `messages.jsonl`：

```bash
./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8110 \
  --mcp-url http://127.0.0.1:8111 \
  --echomem-auth-key "$ECHOMEM_AUTH_KEY" \
  --mcp-auth-key "$ECHOMEM_AUTH_KEY" \
  --sample conv-30 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --tool-calling \
  --mcp-read-mode allow
```

平台侧初始召回始终使用 MCP `memory_query`。`allow` 保留 `read` 工具，但不强制
模型调用；模型是否读取 `current/messages.jsonl` 由模型根据工具描述和上下文自行
决定。`summary.json` 的 `messages_jsonl_read_rate` 用于观察实际读取情况，不能
把调用率直接等同于准确率。

```bash
# 默认注入记忆并使用 VikingBoat 0.4.11 工具口径
python benchmarks/locomo/run_eval.py --sample conv-30 --tools

# 自然无工具对照
python benchmarks/locomo/run_eval.py --sample conv-30 --no-tools

# 追加仅保存在本地的实验 prompt
python benchmarks/locomo/run_eval.py \
  --sample conv-30 \
  --tools \
  --qa-prompt-file /path/to/local-prompt.txt

# 从中断运行继续（统一 --resume）：复用身份，跳过已完成 import batch，
# 只重新 QA 失败/缺失题、只重判缺失 Judge 行；指标（token/延迟/精度）按整轮累计
python benchmarks/locomo/run_eval.py \
  --sample conv-30 \
  --resume /path/to/interrupted-run

# 等价的旧参数形式（已被 --resume 取代，仅保留兼容）
python benchmarks/locomo/run_eval.py \
  --sample conv-30 \
  --resume-qa /path/to/interrupted-run \
  --resume-judge /path/to/interrupted-run

# 使用 VikingBot v0.4.11 prompt、工具语义和循环口径
# 后端和模型可见工具均使用只读 EchoMemory memory_* 接口
python benchmarks/locomo/run_eval.py \
  --sample conv-30 \
  --qa-profile vikingboat0411

# 同一 VikingBoat 0.4.11 prompt 和初始记忆注入，但不暴露工具
python benchmarks/locomo/run_eval.py \
  --sample conv-30 \
  --qa-profile vikingboat0411 \
  --no-tools

# 自然无工具对照：只保留完整初始记忆正文，不保留工具指令或 URI-only 条目
python benchmarks/locomo/run_eval.py \
  --sample conv-30 \
  --qa-profile vikingboat0411-natural-no-tools \
  --no-tools

# 基本用法 (不指定 --dataset 则自动查找/下载)
python benchmarks/locomo/run_eval.py \
  --echomem-url http://127.0.0.1:8010 \
  --llm-base-url https://ark.cn-beijing.volces.com/api/coding/v3 \
  --llm-model doubao-seed-2.0-pro \
  --llm-api-key YOUR_API_KEY

# 指定数据集路径
python benchmarks/locomo/run_eval.py \
  --dataset /path/to/locomo.json \
  --echomem-url http://127.0.0.1:8010 \
  --llm-base-url https://ark.cn-beijing.volces.com/api/coding/v3 \
  --llm-model doubao-seed-2.0-pro \
  --llm-api-key YOUR_API_KEY

# 指定 sample 和问题数量
python benchmarks/locomo/run_eval.py \
  --dataset /path/to/locomo.json \
  --sample sample_0 \
  --questions 10 \
  --llm-api-key YOUR_API_KEY

# 自定义检索参数
python benchmarks/locomo/run_eval.py \
  --dataset /path/to/locomo.json \
  --top-k 20 \
  --memory-budget-chars 12000 \
  --concurrency 8 \
  --llm-api-key YOUR_API_KEY
```

## 参数说明

> **参数归属**: benchmark 只定义数据集参数、Judge 参数和评测基础设施参数
> (`--concurrency`、`--out-dir`、`--allow-diagnostics`)。LLM 参数、
> QA 检索参数、记忆后端参数和插件特有参数均由所选插件及其记忆后端声明，
> 详见 `benchmarks/doc/设计意图.md`。切换 `--agent-plugin` 后可用参数会变化，
> 使用 `--help` 查看。

### 必填参数
| 参数 | 说明 |
|---|---|
| `--llm-api-key` | LLM API Key (也可通过环境变量 `LLM_API_KEY` 设置) |

### 数据集参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--dataset` | 内置 | 默认使用仓库中的 `benchmarks/locomo/data/locomo10.json` |
| `--sample` | `all` | 筛选 sample: `all` 或 sample_id |
| `--questions` | `0` | 限制 QA 数量 (0=全部) |
| `--question-ids` | (空) | 逗号分隔的 question/native/sample ID，在 `--questions` 前应用 |
| `--session-mode` | `auto` | 单 sample 按原始 session; 多 sample 各自合并; 也可显式选 `locomo`/`single` |
| `--max-sessions` | `0` | 每个 sample 最多导入多少个原始 session (0=全部) |

### 记忆后端参数 (通过插件声明)

记忆后端连接和身份管理参数，由所选插件通过 `add_memory_backend_args()` 声明。
不同插件暴露的参数不同：支持多后端的插件（vikingbot、echo_agent）额外暴露
`--memory-backend`；不支持记忆注入的插件（bare_llm）不声明任何后端参数。
完整设计说明见 `benchmarks/doc/设计意图.md`。

#### vikingbot 插件 (默认)
支持 `--memory-backend` 选择 echomem 或 openviking。选择 openviking 时需指定
`--echomem-url http://127.0.0.1:19080`。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--memory-backend` | `echomem` | 记忆后端选择: `echomem` 或 `openviking` |
| `--echomem-url` | `http://127.0.0.1:8010` | 后端 HTTP 地址 |
| `--echomem-auth-key` | (空) | 后端 X-Auth-Key (也可通过 `ECHOMEM_AUTH_KEY` 设置) |
| `--account` | `default` | 后端 account |
| `--user-id` | `default` | 后端 user_id |
| `--agent-id` | `default` | 后端 agent_id |
| `--workspace` | (空) | 后端 workspace 路径 |
| `--commit-timeout-s` | `0` | Commit 轮询超时 (秒)，0 表示无限等待 |
| `--commit-poll-interval-s` | `2.0` | Commit 轮询间隔 (秒) |
| `--timeout-s` | `60.0` | 后端 HTTP 请求超时 (秒) |
| `--max-retries` | `3` | 后端 HTTP 请求最大重试次数 |

#### echomem_mcp 插件
固定使用 EchoMem 后端，不暴露 `--memory-backend`。声明 QA 检索参数（`--top-k`
默认覆盖为 25）。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--echomem-url` | `http://127.0.0.1:8010` | EchoMem HTTP 地址 |
| `--echomem-auth-key` | (空) | X-Auth-Key (也可通过 `ECHOMEM_AUTH_KEY` 设置) |
| `--account` | `default` | 后端 account |
| `--user-id` | `default` | 后端 user_id |
| `--agent-id` | `default` | 后端 agent_id |
| `--workspace` | (空) | 后端 workspace 路径 |
| `--commit-timeout-s` | `0` | Commit 轮询超时 (秒)，0 表示无限等待 |
| `--commit-poll-interval-s` | `2.0` | Commit 轮询间隔 (秒) |
| `--timeout-s` | `60.0` | 后端 HTTP 请求超时 (秒) |
| `--max-retries` | `3` | 后端 HTTP 请求最大重试次数 |
| `--mcp-url` | `http://127.0.0.1:8001` | EchoMem MCP server URL |
| `--mcp-auth-key` | (空) | MCP server X-Auth-Key（留空时回退到 `--echomem-auth-key`） |
| `--mcp-max-iterations` | `50` | 每个问题的最大工具调用迭代次数 |
| `--tool-calling` / `--no-tool-calling` | `True` | 是否启用 LLM 工具调用 |
| `--search-in-tools` / `--no-search-in-tools` | `True` | 是否将 `memory_query` 加入工具定义 |
| `--manual-search` / `--no-manual-search` | `True` | 是否在每轮 LLM 前预取记忆 |
| `--mcp-read-mode` | `allow` | 对话读取策略：`disabled`/`allow`/`require` |

#### openviking_mcp 插件
固定使用 OpenViking 后端，不暴露 `--memory-backend`。使用时需指定
`--echomem-url http://127.0.0.1:19080`。声明 QA 检索参数。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--echomem-url` | `http://127.0.0.1:8010` | OpenViking HTTP 地址 (实际使用时需改为 19080 端口) |
| `--echomem-auth-key` | (空) | 后端 API Key |
| `--account` | `default` | 后端 account |
| `--user-id` | `default` | 后端 user_id |
| `--agent-id` | `default` | 后端 agent_id |
| `--workspace` | (空) | 后端 workspace 路径 |
| `--commit-timeout-s` | `0` | Commit 轮询超时 (秒)，0 表示无限等待 |
| `--commit-poll-interval-s` | `2.0` | Commit 轮询间隔 (秒) |
| `--timeout-s` | `60.0` | 后端 HTTP 请求超时 (秒) |
| `--max-retries` | `3` | 后端 HTTP 请求最大重试次数 |
| `--ov-max-iterations` | `10` | 每个问题的最大工具调用迭代次数 |
| `--ov-search-limit` | `8` | 每次 `memory_search` 返回的最大结果数 |
| `--tool-calling` / `--no-tool-calling` | `True` | 是否启用 LLM 工具调用 |
| `--search-in-tools` / `--no-search-in-tools` | `True` | 是否将 `memory_search` 加入工具定义 |
| `--manual-search` / `--no-manual-search` | `True` | 是否在每轮 LLM 前预取记忆 |

#### echo_agent 插件
支持 `--memory-backend` 选择 echomem 或 openviking。`--echomem-auth-key` 留空
时自动从 echoagent 插件解析；`--agent-id` 为 `default` 时自动设为 `echoagent`。
不声明 QA 检索参数（QA 走 EchoAgent 管线）。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--memory-backend` | `echomem` | 记忆后端选择: `echomem` 或 `openviking` |
| `--echomem-url` | `http://127.0.0.1:8010` | 后端 HTTP 地址 |
| `--echomem-auth-key` | (空) | 后端 X-Auth-Key (留空时自动解析) |
| `--account` | `default` | 后端 account |
| `--user-id` | `default` | 后端 user_id |
| `--agent-id` | `default` | 后端 agent_id (默认自动设为 `echoagent`) |
| `--workspace` | (空) | 后端 workspace 路径 |
| `--commit-timeout-s` | `0` | Commit 轮询超时 (秒)，0 表示无限等待 |
| `--commit-poll-interval-s` | `2.0` | Commit 轮询间隔 (秒) |
| `--timeout-s` | `60.0` | 后端 HTTP 请求超时 (秒) |
| `--max-retries` | `3` | 后端 HTTP 请求最大重试次数 |
| `--echoagent-url` | `http://127.0.0.1:31020` | EchoAgent 后端地址 (环境变量 `ECHOAGENT_URL`) |
| `--username` | `test_user` | EchoAgent 登录用户名 (环境变量 `ECHOAGENT_TEST_USERNAME`) |
| `--password` | (空) | EchoAgent 登录密码 (环境变量 `ECHOAGENT_TEST_PASSWORD`) |
| `--memory-engine-endpoint` | `http://127.0.0.1:31030` | echoagent 插件地址 (环境变量 `GLOBAL_MEMORY_ENGINE_ENDPOINT`) |

#### echoagent_live 插件
与 `echo_agent` 共享 `EchoAgentClient`，但不模拟打字、不触发 prefill 管线。
默认地址指向外网部署。`--echomem-auth-key` 留空时自动从 echoagent 插件解析；
`--agent-id` 为 `default` 时自动设为 `echoagent`。不声明 QA 检索参数。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--memory-backend` | `echomem` | 记忆后端选择: `echomem` 或 `openviking` |
| `--echomem-url` | `http://127.0.0.1:8010` | 后端 HTTP 地址 |
| `--echomem-auth-key` | (空) | 后端 X-Auth-Key (留空时自动解析) |
| `--account` | `default` | 后端 account |
| `--user-id` | `default` | 后端 user_id |
| `--agent-id` | `default` | 后端 agent_id (默认自动设为 `echoagent`) |
| `--workspace` | (空) | 后端 workspace 路径 |
| `--commit-timeout-s` | `0` | Commit 轮询超时 (秒)，0 表示无限等待 |
| `--commit-poll-interval-s` | `2.0` | Commit 轮询间隔 (秒) |
| `--timeout-s` | `60.0` | 后端 HTTP 请求超时 (秒) |
| `--max-retries` | `3` | 后端 HTTP 请求最大重试次数 |
| `--echoagent-url` | `https://echo-agent.online` | EchoAgent 后端地址 (环境变量 `ECHOAGENT_URL`) |
| `--echoagent-api-prefix` | `/api` | API 路径前缀（外网反代 `/api` -> `/v1`；本地直连 `/v1`）(环境变量 `ECHOAGENT_API_PREFIX`) |
| `--username` | `test_user` | EchoAgent 登录用户名 (环境变量 `ECHOAGENT_TEST_USERNAME`) |
| `--password` | (空) | EchoAgent 登录密码 (环境变量 `ECHOAGENT_TEST_PASSWORD`) |
| `--memory-engine-endpoint` | `http://8.134.127.8:31030` | echoagent 插件地址 (环境变量 `GLOBAL_MEMORY_ENGINE_ENDPOINT`) |

#### bare_llm 插件
不声明任何记忆后端参数，适用于无记忆系统基线测试。声明 QA 检索参数。

### LLM 参数 (通过插件声明)
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--llm-base-url` | (空) | LLM API base URL (也可通过 `LLM_BASE_URL` 设置) |
| `--llm-model` | `doubao-seed-2.0-pro` | LLM 模型名 |
| `--llm-api-key` | (空) | LLM API Key (也可通过 `LLM_API_KEY` 设置) |
| `--llm-temperature` | `0.7` | 回答模型生成温度；profile 可选择不显式发送 temperature |
| `--llm-max-tokens` | profile 决定 | LoCoMo 历史 profile 的最大生成 token 数 |
| `--llm-timeout-s` | `120.0` | LLM 请求超时 (秒) |
| `--llm-retries` | `3` | LLM 请求重试次数 |

### QA 检索参数 (通过插件声明)
> 由 `bare_llm`、`echomem_mcp`、`openviking_mcp`、`vikingbot` 声明；
> `echo_agent` 和 `echoagent_live` 不声明（QA 走 EchoAgent 管线）。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--top-k` | profile 决定 | 两个保留 profile 均为 `25` |
| `--memory-budget-chars` | `6000` | 总记忆字符预算 |
| `--question-timeout-s` | profile 决定 | 两个保留 profile 均为 `600` 秒；0 表示不增加总限制 |

### VikingBot 插件参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--tool-search-limit` | profile 决定 | 两个保留 profile 均为 `25` |
| `--initial-min-score` | profile 决定 | VikingBoat 0.4.11 profiles=`0.1` |
| `--tool-min-score` | profile 决定 | VikingBoat 0.4.11 profiles=`0.35` |
| `--tool-search-pool-multiplier` | profile 决定 | 两个保留 profile 均为 `1` |
| `--tool-set` | profile 决定 | VikingBoat 0.4.11 profiles=`vikingbot_echo_native` |
| `--tools` / `--no-tools` | `--tools` | 是否向回答模型暴露 profile 的记忆工具；关闭后保留相同 prompt 和初始检索注入，只执行一次模型调用 |
| `--user-memory-budget-chars` | `4000` | user memory prompt 预算 |
| `--agent-memory-budget-chars` | `2000` | agent memory prompt 预算 |
| `--max-iterations` | `50` | 单题最大模型/tool-loop 迭代数 |
| `--vikingbot-workspace` | 仓库内置历史 bootstrap | 默认使用 `plugins/vikingbot/bootstrap/` 中固定的原始 `SOUL.md` 和 `TOOLS.md` 快照 |

### 评测参数 (benchmark 自身)
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--agent-plugin` | `vikingbot` | QA 阶段使用的 agent 插件名，见 `plugins/` 目录。切换后可用参数会变化 |
| `--qa-profile` | 自动 | `--tools` 默认选择 `vikingboat0411`；`--no-tools` 默认选择 `vikingboat0411-natural-no-tools`。显式指定时可覆盖 |
| `--qa-prompt-file` | (空) | 将本地 UTF-8 文件追加到所选 profile 的 system prompt；`summary.json` 和 resume manifest 仅记录文件名和 SHA-256 |
| `--checkpoint-interval` | `10` | 每完成 N 题写一次 `qa_results.checkpoint.csv`；0 表示关闭 |
| `--resume` | (空) | **统一续跑**：从先前运行目录或 CSV 恢复——复用身份，跳过已完成 import batch（只补中断/缺失的），恢复健康 QA 答案，复用一致 Judge 判定；只跑缺失/失败部分。summary/blackbox 指标对合并后的整轮累计（token/延迟/精度不会只算本轮） |
| `--resume-qa` | (空) | 旧参数，语义同 `--resume`（被取代，仅保留兼容） |
| `--reuse-memory-from` | (空) | 旧参数：只复用身份+已注入记忆、QA/Judge 全量重跑（指标只算本轮；被 `--resume` 取代，仅保留兼容） |
| `--concurrency` | `4` | QA 并发数 |
| `--out-dir` | `results` | 结果目录 |
| `--allow-diagnostics` | false | 导入未完成或 provenance 不一致仍继续；仅限诊断 |

### Judge 参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--judge-model` | (同 `--llm-model`) | Judge LLM 模型名 |
| `--judge-api-key` | (同 `--llm-api-key`) | Judge API Key |
| `--judge-base-url` | (同 `--llm-base-url`) | Judge base URL |
| `--judge-concurrency` | `4` | Judge 并发数；结果仍按原始题目顺序写入 |
| `--judge-checkpoint-interval` | `10` | 每完成 N 题写一次 `judge_results.checkpoint.csv`；0 表示关闭 |
| `--resume-judge` | (空) | 旧参数，已并入 `--resume`（仅保留兼容） |

## 输出文件

每次评测在 `benchmarks/locomo/results/<timestamp>/` 下生成:
- `config.json` - 评测配置
- `run.log` - 完整日志
- `import_results.csv` - 导入结果 (sample_id, session_id, status, messages, elapsed)
- `memory_provenance.json` - 数据集 SHA-256、预期/实际 session 数和实际 session URI manifest
- `qa_results.csv` - QA 结果，包含 tool_call_count、iterations、qa_profile
- `qa_results.checkpoint.csv` - 运行中定期更新的可恢复 QA 快照
- `qa_resume_manifest.json` - 恢复兼容性所需的数据集、身份、模型、QA 参数、agent_options 和本地 prompt/tool/runtime contract hash
- `judge_results.csv` - Judge 结果 (question_id, verdict, reasoning)
- `judge_results.checkpoint.csv` - 运行中定期更新的 Judge 快照
- `judge_resume_manifest.json` - Judge 模型和 prompt 指纹，用于防止混合判分口径
- `diagnosis.json` - 失败分类、检索覆盖率、可重试/缺失/重复题目 ID
- `retrieval_traces.jsonl` - 每题检索内容和失败归因 trace
- `agent_traces/*.json` - VikingBot 初始 prompt、逐轮模型消息、请求/响应模型身份、工具协议 hash、工具参数/结果、原始与清洗后答案
- `summary.json` - 汇总指标，包含 memory_source、qa_profile、agent_options、served model ids、tool protocol hash、tool_call_total、avg_iterations 和 diagnosis 摘要
- `strict_blackbox_metrics.json` - 仅使用外部可观测状态、延迟、重试和 token usage 的指标
- `strict_blackbox_report.md` - strict black-box Markdown 报告

## 独立分析与恢复

```bash
# 从已有结果重建 strict black-box 报告
python benchmarks/locomo/blackbox.py \
  --qa /path/to/run/qa_results.csv \
  --judge /path/to/run/judge_results.csv \
  --import-results /path/to/run/import_results.csv \
  --summary /path/to/run/summary.json \
  --out-dir /path/to/report

# 对比两个运行，输出 JSON/CSV/Markdown
python benchmarks/locomo/compare.py \
  --left /path/to/baseline \
  --right /path/to/candidate \
  --out-dir /path/to/comparison

# 查看失败或缺失题并生成重跑参数
python benchmarks/locomo/retry.py --help

# 检查检索证据 JSON
python scripts/validate_evidence.py \
  --input /path/to/run/qa_results.csv \
  --strict
```
