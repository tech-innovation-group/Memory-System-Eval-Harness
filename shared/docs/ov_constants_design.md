# shared/ov_constants.py -- OpenViking 集成模块设计

## 目标

为 CLI agent 插件（kimi_code、hermes）提供 OpenViking (OV) 记忆系统集成所需的共享常量、钩子脚本内容和配置注入函数。插件在 `send_message()` 中调用本模块的 `write_*_ov_files()` 读取用户实际的 agent 配置文件，注入 OV 钩子，生成临时配置目录，通过环境变量指向该目录，使 agent 子进程加载带 OV 钩子的配置。

## 模块边界

本模块只负责 **配置文件生成**（读取用户配置 + 注入钩子），不负责：
- 启动或管理 OV 服务
- 调用 OV API（钩子脚本在 agent 子进程内运行，不经本模块）
- 记忆写入（由 harness 的 `inject_memories` 处理）

## 常量

| 常量 | 值 | 用途 |
|---|---|---|
| `OV_URL_DEFAULT` | `http://127.0.0.1:19080` | OV 服务默认地址 |
| `OV_ENV_URL` | `OPENVIKING_URL` | 传递给钩子脚本的环境变量名 |
| `OV_ENV_API_KEY` | `OPENVIKING_API_KEY` | OV 鉴权 key 环境变量名 |
| `OV_ENV_ACCOUNT` | `OPENVIKING_ACCOUNT` | OV 账户环境变量名 |
| `OV_ENV_USER` | `OPENVIKING_USER` | OV 用户环境变量名 |
| `KIMI_CODE_HOME_ENV` | `KIMI_CODE_HOME` | kimi-code 子进程环境变量名，指向 ov_home |
| `HERMES_HOME_ENV` | `HERMES_HOME` | hermes 子进程环境变量名，指向 ov_home |

## 钩子脚本

### 共享 recall 逻辑 (`_RECALL_CORE`)

Node.js ESM 模块，导出 `recallContext(userPrompt)` 异步函数：
- 输入：用户 prompt 字符串
- 查询 `POST {OV_URL}/api/v1/search/recall`，body: `{query, quotas:{events:6,entities:6,preferences:3,experiences:0}, max_chars:3000, min_score:0.35, render:true}`
- 成功且有 `result.rendered` 非空时，返回 `<openviking-context>\n{rendered}\n</openviking-context>`
- 任何错误、空结果、短 query（<3 字符）返回空字符串
- 8 秒超时

### Kimi Code 钩子 (`KIMI_HOOK_SCRIPT`)

- 事件：`UserPromptSubmit`
- stdin：`{prompt: [{type:"text", text:"..."}]}` JSON
- stdout：纯文本（`<openviking-context>` 块），kimi-code 追加到上下文

### Hermes 钩子 (`HERMES_HOOK_SCRIPT`)

- 事件：`pre_llm_call`
- stdin：`{extra: {user_message: "..."}}` JSON
- stdout：`{"context": "<openviking-context>..."}` JSON，hermes 追加到当前 turn 的 user message

## 配置注入策略

**不使用硬编码模板**。`write_*_ov_files()` 从调用方传入的 `config_home` 路径读取用户实际的 agent 配置文件，注入 OV 钩子后写到 `ov_home`。用户原始配置不被修改。

`config_home` 由插件通过 CLI 参数（`--kimi-config-home` / `--hermes-config-home`）接收，在 `setup()` 中存储，在 `_build_ov_env()` 中传递给 `write_*_ov_files()`。

### TOML 钩子注入 (`_strip_ov_hooks_from_toml`)

用于 kimi-code 的 `config.toml`：
- 逐行扫描 `[[hooks]]` 块（从 `[[hooks]]` 头到下一个 section 头或 EOF）
- 移除包含 `auto-recall.mjs` 的块（幂等性）
- 保留其他 `[[hooks]]` 块不变

### YAML 钩子注入（hermes）

用于 hermes 的 `config.yaml`：
- 使用 PyYAML `safe_load` 解析用户配置
- 修改 `hooks.pre_llm_call` 列表：移除已有 `auto-recall.mjs` 条目，追加 OV 钩子
- 设置 `hooks_auto_accept: true`
- 按 `mcp_tools` 开关添加/移除 `mcp_servers.openviking` 条目
- `yaml.dump` 写回（`sort_keys=False` 保留键序，注释不保留但 ov_home 副本为一次性使用）

## 文件生成函数

