# Web / Memory Plugin Refactor Design

日期：2026-06-17

## 目标

这次重构围绕三条主线：

1. 前端代码统一收敛到 `web` package。
2. 后端能力统一收敛到 `memory` package。
3. `web` 侧只通过标准 plugin 接口访问具体记忆引擎，不再直接依赖 `openviking` / `echomemory` 细节。

## 设计模式

### 1. Facade

新增 `memory/plugins/service.py` 作为 Facade：

- `available_backends()`
- `get_backend()`
- `backend_contract()`
- `list_imported_memories()`
- `import_integrity()`
- `session_browser()`
- `memory_timeline()`
- `read_memory_file()`
- `agent_context()`
- `agent_chat()`
- `archive_chat()`
- `build_locomo_import_task()`
- `build_locomo_qa_task()`
- `build_generic_qa_task()`

`server.py` 现在优先通过这层 Facade 调后端，而不是直接依赖具体 plugin 目录。

### 2. Strategy

`memory/plugins/openviking` 和 `memory/plugins/echomemory` 仍然保留为两套 Strategy 实现：

- OpenVikingPlugin
- EchoMemoryPlugin

二者共享同一套 plugin contract，但各自决定：

- 如何探活
- 如何浏览/校验记忆
- 如何构建 import / QA 任务
- 如何提供 agent context / chat / archive

### 3. Registry

`memory/plugins/registry.py` 继续负责注册后端实现，但注册表只做“发现后端”，不做 web 层协调逻辑。

### 4. Adapter（兼容层）

`memory/adapters` 仍然存在，但被明确降级为兼容层：

- 旧代码可继续走 `memory.adapters`
- 新代码应走 `memory.plugins.service`

## 当前包边界

### web package

新增 `web/package.py`：

- 统一管理 `web/static`
- 统一管理 legacy `static`
- 统一管理 `web/ui_contract.json`
- 统一暴露 public static files 规则

这样 server 不再四处硬编码 `web/static/...` 路径。

### memory package

`memory/plugins` 现在是正式后端扩展点：

- `memory/plugins/base.py`：基础类型
- `memory/plugins/contract.py`：标准 contract
- `memory/plugins/registry.py`：注册表
- `memory/plugins/service.py`：Facade
- `memory/plugins/openviking/*`：OpenViking 实现
- `memory/plugins/echomemory/*`：EchoMemory 实现

## 这次已经完成的代码落地

### A. web 包边界

已新增：

- `web/package.py`

已更新：

- `web/__init__.py`
- `server.py` 的 UI contract / static root 解析改为委托给 `web` package

### B. plugin facade

已新增：

- `memory/plugins/service.py`

已更新：

- `memory/plugins/__init__.py`
- `memory/adapters/registry.py`
- `memory/adapters/doctor.py`
- `memory/report_export.py`

### C. server -> plugin 接口迁移

`server.py` 中以下链路已切到 `plugin_service`：

- backend probe
- discover openviking ports
- workspace runtime config helpers
- imported memories / import integrity / session browser / memory timeline / memory file
- locomo import / locomo qa / generic qa task build
- agent chat / agent context / agent archive

这意味着 web 层的主要 backend 入口已经不再直接绑死到具体实现目录。

### D. `web/api` 与 `memory/services` 抽离

已新增：

- `web/api/__init__.py`
- `web/api/memory_backend.py`
- `web/api/agent_backend.py`
- `web/api/tasks.py`
- `memory/services/__init__.py`
- `memory/services/task_factory.py`
- `memory/services/runtime_status.py`

当前分工：

- `web/api/memory_backend.py`
  - 承接 memory backend 相关 GET 路由
  - 包括 probe / discover / imported / integrity / sessions / timeline / file
- `web/api/agent_backend.py`
  - 承接 agent context / chat / archive 相关 POST 路由
  - web 侧只做请求分发，不再直接握具体 backend 分支
- `web/api/tasks.py`
  - 承接 task create / validate / stop / stop-all 相关 POST 路由
  - `server.py` 不再内联这些 task API 分支
- `memory/services/task_factory.py`
  - 承接 task kind 到具体命令构建的分发
  - `server.py` 只负责提供上下文依赖并委托调用
- `memory/services/runtime_status.py`
  - 承接 OpenViking / EchoMemory 运行态探测
  - 包括 OpenViking 服务 probe、EchoMemory SDK 根目录发现、版本与 token readiness 判断

这一步的意义不是“把所有逻辑搬完”，而是先把两类最稳定的边界抽出来：

1. HTTP 的 memory backend 查询入口归 `web`。
2. Agent 工作台路由归 `web`。
3. task / agent / memory backend 路由逐步归 `web/api`。
4. 后端任务命令装配与运行态探测归 `memory`。

这样 `server.py` 开始退化为 composition layer，而不是继续同时扮演：

- 路由表
- 后端门面
- 任务工厂
- 具体实现协调器

## 当前仍然存在的技术债

### 1. `server.py` 仍然过大

目前 `server.py` 依旧承担了太多责任：

- HTTP handler
- task orchestration
- account/config normalization
- benchmark-specific routing
- plugin coordination

下一阶段应继续拆出：

- `web/api/*.py`
- `web/routes/*.py`
- `memory/services/*.py`

### 2. adapter 命名仍有残留

变量名和部分 API 里仍有 `adapter` 术语。语义上已经切到了 plugin facade，但命名还没有完全收口。

### 3. POST 路由与剩余 orchestration 仍在 server

虽然 `build_single_command()` 已经委托给 `memory/services/task_factory.py`，但 `server.py` 里仍然保留了大量 POST handler 和运行态编排。后续应继续抽成：

- `web/api/tasks.py`
- `web/api/accounts.py`
- `memory/services/runtime_status.py`
- `memory/services/task_orchestrator.py`

## 推荐的下一步目录形态

```text
web/
  __init__.py
  package.py
  api/
    __init__.py
    memory.py
    tasks.py
    accounts.py
  static/
    index.html
    app.js
    styles.css

memory/
  __init__.py
  plugins/
    __init__.py
    base.py
    contract.py
    registry.py
    service.py
    openviking/
      plugin.py
      runtime.py
      tasks.py
      inspector.py
      agent.py
    echomemory/
      plugin.py
      tasks.py
      inspector.py
      agent.py
  services/
    task_factory.py
    runtime_status.py
    account_state.py
```

## 重构原则

1. web 不直接 import 具体后端实现目录。
2. 新增后端时，只需要：
   - 新建 `memory/plugins/<engine>/`
   - 在 `registry.py` 注册
   - 满足 `contract.py` 定义
3. 兼容层只保留在 `memory/adapters`，避免继续扩散。
4. 大文件拆分时优先抽“稳定边界”，不要先按功能点随意切碎。

## 本次结论

仓库其实已经有了 plugin-first 的雏形，这次不是从零设计，而是把这个方向正式扶正：

- `web` 开始拥有自己的 package manifest
- `memory.plugins` 开始拥有自己的 Facade
- `server.py` 开始从“直接操作具体实现”退回到“通过 plugin 接口装配”

这还不是最终形态，但已经把后续继续拆分的主承重墙立起来了。
