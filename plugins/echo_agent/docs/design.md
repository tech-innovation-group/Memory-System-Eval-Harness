# echo_agent 插件

## 设计意图

完整 EchoAgent + EchoMem 管线插件，是动态评测的默认插件。QA 阶段走完整的 EchoAgent 后端流程（登录、创建会话、发消息、SSE 流式接收），而非直接调 LLM。

它存在的意义是评测**真实 agent 的端到端效果**，包括：

- EchoAgent 的 prefill 记忆管线（打字期 probe -> KV cache 预热 -> 正式生成）
- EchoAgent 的会话管理、上下文组装、工具调用
- 记忆召回时机决策（ProbeSplitter 在 EchoMem 侧判定）

## 架构

```
记忆注入 (memory plugin)  ----->  EchoMem (直连, 绕过 EchoAgent)
send_message()             ----->  EchoAgent 后端  ----->  LLM
                                  ↕
                            echoagent 插件 (31030)  ----->  EchoMem (检索)
```

**注入不经 EchoAgent**：记忆注入由 memory 插件的 client 完成（open/add/commit/poll），直接写入 EchoMem，因为注入只需要建索引，不需要 agent 参与。

**QA 走 EchoAgent**：消息发送到 EchoAgent 后端，后端组装上下文、决定是否召回记忆、调 LLM、通过 SSE 流式返回。这条路径上的 prefill 管线、召回时机决策、KV cache 预热都会真实发生。

## 身份映射

`setup()` 中自动解析 `auth_key`：

1. 优先使用 CLI 传入的 `echomem_auth_key`
2. 若为空，调 EchoAgent 的 `get_memory_auth_key` 接口，从 echoagent 插件（31030）获取
3. 解析成功后写回 `config["echomem_auth_key"]`，确保注入和检索使用同一身份

`agent_id` 默认为 `"echoagent"`（与 echoagent 插件 31030 的配置一致），确保注入的记忆能被 QA 阶段检索到。

**禁止创建隔离租户**：`echo_agent` 插件不调用 `provision_isolated_identity`。EchoAgent 后端做记忆召回时，通过 31030 用登录用户的 UUID 作为 `userId` 解析 auth_key。如果注入时创建隔离租户，记忆会写入与召回不同的租户，导致召回全空。注入必须始终使用 `get_memory_auth_key`（内部传 `self.user_uuid`）解析到的同一 auth_key。

## send_message 返回值契约

`send_message` 返回的 `AgentResponse.extra` 必须包含 `qa_profile` 字段。benchmark QA runner（`shared/benchmark_qa.py`）从 `resp.extra["qa_profile"]` 读取 QA profile，写入 QA 结果 CSV。`--resume-qa` 恢复时，`resume.py` 校验 CSV 中的 `qa_profile` 与 manifest 一致；缺失会导致 profile mismatch 错误。

`echo_agent` 的 `qa_profile` 继承自 `AgentPlugin.qa_profile` 属性，默认返回 `descriptor.id`（即 `"echo_agent"`）。`send_message` 在所有返回路径（成功、错误、异常）中都设置 `extra={"qa_profile": self.qa_profile}`。

成功路径的 `AgentResponse` 还携带后端 SSE done 事件注入的指标。`extra` 中的 `elapsed_s`/`retrieval_latency_s`/`llm_latency_s`（ms→s，除以 1000）、`tool_call_count`/`iterations` 与 `trace`（含原始 `metrics` 快照、`model_name`、`finish_reason`、`tool_audit`）均来自 done 事件的 `metrics` 字段（`tool_audit` 来自 done 事件的 `toolAudit`，元素为 `{name, callId, arguments}`）。`memory_items` 优先级为 prefill（typing 模拟产生）> done 事件的 `memoryItems`。token 类字段（`prompt_tokens`/`completion_tokens`/`cached_tokens`/`ttft_ms`）优先取 `metrics` 中的 snake_case 值，回退到 done 事件顶层的 camelCase/snake_case。

## 会话管理

**动态评测**：`run_eval` 先调 `create_session(title)` 创建 EchoAgent 会话，再将返回的 session_id 传给 `send_message`。

**Benchmark 评测**：benchmark 传入的 `session_id` 是 EchoMem 记忆会话 ID（用于记忆注入），不是 EchoAgent 会话 ID。当 `session_mode=locomo` 时，每个 sample 有多个记忆会话，`session_id` 为空字符串。`send_message` 在 `session_id` 为空时自动调 `client.create_session()` 创建新的 EchoAgent 会话，确保每条 QA 问题在独立上下文中执行。记忆检索不依赖 EchoAgent 会话历史，而是通过 echoagent 插件（31030）查询 EchoMem。

## 打字模拟

`supports_typing_simulation = True`。`simulate_typing()` 模拟用户逐字输入，触发 EchoAgent 的 prefill 管线：

- **逐字模式**（`speed_ms >= 50`）：每个字符发一次 `prefetch_tick`，模拟真实打字节奏
- **快速模式**（`speed_ms < 50`）：发一次 tick + 一次 finalize，不逐字延迟

typing 状态（`_pending_turn_id`、`_typing_committed`、`_typing_memory_items`）在 `send_message()` 中被消费并清空。这使得 `send_message` 能复用 prefill 预热的结果。

## 配置参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `echoagent_url` | `http://127.0.0.1:31020` | EchoAgent 后端地址 |
| `username` / `password` | `test_user` / (空) | EchoAgent 登录凭据 |
| `memory_engine_endpoint` | `http://127.0.0.1:31030` | echoagent 插件地址 |
| `echomem_url` | `http://127.0.0.1:8010` | EchoMem 地址（注入用） |
| `echomem_auth_key` | (自动解析) | X-Auth-Key |
| `agent_id` | `echoagent` | EchoMem agent_id |
| `commit_timeout_s` / `commit_poll_interval_s` | `0` / `2.0` | commit 轮询 |

## 使用方式

```bash
# 动态评测（默认插件）
python dynamic/run_eval.py \
  --echoagent-url http://127.0.0.1:31020 \
  --username test_user --password YOUR_PASSWORD \
  --evaluator-api-key YOUR_KEY

# benchmark（需 --concurrency 1，因有 typing 状态）
# echo_agent 不提供 --llm-* 参数，judge LLM 需通过 --judge-* 指定
python benchmarks/locomo/run_eval.py \
  --agent-plugin echo_agent \
  --echoagent-url http://127.0.0.1:31020 \
  --username test_user --password YOUR_PASSWORD \
  --judge-api-key YOUR_KEY \
  --concurrency 1
```

## 线程安全

**不支持并发**。`simulate_typing()` 写入实例状态（`_pending_turn_id` 等），`send_message()` 读取并清空。多线程并发会导致状态错乱。benchmark 使用时必须设 `--concurrency 1`。

## 依赖

- EchoAgent 后端（31020）必须运行
- echoagent 插件（31030）必须运行
- EchoMem（8010）必须运行
- 三者缺一不可，启动顺序见工作区根 `AGENTS.md`
