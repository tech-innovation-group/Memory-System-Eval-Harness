# EchoMemory v0.0.5 对接当前自定义 Agent 方案

更新时间：2026-06-08

## 目标

在不影响现有 OpenViking 自定义 Agent 链路的前提下，把 `version_0.0.5` EchoMemory 接到当前评测平台的自定义 Agent 工作台中，并尽量对齐 OpenViking / MemoryBench Agent 的使用方式、上下文结构和可观测性。

## 一、当前平台的统一接入边界

平台已经有统一的 Agent API 边界，前端不应该直接依赖某个具体记忆后端。

统一入口：

- `POST /api/agent/context`
- `POST /api/agent/chat`
- `POST /api/agent/archive`

服务端分发逻辑：

- [server.py](../server.py)
- 通过 `memory_backend` 选择插件
- 插件暴露统一能力：
  - `agent_context(payload, defaults)`
  - `agent_chat(payload, defaults, config_path)`
  - `archive_chat(payload, defaults, output_dir)`

插件实现：

- OpenViking:
  - [memory/plugins/openviking/plugin.py](../memory/plugins/openviking/plugin.py)
  - [memory/plugins/openviking/agent.py](../memory/plugins/openviking/agent.py)
- EchoMemory:
  - [memory/plugins/echomemory/plugin.py](../memory/plugins/echomemory/plugin.py)
  - [memory/plugins/echomemory/agent.py](../memory/plugins/echomemory/agent.py)

结论：架构层面已经支持 EchoMemory 作为“当前自定义 Agent”的后端，不需要另起一套前端入口。

## 二、当前 OpenViking 对接方式

OpenViking 当前是“插件 + 统一 Agent API”的标准实现：

1. `agent_context`
   - 读取当前账户 / workspace
   - 检索 relevant memory
   - 组装系统人设、记忆块、最近对话、当前请求
   - 返回右侧上下文预览

2. `agent_chat`
   - 用上一步组装出的 messages
   - 调当前 Agent 模型
   - 返回回答、相关记忆、context trace

3. `archive_chat`
   - 手动 commit
   - 将对话写入后端并触发长期记忆归档
   - 返回 session 路径、memory 路径、归档状态

这条链路的关键不是“OpenViking 特有接口”，而是：

- 统一的 account/workspace 隔离
- 统一的上下文预览结构
- 统一的回答接口
- 统一的手动归档接口

## 三、当前 EchoMemory 已经具备的部分

EchoMemory 当前已经接上了同样三类接口：

1. `agent_context`
   - 文件：[memory/plugins/echomemory/agent.py](../memory/plugins/echomemory/agent.py)
   - 当前通过 EchoMemory local SDK 执行 `find/search`
   - 支持拆分：
     - `user_memory`
     - `agent_memory`
   - 支持返回：
     - `retrieval`
     - `context_trace`
     - `isolation`

2. `agent_chat`
   - 当前实现是：
     - 先 `build_context_preview`
     - 再一次性调用模型生成回答
   - 也就是：
     - `single-shot RAG`
     - 不是完整多轮工具调用 agent

3. `archive_chat`
   - 当前已支持：
     - `create_session`
     - `add_message`
     - `commit_session`
   - 会返回 EchoMemory workspace/account/session 路径

结论：EchoMemory 已经能作为工作台后端使用，但“Agent 行为形态”还没有完全对齐 OpenViking 的 MemoryBench Agent。

## 四、和 OpenViking 对齐时，真正要补的不是接口，而是 Agent 行为

### 当前差异

OpenViking 自定义 Agent 更接近：

- 先构建 prompt / context
- 再按需要做工具检索
- 输出 evidence / trace

EchoMemory 当前工作台更接近：

- 先检索一次
- 把检索结果拼进 prompt
- 一次性回答

### 这会带来的影响

1. 召回失败时，不能二次搜索
2. 复杂问题不能先 search 再 read 再 refine
3. evidence 虽然能展示，但不是“真实多轮工具轨迹”
4. 和 VikingBoat / OpenViking 的可比性不够强

## 五、推荐的对接方案

### 方案原则

前端不新增新入口，继续复用当前自定义 Agent 工作台。

只做两层统一：

1. 统一插件接口
2. 统一 Agent tool-loop 行为

### 方案 A：最小改造，最快落地

保留现有工作台 API：

- `/api/agent/context`
- `/api/agent/chat`
- `/api/agent/archive`

