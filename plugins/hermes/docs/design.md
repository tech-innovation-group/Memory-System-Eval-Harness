# Hermes 插件设计意图

## 目标

通过 subprocess 调用 `hermes` CLI（非交互模式），将用户查询注入 Hermes agent 并获取回复与日志。

## CLI 调用协议

```
hermes agent --message "<msg>" --json [--session-id <id>]
```

- `agent`：启动 agent 交互的子命令。
- `--message "<msg>"`：用户查询。
- `--json`：stdout 输出 JSON。
- `--session-id <id>`：会话 ID（如果 CLI 支持）。

> Hermes CLI 的确切二进制名称和 flag 需通过 `hermes --help` 验证。
> 当前按通用 CLI agent 模式设计。

## Session 管理

- `create_session()` 生成 `eval_hermes_<uuid>` 格式的 session ID。
- **非空 session_id**：传 `--session-id <id>`，并使用 per-session 锁串行化 `send_message` 调用，防止并发 worker 同时访问同一 hermes 会话导致回复串台。
- **空 session_id**：不传 `--session-id`，每次调用独立，不加锁。

## 输出解析

尝试解析 stdout 为 JSON，按优先级提取 `result` -> `output` -> `response` -> `content`。
非 JSON 输出时回退为纯文本。

## 日志收集

- 每次 `subprocess.run` 的 stderr 截断到 2000 字符后累积。
- `getlog()` 返回所有调用的 JSON 日志。

## 记忆注入

- 默认不注入（`NullMemoryClient`）。
- 配置 `--memory-backend` 后通过共享 helper 注入。

## 线程安全

- 空 session_id：并发安全，每次调用独立。
- 非空 session_id：per-session 锁串行化同一 session_id 的并发调用，防止 hermes 后端混淆回复归属。

## 已知限制

- 依赖本地安装 `hermes` CLI 且在 PATH 中。
- CLI flag 需验证后调整。
- 无法获取 TTFT（subprocess 一次性返回全部输出）。

## OpenViking 记忆集成

### 概述

插件支持将 OpenViking (OV) 记忆系统接入 Hermes 评测流程。集成包含两部分：

1. **自动召回钩子**：通过 Hermes 的 `pre_llm_call` hook，在每次 LLM 调用前自动查询 OV recall API，将召回的记忆上下文注入当前 turn。
2. **MCP 工具**：可选地将 OV 的 MCP server 内联到 hermes config，让 agent 主动调用记忆工具。

### 参数

| 参数 | 说明 |
|---|---|
| `--hermes-ov-home <dir>` | OV 配置目录路径。设置后每次 `send_message` 重新生成钩子脚本和配置文件到此目录，并将 `HERMES_HOME` 指向它 |
| `--hermes-config-home <dir>` | 用户 hermes 配置目录路径（如 `~/.hermes`）。`--hermes-ov-home` 设置时必传 |
| `--hermes-mcp-tools` | 开关，是否在配置中启用 OV MCP server。默认关闭 |
| `--ov-url <url>` | OV 服务地址，默认 `http://127.0.0.1:19080` |
| `--ov-api-key <key>` | OV 鉴权 key，以 `OPENVIKING_API_KEY` 环境变量传递 |
| `--ov-account <acct>` | OV 账户标识，以 `OPENVIKING_ACCOUNT` 传递 |
| `--ov-user <user>` | OV 用户标识，以 `OPENVIKING_USER` 传递 |

### 钩子机制

`shared/ov_constants.py` 中的 `write_hermes_ov_files()` 在 `ov_home` 目录下生成：

- `hooks/auto-recall.mjs` -- Node.js ESM 脚本，从 stdin 读取 hermes hook 的 JSON payload，提取 `extra.user_message` 字段，查询 `POST {OV_URL}/api/v1/search/recall`，成功时输出 `{"context":"<openviking-context>...</openviking-context>"}` JSON 到 stdout
- `config.yaml` -- 从 `--hermes-config-home` 指定的目录读取用户实际的 hermes 配置，用 PyYAML 解析后注入 `hooks.pre_llm_call` 钩子（移除已有 OV 钩子再追加，幂等），设置 `hooks_auto_accept: true`。用户原有的 model、provider、toolsets 等全部保留。路径用正斜杠避免 Windows 反斜杠问题

### MCP 开关

`--hermes-mcp-tools` 控制 `config.yaml` 中的 `mcp_servers.openviking` 条目：

- **开启**：在 `mcp_servers` 下添加 `openviking` server（`url: {ov_url}/mcp`, `enabled: true`, `timeout: 120`）
- **关闭**：移除 `mcp_servers` 中的 `openviking` 条目（保留其他 MCP server 不变）

MCP 配置内联到 `config.yaml`（hermes 不像 kimi-code 有独立 mcp.json）。每次 `send_message` 都重写配置（幂等），开关变化即时生效。

### 环境变量

`send_message` 时，若 `ov_home` 已配置，子进程环境变量包含：

- `HERMES_HOME` = ov_home 绝对路径
- `OPENVIKING_URL` / `OPENVIKING_API_KEY` / `OPENVIKING_ACCOUNT` / `OPENVIKING_USER`（仅非空时）

**account/user 回退**：当 `--ov-account` / `--ov-user` 未显式传入时，自动回退到 `memory_client.account` / `memory_client.user_id`。这确保召回钩子查询的 OV 账户与记忆导入时 harness 动态 provision 的账户一致。显式参数优先级最高。回退在 `_build_ov_env()` 中执行（而非 `setup()`），因为 harness 的 resume 机制可能在 setup 之后修改 `memory_client.account`。

### 不做的事

- 不做 Stop hook（auto-capture）。评测时 harness 自己通过 `inject_memories` 处理记忆写入。
- 不覆盖用户当前的 hermes 配置。所有 OV 配置写入独立的 `ov_home` 目录。

### 路径限制

`ov_home` 路径不能包含空格。hermes 的 hook command 格式为 `node <path>/auto-recall.mjs`，按空格分词后路径中的空格会导致 node 收到截断的路径参数，hook 静默不触发。`write_hermes_ov_files()` 检测到空格时抛出 `ValueError`，提示使用无空格路径（如 `D:/ov_eval/hermes`）。
