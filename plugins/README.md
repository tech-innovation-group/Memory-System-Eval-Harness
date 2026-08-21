# Agent Plugins

Agent 插件让评测框架支持评测不同的 agent。评测流程只调用 `AgentPlugin` 接口, 不直接接触 agent 特定的 HTTP API。记忆注入也是 agent 插件的一部分: 评测平台不感知记忆后端的存在。

## 评测生命周期

```
setup(config)
  -> inject_memories(memories, backend=...)
  -> (create_session -> [simulate_typing] -> send_message)*
  -> getlog
  -> teardown
```

1. **setup**: 初始化 agent 客户端和记忆后端 (登录、身份解析等)。
2. **inject_memories**: 把全部记忆注入指定后端 (echomem / openviking)。不支持的插件 (如 bare_llm) 不覆盖此方法, 默认 no-op 返回 session_id。
3. **create_session**: 随机创建新会话。
4. **simulate_typing** (可选): 如果 `supports_typing_simulation` 返回 True, 评测平台模拟打字触发 prefill。
5. **send_message**: 发送完整用户查询, 接收回复。
6. **teardown**: 结束评测后做资源清理。

## 目录结构

```
plugins/
  __init__.py          # 导出 AgentPlugin, AgentResponse, TypingResult, load_agent_plugin, get_plugin_class
  base.py              # AgentPlugin ABC + AgentResponse / TypingResult
  registry.py          # load_agent_plugin(name, config) / get_plugin_class(name) -- 按名动态加载
  vikingbot/           # VikingBot 工具调用 agent (LoCoMo benchmark 默认)
    __init__.py
    plugin.py          # VikingBotPlugin (inject_memories 支持 echomem/openviking)
    prompting.py       # 系统提示词构建
    runtime.py         # 工具调用循环
    tools.py           # 工具定义
    bootstrap/         # 启动脚本
    docs/design.md     # 使用说明与设计意图
  echo_agent/          # EchoAgent + EchoMem 完整管线 (动态评测默认)
    __init__.py
    plugin.py          # EchoAgentPlugin (inject_memories 支持 echomem/openviking)
    client.py          # EchoAgentClient (HTTP 客户端)
    docs/design.md     # 使用说明与设计意图
  echoagent_live/      # EchoAgent 外网部署评测 (无打字模拟)
    __init__.py
    plugin.py          # EchoAgentLivePlugin (复用 EchoAgentClient, 无 prefill)
    docs/design.md     # 使用说明与设计意图
  echomem_mcp/         # LLM 通过 EchoMem MCP 工具检索记忆
    __init__.py
    plugin.py          # EchoMemMCPPlugin
    mcp_client.py      # MCP 客户端 (JSON-RPC + SSE)
    runtime.py         # MCP 工具定义 + 系统提示词
    docs/design.md     # 设计意图
  openviking_mcp/      # LLM 通过 MemoryClient 工具检索记忆 (OpenViking 等)
    __init__.py
    plugin.py          # OpenVikingMCPPlugin
    runtime.py         # 工具定义 + 执行 + 系统提示词
    docs/design.md     # 设计意图
  bare_llm/            # 纯 LLM 基线 (system prompt + 用户查询)
    __init__.py
    plugin.py          # BareLLMPlugin
    docs/design.md     # 使用说明与设计意图
```

## 可用插件

| 插件 | 说明 | QA 策略 | 记忆注入 | 打字模拟 | 创建会话 | 线程安全 | 依赖 |
|---|---|---|---|---|---|---|---|
| `vikingbot` | VikingBot 工具调用 agent (LoCoMo 默认) | 工具调用循环 (直连 EchoMem REST) | echomem / openviking | 不支持 | 支持 | 是 | LLM API + 记忆后端 |
| `echo_agent` | EchoAgent + EchoMem 完整管线 | 不支持 benchmark QA | echomem / openviking | 支持 (prefetch tick/finalize) | 支持 | 否 (有 typing 实例状态) | EchoAgent 后端 + 记忆后端 |
| `echoagent_live` | EchoAgent 外网部署评测 (无打字模拟) | 不支持 benchmark QA | echomem / openviking | 不支持 | 支持 | 是 | EchoAgent 后端 + 记忆后端 |
| `echomem_mcp` | LLM 通过 MCP 工具检索记忆 | 工具调用循环 (MCP 协议) | echomem | 不支持 | 支持 | 是 | LLM API + EchoMem MCP 服务 (8001) |
| `openviking_mcp` | LLM 通过 MemoryClient 工具检索记忆 | 工具调用循环 (MemoryClient 协议) | openviking | 不支持 | 支持 | 是 | LLM API + 记忆后端 |
| `bare_llm` | 纯 LLM 基线 (system prompt + 用户查询) | 纯 LLM 调用 (无记忆检索) | 不支持 | 不支持 | 支持 | 是 | 仅 LLM API |

