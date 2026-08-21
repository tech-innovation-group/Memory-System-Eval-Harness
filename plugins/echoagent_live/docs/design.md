# echoagent_live 插件

## 设计意图

EchoAgent 外网部署评测插件。QA 走完整的 EchoAgent 后端流程（登录、创建会话、发消息、SSE 流式接收），但**不模拟打字、不触发 prefill 管线**。

它存在的意义是评测**外网部署的 EchoAgent 端到端效果**，在不含打字模拟的前提下验证：
- EchoAgent 的会话管理、上下文组装、记忆召回
- 外网服务的连通性与响应延迟
- 记忆注入后 QA 阶段能否正确检索

与 `echo_agent` 的区别：`echo_agent` 面向本地部署，支持打字模拟（prefill tick/finalize），有 typing 实例状态、不支持并发；`echoagent_live` 面向外网部署，不做打字模拟，无实例状态、线程安全。

## 架构

```
记忆注入 (memory plugin)  ----->  EchoMem (直连, 绕过 EchoAgent)
send_message()             ----->  EchoAgent 后端  ----->  LLM
                                  ↕
                            echoagent 插件 (31030)  ----->  EchoMem (检索)
```

**注入不经 EchoAgent**：记忆注入由 memory 插件的 client 完成（open/add/commit/poll），直接写入 EchoMem，因为注入只需要建索引，不需要 agent 参与。

**QA 走 EchoAgent**：消息发送到 EchoAgent 后端，后端组装上下文、决定是否召回记忆、调 LLM、通过 SSE 流式返回。与 `echo_agent` 不同的是，`send_message` 不传 `prefetchClientTurnId`，因此不触发 prefill 管线。

## 与 echo_agent 的差异

| 方面 | echo_agent | echoagent_live |
|---|---|---|
| 部署目标 | 本地 (127.0.0.1) | 外网 (echo-agent.online) |
| API 前缀 | `/v1`（直连后端） | `/api`（nginx 反代 `/api` -> `/v1`） |
| 打字模拟 | 支持 (prefill tick/finalize) | 不支持 |
| prefill 管线 | 触发 | 不触发 |
| send_message 第 4 参数 | 传 `pending_turn_id` | 不传（默认 `""`） |
| typing 实例状态 | 有 (`_pending_turn_id` 等) | 无 |
| 线程安全 | 否 | 是 |

## 身份映射

`setup()` 中自动解析 `auth_key`：

1. 优先使用 CLI 传入的 `echomem_auth_key`
2. 若为空，调 EchoAgent 的 `get_memory_auth_key` 接口，从 echoagent 插件（31030）获取
3. 解析成功后写回 `config["echomem_auth_key"]`，确保注入和检索使用同一身份

`agent_id` 默认为 `"echoagent"`（与 echoagent 插件 31030 的配置一致），确保注入的记忆能被 QA 阶段检索到。

**禁止创建隔离租户**：`echoagent_live` 插件不调用 `provision_isolated_identity`。EchoAgent 后端做记忆召回时，通过 31030 用登录用户的 UUID 作为 `userId` 解析 auth_key。如果注入时创建隔离租户，记忆会写入与召回不同的租户，导致召回全空。注入必须始终使用 `get_memory_auth_key`（内部传 `self.user_uuid`）解析到的同一 auth_key。

## send_message 返回值契约

`send_message` 返回的 `AgentResponse.extra` 必须包含 `qa_profile` 字段。benchmark QA runner（`shared/benchmark_qa.py`）从 `resp.extra["qa_profile"]` 读取 QA profile，写入 QA 结果 CSV。`--resume-qa` 恢复时，`resume.py` 校验 CSV 中的 `qa_profile` 与 manifest 一致；缺失会导致 profile mismatch 错误。

`echoagent_live` 的 `qa_profile` 继承自 `AgentPlugin.qa_profile` 属性，默认返回 `descriptor.id`（即 `"echoagent_live"`）。`send_message` 在所有返回路径（成功、错误、异常）中都设置 `extra={"qa_profile": self.qa_profile}`。

成功路径的 `AgentResponse` 还携带后端 SSE done 事件注入的指标。`extra` 中的 `elapsed_s`/`retrieval_latency_s`/`llm_latency_s`（ms→s，除以 1000）、`tool_call_count`/`iterations` 与 `trace`（含原始 `metrics` 快照、`model_name`、`finish_reason`、`tool_audit`）均来自 done 事件的 `metrics` 字段（`tool_audit` 来自 done 事件的 `toolAudit`，元素为 `{name, callId, arguments}`）。`prefetch_committed` 恒为 `False`（echoagent_live 不触发 prefill），`memory_items` 来自 done 事件的 `memoryItems`。token 类字段（`prompt_tokens`/`completion_tokens`/`cached_tokens`/`ttft_ms`）优先取 `metrics` 中的 snake_case 值，回退到 done 事件顶层的 camelCase/snake_case。