### `write_kimi_ov_files(ov_home, *, mcp_tools, ov_url, config_home) -> str`

1. 从 `config_home` 读取 `config.toml`
2. 用 `_strip_ov_hooks_from_toml` 移除已有 OV 钩子
3. 追加 `[[hooks]] event="UserPromptSubmit"` 块，指向 `ov_home/hooks/auto-recall.mjs`
4. 生成三个文件：
   - `{ov_home}/hooks/auto-recall.mjs` -- 钩子脚本
   - `{ov_home}/config.toml` -- 用户配置 + OV 钩子
   - `{ov_home}/mcp.json` -- MCP 配置（`mcp_tools=True` 时含 OV server，否则空 `mcpServers`）
5. 返回 `ov_home` 的绝对路径

找不到 `config.toml` 时抛出 `FileNotFoundError`。

### `write_hermes_ov_files(ov_home, *, mcp_tools, ov_url, config_home) -> str`

1. 从 `config_home` 读取 `config.yaml`
2. 用 PyYAML 解析，注入 `pre_llm_call` 钩子，设置 `hooks_auto_accept: true`
3. 按 `mcp_tools` 开关添加/移除 `mcp_servers.openviking`
4. 生成两个文件：
   - `{ov_home}/hooks/auto-recall.mjs` -- 钩子脚本
   - `{ov_home}/config.yaml` -- 用户配置 + OV 钩子（MCP 内联到 YAML）
5. 返回 `ov_home` 的绝对路径

找不到 `config.yaml` 时抛出 `FileNotFoundError`。

## 环境变量构建

### `build_ov_env(ov_url, ov_api_key, ov_account, ov_user) -> dict`

构建 `OPENVIKING_*` 环境变量字典。空值不包含在返回字典中。

## 行为约束

1. **幂等**：重复调用 `write_*_ov_files()` 先移除已有 OV 钩子再追加，不产生重复
2. **路径安全**：钩子路径用正斜杠（`str(path).replace("\\", "/")`），避免 Windows 反斜杠导致 TOML/YAML 解析问题
3. **路径无空格**：`ov_home` 路径不能包含空格。agent 的 hook command 解析（TOML `command = "node <path>/auto-recall.mjs"` / YAML `command` 字段）按空格分词，路径中的空格会导致 hook 静默不触发。`write_*_ov_files()` 检测到空格时抛出 `ValueError`
4. **MCP 开关即时生效**：每次 `send_message` 都重写配置文件，`mcp_tools` 参数变化立即反映
5. **不触碰用户配置**：所有文件写入 `ov_home` 指定的独立目录，通过 `KIMI_CODE_HOME` / `HERMES_HOME` 环境变量让 agent 子进程加载该目录
6. **不硬编码 agent 配置**：API key、模型名、provider 等从用户实际配置文件读取（路径由 CLI 参数 `--kimi-config-home` / `--hermes-config-home` 传入），不在代码中硬编码
7. **config_home 必传**：`write_*_ov_files()` 的 `config_home` 参数为必传项，插件在 `ov_home` 已设置但 `config_home` 为空时抛出 `ValueError`
8. **account/user 回退**：插件的 `_build_ov_env()` 在 `--ov-account` / `--ov-user` 未显式传入时，回退到 `memory_client.account` / `memory_client.user_id`，确保召回钩子查询的 OV 账户与记忆导入时动态 provision 的账户一致。回退在 `_build_ov_env()` 而非 `setup()` 执行，因为 harness 的 resume 机制可能在 setup 之后修改 `memory_client.account`

## 单元测试

测试文件：`tests/test_ov_constants.py`

| 测试类 | 覆盖内容 |
|---|---|
| `BuildOvEnvTests` | 全字段/空字段/部分空/全空 |
| `StripOvHooksFromTomlTests` | 移除 OV 钩子/保留非 OV 钩子/无钩子/多 OV 钩子/空字符串 |
| `WriteKimiOvFilesTests` | 生成 config.toml、保留用户配置、保留非 OV 钩子、hook 脚本、MCP on/off、路径正斜杠、幂等覆盖、幂等不重复钩子、配置不存在报错、路径含空格报错 |
| `WriteHermesOvFilesTests` | 生成 config.yaml、保留用户配置、hook 脚本、MCP on/off、路径正斜杠、幂等覆盖、幂等不重复钩子、配置不存在报错、hooks_auto_accept 设置、路径含空格报错 |
| `OVConstantsTests` | 常量值正确性 |
