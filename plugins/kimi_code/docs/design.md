# Kimi Code CLI Plugin -- Design

## 目标

通过 subprocess 调用本地 `kimi` CLI（Kimi Code），在 prompt 模式下注入用户查询并获取回复，用于记忆系统评测。

## CLI 接口

```
kimi -p "<message>" --output-format stream-json [-m <model>] [-S <session_id>]
```

- `-p, --prompt`：非交互模式，执行单条 prompt 后退出
- `--output-format stream-json`：输出 JSONL，每行一个 JSON 对象
- `-m, --model`：可选，指定模型别名
- `-S, --session <id>`：恢复已有会话

**限制**：`--yolo` 和 `--auto` 不能与 `--prompt` 组合使用。prompt 模式下 agent 使用默认权限运行。对记忆评测足够（agent 只需回答问题）。

## 输出解析

`stream-json` 输出为 JSONL，每行一个 JSON 对象：

```jsonl
{"role":"assistant","content":"Hello"}
{"role":"meta","type":"session.resume_hint","session_id":"session_abc123","command":"kimi -r session_abc123","content":"To resume this session: kimi -r session_abc123"}
```

- `role=assistant` 的行：`content` 字段是 agent 回复
- `role=meta` 且 `type=session.resume_hint` 的行：`session_id` 字段是 kimi 生成的会话 ID

非 JSON 行跳过（hook 输出/通知，非 LLM 回复）。stream-json 模式下非 JSON 行来自 hook stdout（如 `UserPromptSubmit hook` 字样）或 `<openviking-context>` 块，不属于 agent 回复，直接忽略。

此外，kimi-code 会将 UserPromptSubmit hook 的 stdout 包装在 `role=assistant` 的 JSON 行中输出。解析器额外过滤 `content` 包含 `<openviking-context>` 标记的 assistant 行，该标记是 OV 召回钩子的输出标志，不会出现在正常 LLM 回复中。

## 会话管理

kimi-code 自己生成 session ID，不支持指定 ID。插件维护一个映射：

```
harness_session_id -> kimi_session_id
```

- **非空 harness session_id**：首次调用不带 `-S`，从输出中解析 kimi session_id 并存储；后续调用使用 `-S <kimi_session_id>` 恢复会话。为防止并发 worker 竞态（首批同时创建多个 kimi 会话、后续并发恢复同一会话导致回复串台），非空 session_id 使用 per-session 锁串行化整个 `send_message` 调用。
- **空 harness session_id**：每次调用都是独立的，不带 `-S`，不存储映射。这是 LoCoMo 等 benchmark 的正确行为——每道题独立作答，不依赖前一题的上下文。

`create_session()` 返回 `eval_kimi_<uuid12>` 格式的占位 ID，不实际创建 kimi 会话。

## 行为约束

1. 非零退出码：返回 `AgentResponse(error=stderr)`，不抛异常
2. 超时：返回 `AgentResponse(error="kimi timed out after Ns", extra={"timed_out": True})`
3. `extra=None`：容错为 `{}`
4. `system_prompt_append`：从 extra 取出，拼到消息前面
5. `question_timeout_s`：从 extra 取，传给 runner
6. 默认无 memory backend -> `NullMemoryClient`，`inject_memories` 自动 no-op

## 边界条件

- kimi session_id 未解析到（如输出格式变化）：首次调用不传 `-S`，后续也不传，每次都是独立会话
- 多行 assistant 输出：按换行拼接
- 空输出：返回空 text，不报错
- 空 harness session_id：不传 `-S`，不存储映射，不创建 per-session 锁--每次调用完全独立
- 并发访问同一非空 session_id：per-session 锁串行化，防止竞态

## OpenViking 记忆集成

### 概述

插件支持将 OpenViking (OV) 记忆系统接入 Kimi Code 评测流程。集成包含两部分：