> **线程安全**: benchmark 评测使用 `ThreadPoolExecutor` 并发 QA。`vikingbot`、`echomem_mcp`、`openviking_mcp`、`bare_llm` 和 `echoagent_live` 的调用是无状态的, 支持并发。`echo_agent` 有 typing 实例状态, benchmark 使用时需 `--concurrency 1`。

## 接口

```python
class AgentPlugin(ABC):
    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None
    def setup(self, config: dict) -> None
    def inject_memories(self, memories: list[dict], *, backend: str = "echomem", session_id: str = "") -> str
    def create_session(self, title: str = "") -> str
    def send_message(self, session_id: str, message: str, context_path: str = "/", *, extra: dict | None = None) -> AgentResponse
    @property
    def supports_typing_simulation(self) -> bool
    def simulate_typing(self, session_id, context_path, text, speed_ms, jitter_ms) -> TypingResult | None
    def getlog(self) -> str
    def teardown(self) -> None
```

- `add_arguments(parser)` (classmethod): 声明该插件所需的 CLI 参数。`run_eval.py` 根据 `--agent-plugin` 值动态调用此方法。默认空实现。
- `setup(config)`: 初始化客户端 (登录、解析凭据、创建 memory_client)。`config` 是所有 CLI 参数的扁平 dict。**每个插件必须在 `setup` 中设置 `self.memory_client`**：有记忆后端的插件设置 `EchoMemClient` / `OpenVikingClient`，无记忆后端的插件（如 `bare_llm`）设置 `NullMemoryClient`（no-op，`health()` 直接返回 ok）。评测框架在插件加载后无条件调用 `agent_plugin.memory_client.health()` 做健康检查，因此 `self.memory_client` 不能为 `None`。
- `inject_memories(memories, backend=)`: 把全部记忆注入指定后端。`backend` 参数选择 echomem 或 openviking。返回 session_id。不支持的插件不覆盖此方法 (默认 no-op 返回 session_id)。
- `create_session(title)`: 创建 QA 会话。返回 session ID。
- `send_message(session_id, message, context_path, *, extra)`: 发送消息, 返回 `AgentResponse`。`extra` dict 携带 benchmark 上下文 (question_time, question_id 等)，动态模式为 None。
- `supports_typing_simulation` (property): 是否支持模拟打字。
- `simulate_typing(...)`: 模拟打字触发 prefill。返回 `TypingResult` 或 `None`。
- `getlog()`: 获取 agent/记忆后端日志, 返回 JSON 字符串。评测结束时由 runner 保存到结果目录。echomem 后端按本运行租户/user 拉取 EchoMem `GET /api/logs`（注入记忆的全部日志 + 本次评测 QA 日志），不拉全局；`auth.mode=x_auth_key` 时用 `--echomem-log-access-key` 提供专用日志访问密钥。
- `teardown()`: 释放资源。

`AgentResponse` 字段: `text`, `ttft_ms`, `prompt_tokens`, `completion_tokens`, `cached_tokens`, `prefetch_committed`, `memory_items`, `error`, `extra`。

## 记忆注入

记忆注入是 `AgentPlugin` 接口的一部分。`inject_memories` 方法接收全部记忆, 通过 `backend` 参数选择后端 (echomem / openviking), 在内部调用 `self.memory_client` 的 `open_session` / `add_message` / `commit_session` / `poll_commit` 完成注入。

支持多后端的插件 (echo_agent, vikingbot) 通过 `--memory-backend` 参数选择后端。单后端插件 (echomem_mcp, openviking_mcp) 忽略 `backend` 参数, 始终使用自己的后端。

记忆客户端类:
- `backends/echomem/client.py` -> `EchoMemClient`
- `backends/openviking/client.py` -> `OpenVikingClient`
- `backends/memory_types.py` -> `NullMemoryClient` (用于不支持注入的插件)

共享的记忆类型 (`CommitResult`, `SearchResult`, `MemoryClient` Protocol, `BaseHTTPMemoryClient`) 在 `backends/memory_types.py` 中定义。记忆后端 CLI 参数通过 `backends/memory_args.py` 中的 `add_memory_backend_args()` 添加。

## 新增插件

1. 创建目录 `plugins/<name>/`
2. 创建 `__init__.py` (空即可)
3. 创建 `plugin.py`, 实现 `AgentPlugin` 子类:

```python
from plugins.base import AgentPlugin, AgentResponse

class MyAgentPlugin(AgentPlugin):
    def setup(self, config: dict) -> None:
        ...
    def inject_memories(self, memories, *, backend="echomem", session_id=""):
        ...
    def create_session(self, title=""):
        ...
    def send_message(self, session_id, message, context_path="/", *, extra=None):
        ...
    def getlog(self) -> str:
        ...
```

4. 实现 `add_arguments` classmethod, 声明该插件所需的 CLI 参数。可复用 `backends/memory_args.py` 中的 `add_memory_backend_args()` 添加记忆后端连接参数。

`registry.py` 自动扫描 `plugins.<name>.plugin` 模块中 `AgentPlugin` 的子类, 无需手动注册。

5. 运行: `python dynamic/run_eval.py --agent-plugin <name> ...`