## 会话管理

**动态评测**：`run_eval` 先调 `create_session(title)` 创建 EchoAgent 会话，再将返回的 session_id 传给 `send_message`。

**Benchmark 评测**：benchmark 传入的 `session_id` 是 EchoMem 记忆会话 ID（用于记忆注入），不是 EchoAgent 会话 ID。当 `session_mode=locomo` 时，每个 sample 有多个记忆会话，`session_id` 为空字符串。`send_message` 在 `session_id` 为空时自动调 `client.create_session()` 创建新的 EchoAgent 会话，确保每条 QA 问题在独立上下文中执行。记忆检索不依赖 EchoAgent 会话历史，而是通过 echoagent 插件（31030）查询 EchoMem。

## 配置参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `echoagent_url` | `https://echo-agent.online` | EchoAgent 后端地址（外网域名） |
| `echoagent_api_prefix` | `/api` | API 路径前缀（外网 nginx 反代 `/api` -> `/v1`；本地直连用 `/v1`） |
| `username` / `password` | `test_user` / (空) | EchoAgent 登录凭据 |
| `memory_engine_endpoint` | `http://8.134.127.8:31030` | echoagent 插件地址 |
| `echomem_url` | `http://8.134.127.8:8010` | EchoMem 地址（注入用，需显式指定） |
| `echomem_auth_key` | (自动解析) | X-Auth-Key |
| `agent_id` | `echoagent` | EchoMem agent_id |
| `commit_timeout_s` / `commit_poll_interval_s` | `0` / `2.0` | commit 轮询 |

### 外网反代路由

外网部署使用 nginx 单域名路径路由：

| 路径 | 代理目标 | 说明 |
|---|---|---|
| `/api/...` | `http://127.0.0.1:31020/v1/...` | 后端 API（路径重写 `/api` -> `/v1`） |
| 其他 (`/`, `/assets/...`) | `http://127.0.0.1:31010` | 前端 SPA |

因此 `echoagent_live` 默认 `api_prefix="/api"`，`EchoAgentClient` 构造 URL 时用 `{base_url}{api_prefix}/auth/login` = `https://echo-agent.online/api/auth/login`。本地直连后端时用 `api_prefix="/v1"`（即 `echo_agent` 插件的默认值）。

> **注意**：`add_memory_backend_args` 的 `--echomem-url` 默认值为 `http://127.0.0.1:8010`（本地地址）。使用 echoagent_live 时需通过 CLI 或环境变量显式指定外网地址 `--echomem-url http://8.134.127.8:8010`。

## 使用方式

```bash
# 动态评测 -- EchoMem 后端（默认）
python -m dynamic.run_eval \
  --agent-plugin echoagent_live \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-api-key YOUR_API_KEY \
  --scenario-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --scenario-api-key YOUR_API_KEY \
  --echoagent-url https://echo-agent.online \
  --username test_user \
  --password YOUR_PASSWORD \
  --memory-engine-endpoint http://8.134.127.8:31030 \
  --echomem-url http://8.134.127.8:8010 \
  --num-memories 5 --num-queries 10

# 动态评测 -- OpenViking 后端
python -m dynamic.run_eval \
  --agent-plugin echoagent_live \
  --memory-backend openviking \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-api-key YOUR_API_KEY \
  --scenario-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --scenario-api-key YOUR_API_KEY \
  --echoagent-url https://echo-agent.online \
  --username test_user \
  --password YOUR_PASSWORD \
  --memory-engine-endpoint http://8.134.127.8:31030 \
  --echomem-url http://8.134.127.8:19080 \
  --num-memories 5 --num-queries 10

# LoCoMo benchmark 评测
python benchmarks/locomo/run_eval.py \
  --agent-plugin echoagent_live \
  --echoagent-url https://echo-agent.online \
  --username test_user \
  --password YOUR_PASSWORD \
  --memory-engine-endpoint http://8.134.127.8:31030 \
  --echomem-url http://8.134.127.8:8010 \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash \
  --llm-api-key YOUR_API_KEY \
  --sample conv-30 \
  --commit-timeout-s 0 \
  --llm-timeout-s 600
```

## 线程安全

**线程安全**。无打字模拟，无实例状态，`send_message` 不依赖前序调用写入的状态。支持并发 QA。

## 依赖

- EchoAgent 后端（31020）必须运行
- echoagent 插件（31030）必须运行
- EchoMem（8010）必须运行
- 三者缺一不可