仅把 EchoMemory 的 `agent_chat` 从“单次 RAG”升级为“受控工具循环”：

可直接复用的现有能力：

- [scripts/echomemory_memory_qa.py](../scripts/echomemory_memory_qa.py)
  - `--prompt-mode vikingboat_compat`
  - `--vikingboat-tool-loop`
  - `--tool-set vikingboat_default`
  - `--top-k`
  - `--retrieval-mode both`
  - `call_echomemory_vikingboat_lite_loop(...)`

建议做法：

1. 从 `memory/plugins/echomemory/agent.py` 中保留：
   - `resolve_settings`
   - `retrieve`
   - `build_context_preview_async`

2. 把 `chat(...)` 的一次性 `llm.openai_chat(...)`
   替换为：
   - 与 `echomemory_memory_qa.py` 同构的 tool-loop
   - 至少支持：
     - search
     - read
     - multi-read

3. 让工作台和 QA 使用同一组核心参数：
   - `top_k`
   - `tool_search_limit`
   - `tool_min_score`
   - `max_iterations`
   - `prompt_mode`
   - `vikingboat_tool_loop`

这样做的好处：

- 改动小
- 复用已有 QA 逻辑
- 和 OpenViking / VikingBoat 更可比

### 方案 B：完全统一成 “MemoryBench Agent”

目标：

- OpenViking 和 EchoMemory 都不再各自维护一套 `agent.py`
- 改为一个统一的 `MemoryBench Agent Core`
- 后端差异只保留在 memory tool adapter

拆分方式：

1. `memory/agents/memorybench_core.py`
   - prompt 组装
   - tool loop
   - trace 输出
   - evidence schema

2. `memory/tools/openviking_tools.py`
   - search/read 具体实现

3. `memory/tools/echomemory_tools.py`
   - search/read 具体实现

4. 插件层只负责：
   - 配置
   - account/workspace 隔离
   - archive / import / QA task build

这个方案更干净，但改动更大，建议放到下一轮。

## 六、建议当前就执行的最小改动

如果目标是“尽快把 EchoMemory v0.0.5 接成当前自定义 Agent，且参考 OpenViking”，建议按下面顺序做：

1. 保留现在的统一 API
2. 保留当前 EchoMemory 插件结构
3. 把 EchoMemory `agent_chat()` 升级为 tool-loop
4. 让工作台参数与 QA 参数共用一套默认值
5. 让右侧 `context_trace` 继续显示：
   - system prompt
   - user memory
   - agent memory
   - tool 调用轨迹
   - 最终 evidence
6. `archive_chat()` 继续沿用现在的 EchoMemory commit_session

## 七、实现位置建议

### 必改文件

- [memory/plugins/echomemory/agent.py](../memory/plugins/echomemory/agent.py)
  - 把 `chat()` 从 single-shot 改为 tool-loop

- [web/static/app.js](../web/static/app.js)
  - 工作台配置面板里显示 EchoMemory 与 OpenViking 共用的 Agent 参数

### 可复用文件

- [scripts/echomemory_memory_qa.py](../scripts/echomemory_memory_qa.py)
  - 可直接复用其 tool-loop 和 vikingboat compat prompt 逻辑

- [memory/plugins/echomemory/tasks.py](../memory/plugins/echomemory/tasks.py)
  - 已经把 EchoMemory QA 对齐到了 VikingBoat 兼容参数

## 八、最终落地后的目标状态

理想状态下，当前自定义 Agent 工作台对两种后端表现一致：

1. 选择 `memory_backend=openviking`
   - 走 OpenViking memory tools
   - 走 OpenViking archive

2. 选择 `memory_backend=echomemory`
   - 走 EchoMemory local SDK tools
   - 走 EchoMemory archive

3. 两者前端完全共用：
   - 上下文预览
   - 人设展示
   - 相关记忆展示
   - 手动 commit
   - trace / evidence 展示

4. 两者 QA 尽量共用：
   - prompt mode
   - top-k
   - tool-loop
   - 报告字段

## 九、一句话结论

参考 OpenViking 的方式时，EchoMemory 不需要再单独造一个“新 Agent 页面”；正确路线是继续走当前统一插件接口，把 EchoMemory 的 `agent_chat` 从 `single-shot RAG` 升级成和 QA 同构的 `MemoryBench Agent tool-loop`，这样才能真正和 OpenViking / VikingBoat 保持可比。
