# echomem_mcp Agent Plugin

## 设计意图

测试 agent 通过 EchoMem MCP 协议检索记忆的能力。EchoMem QA 检索不再使用 HTTP search API：平台侧初始检索固定通过 MCP `memory_query` 完成；开启工具调用时，LLM 也通过 OpenAI function-calling 调用 MCP 工具（`memory_query`、`read`、`list`、`glob`），模拟真实 agent 与记忆系统的交互。

## 工作原理

1. 每个问题创建一个 MCP 会话（`initialize` -> 获取 `Mcp-Session-Id`）
2. LLM 被赋予 4 个 MCP 工具作为 function-calling 定义
3. 工具调用循环：
   - LLM 生成 -> 若有 `tool_calls`，逐个通过 `McpClient.call_tool` 转发到 MCP 服务器
   - 将工具结果追加到对话
   - 继续循环，直到 LLM 不再调用工具（给出最终答案）或达到最大迭代数
4. 达到最大迭代时，强制 LLM 不再调用工具，直接回答

## 工具调用控制

`send_message` 通过两个 CLI 参数控制模型侧工具调用行为：

- `--tool-calling` / `--no-tool-calling`：是否开启模型工具调用循环。开启时解析 LLM 返回的工具并执行，把结果返回给 LLM 不断迭代；关闭时只做单次 LLM 调用。
- `--search-in-tools` / `--no-search-in-tools`：是否将 `memory_query` 包含在模型可调用工具定义中。

无论是否开启模型工具调用，平台侧初始检索都固定通过 MCP `memory_query` 完成，不再提供 `--manual-search` / `--mcp-initial-search`，也不会调用 EchoMem HTTP search API。

## 记忆注入

`send_message` 通过 `self.memory_client`（`EchoMemClient`）注入记忆；QA 检索通过 `McpClient.call_tool("memory_query", ...)` 访问 EchoMem MCP。记忆客户端定义在 `backends/echomem/client.py`，由 `setup()` 创建。auth key 回退到 `echomem_auth_key`。

## 前置条件

- EchoMem MCP 服务必须运行（workspace config `mcp.enabled=true`，默认端口 8001）
- 若 memory 插件是 echomemory，auth key 自动复用

## 文件结构

| 文件 | 职责 |
|---|---|
| `plugin.py` | 插件入口：`add_arguments`、`setup`、`send_message` |
| `mcp_client.py` | 最小 MCP 客户端：JSON-RPC over HTTP + SSE 解析 + 会话管理 |
| `runtime.py` | 工具调用循环 + 并发执行 |
| `docs/design.md` | 本文件 |

## CLI 参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--mcp-url` | `http://127.0.0.1:8001` | MCP 服务器地址 |
| `--mcp-auth-key` | `""` | X-Auth-Key，空则回退到 `--echomem-auth-key` |
| `--mcp-max-iterations` | `50` | 每个问题的最大工具调用迭代数 |