1. **自动召回钩子**：通过 Kimi Code 的 `UserPromptSubmit` hook，在每次用户提交 prompt 时自动查询 OV recall API，将召回的记忆上下文注入 agent。
2. **MCP 工具**：可选地将 OV 的 MCP server 配置给 agent，让 agent 在推理过程中主动调用记忆工具。

### 参数

| 参数 | 说明 |
|---|---|
| `--kimi-ov-home <dir>` | OV 配置目录路径。设置后每次 `send_message` 重新生成钩子脚本和配置文件到此目录，并将 `KIMI_CODE_HOME` 指向它 |
| `--kimi-config-home <dir>` | 用户 kimi-code 配置目录路径（如 `~/.kimi-code`）。`--kimi-ov-home` 设置时必传 |
| `--kimi-mcp-tools` | 开关，是否在配置中启用 OV MCP server。默认关闭 |
| `--ov-url <url>` | OV 服务地址，默认 `http://127.0.0.1:19080` |
| `--ov-api-key <key>` | OV 鉴权 key，传入后以 `OPENVIKING_API_KEY` 环境变量传递 |
| `--ov-account <acct>` | OV 账户标识，传入后以 `OPENVIKING_ACCOUNT` 传递 |
| `--ov-user <user>` | OV 用户标识，传入后以 `OPENVIKING_USER` 传递 |

### 钩子机制

`shared/ov_constants.py` 中的 `write_kimi_ov_files()` 在 `ov_home` 目录下生成：

- `hooks/auto-recall.mjs` -- Node.js ESM 脚本，从 stdin 读取 kimi hook 的 JSON payload，提取 `prompt` 数组中的用户文本，查询 `POST {OV_URL}/api/v1/search/recall`（body 含 query/quotas/max_chars/min_score/render），成功时输出 `<openviking-context>` 块到 stdout
- `config.toml` -- 从 `--kimi-config-home` 指定的目录读取用户实际的 kimi-code 配置，移除已有 OV 钩子（幂等），追加 `[[hooks]] event="UserPromptSubmit"` 指向钩子脚本。用户原有的 API key、模型、provider、其他 hooks 等全部保留。路径用正斜杠避免 Windows 反斜杠问题
- `mcp.json` -- `mcp_tools=true` 时写入 OV MCP server（`{url}/mcp`），否则写入空 `mcpServers`

### MCP 开关

`--kimi-mcp-tools` 控制 `mcp.json` 内容：

- **开启**：`{"mcpServers":{"openviking":{"type":"http","url":"{ov_url}/mcp"}}}`
- **关闭**：`{"mcpServers":{}}`

每次 `send_message` 都重写配置文件（幂等），开关变化即时生效。

### 环境变量

`send_message` 时，若 `ov_home` 已配置，子进程环境变量包含：

- `KIMI_CODE_HOME` = ov_home 绝对路径
- `OPENVIKING_URL` / `OPENVIKING_API_KEY` / `OPENVIKING_ACCOUNT` / `OPENVIKING_USER`（仅非空时）

**account/user 回退**：当 `--ov-account` / `--ov-user` 未显式传入时，自动回退到 `memory_client.account` / `memory_client.user_id`。这确保召回钩子查询的 OV 账户与记忆导入时 harness 动态 provision 的账户一致。显式参数优先级最高。回退在 `_build_ov_env()` 中执行（而非 `setup()`），因为 harness 的 resume 机制可能在 setup 之后修改 `memory_client.account`。

### 不做的事

- 不做 Stop hook（auto-capture）。评测时 harness 自己通过 `inject_memories` 处理记忆写入。
- 不覆盖用户当前的 kimi-code 配置。所有 OV 配置写入独立的 `ov_home` 目录。

### 路径限制

`ov_home` 路径不能包含空格。kimi-code 的 hook command 格式为 `command = "node <path>/auto-recall.mjs"`，按空格分词后路径中的空格会导致 node 收到截断的路径参数，hook 静默不触发。`write_kimi_ov_files()` 检测到空格时抛出 `ValueError`，提示使用无空格路径（如 `D:/ov_eval/kimi`）。
