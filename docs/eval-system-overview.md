# 评测系统总览：大模型模拟用户评测 EchoAgent / EchoMem

本文档详尽总结 Memory-System-Eval-Harness 如何通过网页前端驱动大模型模拟真实用户，对 EchoAgent（Agent 前后端）和 EchoMem（记忆系统）进行端到端评测，覆盖评测功能点、系统输入输出及评测平台自身能力。

---

## 目录

1. [系统总览](#1-系统总览)
2. [网页前端如何让大模型模拟用户](#2-网页前端如何让大模型模拟用户)
3. [评测功能点](#3-评测功能点)
4. [评测系统需要的输入](#4-评测系统需要的输入)
5. [评测系统能输出什么](#5-评测系统能输出什么)
6. [评测系统本身的功能点](#6-评测系统本身的功能点)

---

## 1. 系统总览

### 1.1 项目定位

Memory-System-Eval-Harness 是一个**记忆系统评测工作台**（Memory Evaluation Workbench），用于对记忆增强型 AI 系统（EchoMemory 后端 + EchoAgent 前后端）进行标准化评测和实时交互测试。

### 1.2 三种评测工作流

| 工作流 | 脚本入口 | 测试对象 | 核心特点 |
|--------|---------|---------|---------|
| **(A) HTTP 黑盒 QA** | `scripts/echomemory_memory_qa.py` | EchoMem 检索准确性 | 使用标准数据集（LoCoMo/HotpotQA/LongMemEval），严格黑盒，仅通过 HTTP API 调用 |
| **(B) EchoAgent 交互式实时测试** | `scripts/echoagent_live_test.py` | EchoAgent + EchoMem 端到端 | LLM 生成场景和用户查询，模拟打字触发 prefill 管线，测量 TTFT/cached_tokens/召回质量 |
| **(C) 全流程评估管道** | `scripts/run_full_evaluation.py` | EchoAgent + EchoMem 完整链路 | 动态生成记忆 -> 注入 EchoMem -> 经 EchoAgent 发查询 -> 收集运行时指标 |

工作流 (B) 是**网页前端触发的核心入口**，也是本文的重点。

### 1.3 服务拓扑

```
浏览器 (localhost:4173)
  │
  ├─ 静态前端 (index.html + app.js + src/)
  │
  └─ /api/* 反向代理
       │
       ▼
  评测后端 server.py (localhost:19181)
    │
    ├── POST /api/tasks (kind=echoagent_live)
    │     └─ 启动 scripts/echoagent_live_test.py 子进程
    │
    ├── POST /api/dynamic/* (动态评测 API)
    │     └─ 调用 memory/dynamic_evaluator.py
    │
    └── GET /api/tasks/:id/log (轮询任务进度)
         │
         ▼
  被测服务:
    ├── EchoAgent 后端 (localhost:31020)  ← 模拟用户交互目标
    ├── echoagent 插件  (localhost:31030)  ← 记忆引擎适配层
    └── EchoMem         (localhost:8010)   ← 记忆算法核心
```

### 1.4 两套前端

| 前端 | 位置 | 技术栈 | 说明 |
|------|------|--------|------|
| **V2 模块化工作台** | `index.html` + `app.js` + `src/` | 原生 ES Module，无构建步骤 | 当前主力 UI，4 个 benchmark + 4 阶段流水线 |
| **Legacy 单体应用** | `web/static/index.html` + `app.js` | 全局脚本，单文件 26000+ 行 | 9 个视图，含手动聊天调试面板 |

两套前端共用同一个 Python 后端 (`server.py`)。

### 1.5 四阶段流水线

所有 benchmark 共用统一的 4 阶段评测流程：

```
Import（导入记忆） → QA（问答测试） → Judge（LLM 判分） → Report（导出报告）
```

EchoAgent 交互评测（`echoagent_live` benchmark）将 Import 阶段重新定义为「实时测试配置」，QA/Judge/Report 阶段由测试脚本内部自动完成。

---

## 2. 网页前端如何让大模型模拟用户

### 2.1 前端入口

在 V2 工作台中，用户通过以下路径触达交互评测：

1. **侧边栏选择**：点击「EchoAgent 交互」benchmark（`src/config.js` 中 `echoagent_live`，`benchmarkFamily: "live-interaction"`）
2. **Import 面板配置**：表单由 `src/render/import-echoagent.js` 渲染
3. **顶部选择器**：选择用户模拟器配置（`wbUserSimSelect`）和评估器配置（`wbEvalConfigSelect`）

### 2.2 前端表单字段

`src/render/import-echoagent.js` 渲染的配置表单包含三组字段：

| 分组 | 字段 | DOM ID | 默认值 | 说明 |
|------|------|--------|--------|------|
| **EchoAgent 连接** | EchoAgent URL | `wbEchoAgentUrl` | `http://127.0.0.1:31020` | 被测 EchoAgent 后端地址 |
| | EchoMem URL | `wbEchoMemUrl` | `http://127.0.0.1:8010` | EchoMem 记忆服务地址 |
| | 用户名 | `wbEchoAgentUsername` | `test_user` | EchoAgent JWT 登录用户名 |
| | 密码 | `wbEchoAgentPassword` | `test_password` | EchoAgent JWT 登录密码 |
| **测试参数** | 测试批次 | `wbNumBatches` | 3 (1-20) | 重复测试的批次数 |
| | 每批查询数 | `wbQueriesPerBatch` | 5 (1-20) | 每批中 LLM 生成的用户查询数 |
| **场景设置** | 自定义场景 | `wbCustomScenario` | (空) | 自定义场景描述文本，留空则 LLM 自动生成 |
| | 场景生成模型 | `wbScenarioModel` | `deepseek-v4-flash` | 用于生成记忆和查询的 LLM |
| | API Base URL | `wbScenarioBaseUrl` | (空) | LLM API 地址，留空用环境变量 |
| | API Key | `wbScenarioApiKey` | (空) | LLM API Key，留空用环境变量 |

### 2.3 启动流程

```
用户填写表单 → 点击「开始测试」
  │
  ├─ src/form-readers.js: readEchoAgentLiveImportForm()
  │    读取表单字段为 { echoagent_url, echomem_url, username, password,
  │                     num_batches, queries_per_batch, custom_scenario,
  │                     scenario_model, scenario_base_url, scenario_api_key }
  │
  ├─ src/action/echoagent.js: startEchoAgentLive()
  │    POST /api/tasks { kind: "echoagent_live", ...payload }
  │
  ├─ server.py 接收请求
  │    └─ memory/services/task_orchestrator.py: create_task()
  │         └─ memory/task_specs.py: build_echoagent_live_task()
  │              构建命令: python scripts/echoagent_live_test.py --echoagent-url ... --out-dir ...
  │              可选 --user-simulator-config <name|path> --evaluator-config <name|path>
  │
  └─ 前端轮询 GET /api/tasks/:id/log 获取实时进度
```

### 2.4 LLM 模拟用户的核心机制

测试脚本 `scripts/echoagent_live_test.py` 的 `run_test()` 函数（`:790`）按批次循环执行以下流程：

#### 步骤 1：LLM 生成背景记忆

```
MemoryDynamicEvaluator.generate_background_memories()
  ├─ 调用 memory/dynamic_evaluator.py:531 _generate_dynamic_memories()
  │    使用 user_simulator_config 中的 background_memories_prompt
  │    prompt 含 {num_memories} 占位符 → LLM 生成 N 条事实
  │    返回 [{"id":"f1","text":"...","length_hint":"short|medium|long"}, ...]
  └─ 若 LLM 不可用 → _generate_fallback_memories() 返回预置中文示例
```

`background_memories_prompt` 模板（来自 `configs/custom/user_simulator_default.yaml`）让 LLM 扮演一个测试场景生成器，模拟后端开发者在技术项目中与 AI 助手分享的事实信息（架构决策、会议结论、技术障碍、截止日期等），要求使用中文输出。

#### 步骤 2：LLM 生成用户查询

```
MemoryDynamicEvaluator.generate_next_query(context)
  ├─ 调用 memory/dynamic_evaluator.py:690 _generate_dynamic_query()
  │    使用 user_simulator_config 中的 persona_prompt
  │    prompt 占位符替换：
  │      {background_facts}      → 背景事实列表
  │      {conversation_history}   → 历史对话（从最近向最远累积，上限 ≈64K tokens / 256K 字符）
  │      {round_index}            → 当前轮次
  │      {is_new_session}         → 是否新会话
  │    LLM 返回 JSON: { query, ground_facts, complexity, reasoning, new_session_hint }
  └─ 若 LLM 不可用 → _fallback_query() 用模板拼装简单召回问题
```

`persona_prompt` 模板让 LLM 模拟一个后端工程师：
- 用技术术语自然表达
- **模糊引用**过去的讨论（如「上次说的那个方案」「之前定的时间」），不直接复述事实
- 不透露自己在测试记忆召回
- 生成中文对话

输出中 `ground_facts` 标注了该查询依赖哪些背景事实 ID，用于后续评分。

#### 步骤 3：模拟打字 + Prefill 预热

```python
# scripts/echoagent_live_test.py:260 simulate_typing()
client_turn_id = uuid.uuid4().hex[:12]
for i in range(1, len(query) + 1):
    draft = query[:i]  # 逐字符截取
    client.prefetch_tick(session_id, context_path, client_turn_id, i, draft)
    #  每个 tick 发往 EchoAgent → 触发 EchoMem prefill 管线：
    #    ProbeSplitter 判定是否需要 probe → 检索记忆 → max_tokens=1 预热 KV cache
    time.sleep((typing_speed_ms + random.randint(-jitter_ms, jitter_ms)) / 1000)

# 打字完成后，finalize 获取 prefill 结果
finalize_result = client.prefetch_finalize(session_id, context_path, client_turn_id, query)
#  返回: { accepted: bool, memoryItems: [...], reason: "..." }
```

这直接测试 EchoMem 的 prefill 管线在用户打字期间能否提前召回记忆并预热 KV cache，从而降低首 token 延迟。

#### 步骤 4：发送消息 + 流式接收回复

```python
# 发送完整消息
msg_result = client.send_message(session_id, context_path, query, client_turn_id)
seq = msg_result["data"]["latestContextSeq"]

# SSE 流式接收回复，测量 TTFT
reply_result = client.stream_reply(session_id, context_path, seq)
#  解析 SSE 事件:
#    "create"/"append" → 收集 fragment, 记录 TTFT（首个 token 时间）
#    "done"            → 提取 cachedTokens, promptTokens
#    "error"           → 记录错误
```

#### 步骤 5：LLM 评估回复质量

测试完成后调用 `generate_quality_report()`（`:393`）：
- 将所有查询、回复、ground_facts 发给 LLM
- LLM 对每条查询评分：`recall_score`(0-2)、`factual_accuracy`(0-2)、`relevance`(0-2)
- 输出聚合分数：`overall_score`、`cross_session_score`、`same_session_score`

### 2.5 跨会话记忆测试

```python
# scripts/echoagent_live_test.py:884-898
need_new = not session_id
if not need_new and round_data.get("new_session"):
    if random.random() < args.new_session_ratio:  # 默认 0.3
        need_new = True
if need_new:
    session_id = client.create_session(
        title=f"test-{evaluator.theme}-{batch_idx}-s{session_count}",
        memory_engine_endpoint=args.memory_engine_endpoint,  # 启用记忆引擎
    )
```

通过 `new_session_ratio` 控制新会话概率，模拟用户切换会话后仍期望 Agent 记住之前的内容，测试跨会话记忆持久性。

### 2.6 数据集回放模式

当通过 `--dataset` 参数指定数据集时（`run_replay_test()`，`:574`）：
1. 从 LoCoMo 数据集加载对话历史
2. 将对话历史逐条注入 EchoAgent 会话（模拟用户说过的话）
3. 在**新会话**中提出数据集的 QA 问题
4. 测量跨会话召回准确性和延迟

### 2.7 动态评测 API 层

`web/api/dynamic_eval.py` 提供独立于任务系统的 HTTP 端点，支持前端实时交互式评测：

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/dynamic/generate_background_memories` | POST | 创建 evaluator + 生成背景记忆 |
| `/api/dynamic/generate_user_query` | POST | 生成下一条用户查询 |
| `/api/dynamic/evaluate_response` | POST | LLM 评估回复质量（10 维度） |
| `/api/dynamic/inject_memories` | POST | 将记忆注入 EchoMem（open→messages→commit→poll） |
| `/api/dynamic/echo_agent` | POST | 代理请求到 EchoAgent 后端（带 cookie 管理） |
| `/api/dynamic/evaluators` | GET | 列出活跃的 evaluator 实例 |
| `/api/dynamic/evaluators/:id` | GET | 获取 evaluator 状态 |
| `/api/dynamic/evaluators/:id` | POST | 操作：add_history / get_state / stop / clear_stop |
| `/api/dynamic/evaluators/:id` | DELETE | 删除 evaluator |
| `/api/dynamic/user_simulators` | GET | 列出可用的用户模拟器配置 |
| `/api/dynamic/evaluator_configs` | GET | 列出可用的评估器配置 |

### 2.8 用户模拟器与评估器配置

前端顶部选择器加载两类 YAML 配置：

**用户模拟器配置**（`configs/user_simulator/*.yaml` 或 `configs/custom/user_simulator_default.yaml`）：
- `background_memories_prompt`：背景事实生成 prompt，含 `{num_memories}` 占位符
- `persona_prompt`：用户画像 prompt，含 `{background_facts}`、`{conversation_history}`、`{round_index}`、`{is_new_session}` 占位符
- 初始化时校验所有占位符存在（`memory/dynamic_evaluator.py:53` `_validate_user_simulator_config`）

**评估器配置**（`configs/custom/evaluator_template.yaml`）：
- `dimensions`：10 个评分维度，总分 100
- `evaluate_prompt`：评估 prompt，含 `{query}`、`{reply}`、`{ground_facts}`、`{recalled_memories}`、`{dimension_criteria}` 占位符
- 初始化时校验维度结构和占位符（`memory/dynamic_evaluator.py:98` `_validate_evaluator_config`）

10 个评分维度：

| 维度 | 字段名 | 满分 | 说明 |
|------|--------|------|------|
| 任务完成度 | `task_completion_score` | 15 | Agent 是否成功完成用户预期任务 |
| 事实覆盖 | `fact_coverage_score` | 15 | 预期事实被正确使用的程度 |
| 信息准确性 | `information_accuracy_score` | 15 | 信息是否正确，无幻觉 |
| 回复相关性 | `response_relevance_score` | 10 | 是否直接回答用户问题 |
| 记忆利用 | `memory_utilization_score` | 10 | 召回记忆是否有效提升回复 |
| 回复连贯性 | `response_coherence_score` | 10 | 逻辑结构和可读性 |
| 有用性 | `helpfulness_score` | 10 | 回复的帮助程度 |
| 回复效率 | `response_efficiency_score` | 5 | 是否简洁无冗余 |
| 安全性 | `safety_appropriateness_score` | 5 | 是否安全专业 |
| 用户体验 | `user_experience_score` | 5 | 整体满意度 |

---

## 3. 评测功能点

### 3.1 记忆召回准确性

通过 LLM 生成的 ground_facts 或标准数据集答案，测试 EchoMem 能否召回与用户查询相关的记忆，并让 EchoAgent 据此给出正确回复。

- **动态模式**：LLM 生成背景事实 + 模糊引用查询，LLM 判分
- **静态模式**：从 LoCoMo 数据集加载对话和 QA，标准答案对照
- **回放模式**：将数据集对话注入 EchoAgent 后在新会话提问

### 3.2 跨会话记忆持久性

通过 `new_session_ratio`（默认 0.3-0.4）控制新会话创建概率：
- **同会话召回**：当前会话中提到的信息在后续轮次被引用
- **跨会话召回**：之前会话中的信息在新会话被引用（`cross_session_score` 单独统计）

### 3.3 Prefill 管线效果

逐字符打字模拟触发 EchoMem 的 prefill 管线，测量：
- `prefetch_committed`：prefill 是否被接受并提交
- `cached_tokens`：KV cache 预热命中的 token 数
- `prompt_tokens`：总 prompt token 数
- TTFT 对比：prefill 命中 vs 未命中时的首 token 延迟差异

### 3.4 端到端延迟

| 指标 | 来源 | 说明 |
|------|------|------|
| TTFT (ms) | SSE 流首个 token 时间 | 用户发消息到收到首个回复 token 的延迟 |
| TTFT P50/P95/P99 | Prometheus 直方图 | 从 EchoAgent `/metrics` 采集的百分位延迟 |
| EchoMem API 耗时 | Prometheus 直方图 | EchoMem 检索接口响应时间 |
| 检索耗时 | Prometheus 直方图 | EchoMem 内部检索阶段耗时 |

### 3.5 LLM 回复质量评估

通过 `MemoryDynamicEvaluator.evaluate_response()` 或 `generate_quality_report()` 用 LLM-as-judge 评估：

**10 维度评分**（`evaluate_response`，总分 100）：
- 见 [2.8 节](#28-用户模拟器与评估器配置) 的维度表
- 额外输出：`matched_facts`、`recall_helped`、`hallucination_detected`、`task_completed`、`strengths`、`weaknesses`

**召回质量评分**（`generate_quality_report`，0-2 量表）：

| 指标 | 范围 | 说明 |
|------|------|------|
| `recall_score` | 0-2 | 2=全部事实正确使用，1=部分，0=未使用 |
| `factual_accuracy` | 0-2 | 2=无幻觉，0=严重幻觉 |
| `relevance` | 0-2 | 2=直接相关，0=不相关 |
| `ground_fact_coverage` | 0.0-1.0 | 事实覆盖率 |
| `overall_score` | 0.0-2.0 | 全部查询平均召回分 |
| `cross_session_score` | 0.0-2.0 | 新会话查询平均召回分 |
| `same_session_score` | 0.0-2.0 | 同会话查询平均召回分 |

### 3.6 严格黑盒指标

适用于工作流 (A) HTTP 黑盒 QA，从 QA 结果 CSV 和导入摘要中计算 15 项核心指标：

| 指标 | 计算方式 |
|------|---------|
| 准确率 | `CORRECT / (CORRECT + WRONG)` |
| QA 请求成功率 | 四个状态字段(retrieval/answer/model/health)全为 ok 的比例 |
| 空召回率 | `retrieval_count=0` 的题占比 |
| 失败率 | 未满足成功条件的完整状态行占比 |
| 重试率 | `model_retry_count>0` 的题占比 |
| 每正确答案 Token | 总回答 Token / CORRECT 数 |
| Judge 模型 Token | 判分消耗的 Token 总量 |
| 可见模型总 Token | 回答 Token + 判分 Token |
| 消息提交率 | `submitted_messages / expected_messages` |
| 记忆导入状态 | 直接读取导入摘要 status |
| QA 并行度 | 配置值 |
| 批次墙钟时间 | 首尾时间戳之差 |
| QA 吞吐量 (QPS) | 结果行数 / 墙钟时间 |
| 内部记忆注入 Token | N/A（黑盒不可观测） |
| 初始记忆导入时间 | N/A（无可靠完成事件） |

另含可展开表：分类准确率分解、延迟分布（端到端/检索/注入/LLM 的 P50/P95/P99/max）、Token 分布（prompt/completion/total）。

### 3.7 数据集回放

将 LoCoMo 数据集对话作为真实用户交互回放到 EchoAgent：
- 对话历史逐条注入（模拟用户说过的话）
- 在新会话提出数据集 QA 问题
- 对照标准答案评估召回准确性

### 3.8 结果导出为数据集

`save_as_locomo_dataset()`（`echoagent_live_test.py`）将 LLM 生成的交互导出为 LoCoMo 格式 JSON，QA 的 `answer` 字段使用 ground_facts 对应的事实文本（而非 AI 回复）：
- 包含 `background_memories`、`samples`（按会话分组的对话轮次）
- 自动注册到 `dataset/manifest.json`
- 可用于后续回放评测或对比实验

---

## 4. 评测系统需要的输入

### 4.1 EchoAgent 连接

| 输入 | 来源 | 默认值 |
|------|------|--------|
| EchoAgent URL | 前端表单 `wbEchoAgentUrl` | `http://127.0.0.1:31020` |
| 用户名 | 前端表单 `wbEchoAgentUsername` | `test_user` |
| 密码 | 前端表单 `wbEchoAgentPassword` | `test_password` |
| Memory Engine Endpoint | CLI `--memory-engine-endpoint` | `http://127.0.0.1:31030` |

用于 JWT 登录、创建会话、启用记忆引擎、发送消息、SSE 流式接收回复。

### 4.2 EchoMem 连接

| 输入 | 来源 | 默认值 |
|------|------|--------|
| EchoMem URL | 前端表单 `wbEchoMemUrl` | `http://127.0.0.1:8010` |
| Auth Key | `EchoMem/echoagent_registry.json` 自动查找 | (自动) |

用于运行时指标采集（Prometheus `/metrics`）和记忆注入（动态评测 API 的 `inject_memories` 端点）。Auth Key 自动从 `echoagent_registry.json` 查找，找不到则自动创建租户和用户。

### 4.3 LLM 配置

| 输入 | 来源 | 默认值 | 用途 |
|------|------|--------|------|
| 模型名 | 前端表单 `wbScenarioModel` | `deepseek-v4-flash` | 生成记忆、查询、评估 |
| API Base URL | 前端表单 `wbScenarioBaseUrl` | (空→环境变量) | LLM API 地址 |
| API Key | 前端表单 `wbScenarioApiKey` | (空→环境变量) | LLM API 密钥 |

留空时从环境变量 `ECHOAGENT_TEST_SCENARIO_BASE_URL` / `ECHOAGENT_TEST_SCENARIO_API_KEY` 读取。

### 4.4 测试参数

| 输入 | 来源 | 范围 | 说明 |
|------|------|------|------|
| 测试批次 | 前端 `wbNumBatches` | 1-20 | 重复测试批次数 |
| 每批查询数 | 前端 `wbQueriesPerBatch` | 1-20 | 每批 LLM 生成的查询数 |
| 自定义场景 | 前端 `wbCustomScenario` | 文本 | 留空则 LLM 自动生成 |
| 新会话比例 | CLI `--new-session-ratio` | 0.0-1.0 | 默认 0.3 |
| 打字速度 | CLI `--typing-speed-ms` | ms | 默认 200ms/字符 |
| 打字抖动 | CLI `--typing-jitter-ms` | ms | 默认 ±20ms |

### 4.5 YAML 配置文件

**用户模拟器配置**（`configs/user_simulator/*.yaml` 或 `configs/custom/user_simulator_default.yaml`）：

```yaml
# 必填字段
background_memories_prompt: |     # 必须含 {num_memories} 占位符
  ...
persona_prompt: |                  # 必须含 {background_facts} {conversation_history}
  ...                              #   {round_index} {is_new_session} 占位符
```

前端通过 `GET /api/dynamic/user_simulators` 获取可用列表，顶部下拉框选择。

**评估器配置**（`configs/custom/evaluator_template.yaml` 或 `configs/evaluator/*.yaml`）：

```yaml
# 必填字段
dimensions:                        # 非空列表
  - name: task_completion_score    # 必须含 name, display_name, max_score
    display_name: "任务完成度"
    max_score: 15
    description: "..."
evaluate_prompt: |                 # 必须含 {query} {reply} {ground_facts}
  ...                              #   {recalled_memories} {dimension_criteria}
```

前端通过 `GET /api/dynamic/evaluator_configs` 获取可用列表，顶部下拉框选择。

**动态评测配置**（`configs/dynamic_eval/dynamic_config.yaml`）：

```yaml
mode: dynamic              # 或 static
theme_pool: [...]          # 12 个主题
num_memories: 10
queries_per_test: 8
new_session_ratio: 0.4
typing_simulation:
  enabled: true
  speed_ms: 100
  jitter_ms: 20
llm_config: { model, base_url, api_key, temperature, timeout }
evaluator_llm_config: { ... }
echoagent: { url, username, password, memory_engine_endpoint }
echomem: { url }
runtime_metrics: { enabled, echoagent_url, echomem_url }
```

### 4.6 环境变量

| 变量 | 用途 |
|------|------|
| `ECHOAGENT_URL` | EchoAgent 地址（CLI 默认） |
| `ECHOMEM_URL` | EchoMem 地址（CLI 默认） |
| `ECHOAGENT_TEST_USERNAME` / `ECHOAGENT_TEST_PASSWORD` | 登录凭据 |
| `ECHOAGENT_TEST_BATCHES` / `ECHOAGENT_TEST_QUERIES` | 批次/查询数默认值 |
| `ECHOAGENT_TEST_NEW_SESSION_RATIO` | 新会话比例默认值 |
| `ECHOAGENT_TEST_TYPING_SPEED` / `ECHOAGENT_TEST_TYPING_JITTER` | 打字参数默认值 |
| `ECHOAGENT_TEST_SCENARIO_MODEL` | 场景模型默认值 |
| `ECHOAGENT_TEST_SCENARIO_BASE_URL` / `ECHOAGENT_TEST_SCENARIO_API_KEY` | LLM API 配置 |
| `GLOBAL_MEMORY_ENGINE_ENDPOINT` | 记忆引擎端点 |
| `ECHOMEM_BASE_URL` / `ECHOMEM_AUTH_KEY` | EchoMem 连接（黑盒 QA 模式） |
| `JUDGE_BASE_URL` / `JUDGE_MODEL` / `JUDGE_TOKEN` | 判分模型配置 |
| `DASHSCOPE_BASE_URL` / `DASHSCOPE_API_KEY` | 嵌入模型配置（导入） |

### 4.7 数据集文件（静态/回放模式）

| 数据集 | 格式 | 用途 |
|--------|------|------|
| LoCoMo | JSON 数组，含 `sample_id`、`conversation`(session_N)、`qa`(question/answer/category) | 记忆召回基准评测 |
| LongMemEval | JSONL/JSON，含 `question_id`、`question`、`answer`、`question_type` | 长期记忆评测 |
| HotpotQA | JSON，含 `_id`、`question`、`answer`、`context`、`supporting_facts` | 多跳推理评测 |

---

## 5. 评测系统能输出什么

### 5.1 EchoAgent 交互测试输出

测试结果写入 `runs/<run_id>/echoagent_live_test/` 目录：

| 文件 | 格式 | 内容 |
|------|------|------|
| `echoagent_live_test_results.json` | JSON | 完整结果：testId、config、summary、facts、rounds（每轮含 query/reply/ttft_ms/cached_tokens/prompt_tokens/prefetch_committed/ground_facts/relevant_memory） |
| `echoagent_live_test_results.csv` | CSV | 每轮一行：round_id、session_id、query、reply_length、query_length、ttft_ms、cached_tokens、prompt_tokens、prefetch_committed、is_new_session、is_injection、complexity、error、relevant_memory |
| `quality_report.json` | JSON | LLM 质量评估：per_query（recall_score/factual_accuracy/relevance/reasoning）、overall_score、cross_session_score、same_session_score、summary |
| `summary.json` | JSON | 汇总：total_queries、total_rounds、new_sessions、avg_query_length、avg_reply_length、avg_ttft_ms、avg_cached_tokens、avg_prompt_tokens、config |
| `dataset.json` | JSON | 导出的 LoCoMo 格式数据集：background_memories + samples(按会话分组对话) |
| `run.log` | 文本 | 运行日志，含每轮 query、ttft、cached_tokens 等 |

### 5.2 黑盒 QA 模式额外输出

| 文件 | 格式 | 内容 |
|------|------|------|
| `echomemory_memory_qa_results.csv` | CSV | 逐题：question_id、question、answer、response、result(CORRECT/WRONG)、reasoning、time_cost、retrieval_count、retrieval_status、answer_status、model_status、health_status、token 用量、延迟字段 |
| `echomemory_import_summary.json` | JSON | 导入状态、样本记录、完整性阶段(live_complete/archive_complete/qa_ready)、token 用量、消息计数 |
| `qNNN.recall.json` | JSON | 逐题召回证据：hits、query、retrieval_error |
| `judge_summary.json` | JSON | 判分计数、准确率、token 用量 |
| `summary.txt` | 文本 | 简单准确率/Token 统计 |
| `*_report.html` | HTML | 可视化报告：指标卡、分类分解、延迟/Token 表、逐题详情 |
| `strict_blackbox_metrics.json` | JSON | 缓存的严格黑盒指标快照（含源签名） |
| `manifest.json` | JSON | 任务清单（密钥已脱敏） |
| `config_snapshot.json` | JSON | 任务配置快照 |

### 5.3 运行时指标输出

`RuntimeMetricsClient`（`scripts/runtime_metrics_client.py`）从 Prometheus `/metrics` 端点采集：

| Prometheus 指标 | 类型 | 标签 | 说明 |
|-----------------|------|------|------|
| `echoagent_turn_ttft_seconds` | histogram | pipeline, status | TTFT 分布 |
| `echoagent_generate_cached_tokens` | histogram | pipeline, status | 缓存 token 分布 |
| `echoagent_generate_prompt_tokens` | histogram | pipeline, status | prompt token 分布 |
| `echoagent_prefill_warmup_duration_seconds` | histogram | - | prefill 预热耗时 |
| `echoagent_prefill_warmup_cached_tokens` | histogram | - | prefill 缓存 token |
| `echoagent_echomem_api_duration_seconds` | histogram | - | EchoMem API 耗时 |
| `echomem_retrieval_duration_seconds` | histogram | - | EchoMem 检索耗时 |

计算 P50/P95/P99/mean/sum，支持前后快照 diff 计算。

### 5.4 前端展示

| 展示区域 | 内容 |
|---------|------|
| Import 进度面板 | 进度条、百分比、状态、输出目录、结果文件、任务类型、阶段、已处理/总数 |
| QA 指标卡片 | 准确率、correct/wrong/pending 计数、answer F1/EM、joint F1/EM |
| 严格黑盒指标表 | 15 项核心指标 + 可展开分类准确率/延迟分布/Token 分布表 |
| Recall Workbench（LoCoMo） | 逐题检索 trace：hit/miss/partial 证据、judge 结果、召回记忆条目 |
| Run 历史 | 按时间排列的运行记录卡片，含 benchmark 特定摘要 |
| Report 导出 | 导出 HTML 报告按钮 + 打开 summary/CSV/report.html 路径按钮 |

---

## 6. 评测系统本身的功能点

### 6.1 四阶段流水线

所有 benchmark 共用统一的 Import → QA → Judge → Report 流程，前端通过 `src/controller.js` 管理阶段切换：
- 每阶段有独立的配置面板和进度展示
- 阶段间有预检门控（preflight gate）
- 状态持久化到 localStorage（活跃 benchmark、阶段、账号、配置选择）

### 6.2 多 Benchmark 支持

| Benchmark | 标识 | 评测类型 | 数据集 |
|-----------|------|---------|--------|
| LoCoMo | `locomo` | 记忆召回 QA | LoCoMo 对话数据集 |
| HotpotQA | `hotpotqa` | 多跳推理 QA | HotpotQA |
| LongMemEval | `longmemeval` | 长期记忆 QA | LongMemEval |
| EchoAgent 交互 | `echoagent_live` | LLM 模拟用户实时交互 | LLM 动态生成或数据集回放 |

### 6.3 任务编排

`server.py`（~8500 行）统一管理所有评测任务：
- `POST /api/tasks`：创建任务，由 `task_orchestrator.py` 规范化配置、检查就绪状态、解析 token、构建环境变量、创建子进程线程
- `GET /api/tasks`：列出所有任务及进度
- `GET /api/tasks/:id/log`：轮询任务日志
- `POST /api/tasks/stop-all`：停止所有任务
- `TaskSpec`（`memory/task_specs.py`）：为每种任务类型构建 CLI 命令数组

### 6.4 预检门控系统

**LoCoMo QA 启动门控**（`src/action/locomo-helpers.js`）— 11 项检查：
1. 数据集已选择
2. workspace 路径有效
3. 记忆身份配置（user_id/agent_id）
4. echomem_root 有效
5. 后端已选择
6. 回答模型已配置
7. 回答模型 API 地址有效
8. 回答模型 Token 有效
9. 运行时已就绪
10. 导入已完成（import_ready）
11. 无同范围并发任务（single_flight）

**LoCoMo Judge 预检** — 6+ 项检查：结果 CSV 存在、写入器空闲、judge 模型/地址/token 有效、pending 行数、validate 通过。

**Official（HotpotQA/LongMemEval）QA 门控**：活跃任务检查、数据集、workspace、数量、top_k、回答模型、API 地址、模型探测。

**Official Judge 预检**：当前结果、运行完成、输出文件、数据集、import-only 检查、行数、official eval 后、official summary 状态。

### 6.5 用户模拟器/评估器配置管理

- **YAML 加载**：`memory/prompt_config_loader.py` 从 `configs/user_simulator/`、`configs/evaluator/` 加载配置，未找到时回退搜索 `configs/custom/`
- **占位符校验**：初始化时校验所有必需占位符存在（`_validate_user_simulator_config` / `_validate_evaluator_config`）
- **前端下拉选择**：启动时 `GET /api/dynamic/user_simulators` 和 `GET /api/dynamic/evaluator_configs` 加载可用列表
- **支持直接传入 YAML 内容**：`user_simulator_config_yaml` / `evaluator_config_yaml` 字段可直接传入 YAML 字符串

### 6.6 动态评测引擎

`MemoryDynamicEvaluator`（`memory/dynamic_evaluator.py`，1063 行）：
- **全局注册表**：`create_evaluator()` / `get_evaluator()` / `list_evaluators()` / `remove_evaluator()`
- **停止/恢复**：`set_evaluator_stopped()` / `is_evaluator_stopped()` / `clear_evaluator_stop_flag()` — 支持用户手动停止评测
- **TTL**：evaluator 实例 1 小时后过期，`get_evaluator()` 访问时自动清理
- **线程安全**：`threading.Lock` 保护注册表和停止标志

### 6.7 记忆注入

`web/api/dynamic_eval.py:25` `_inject_memories_to_echomem()` 通过 EchoMem HTTP API 自动注入背景记忆（轮询提交状态无超时，因注入耗时较长）：
1. 查找 auth_key（先查 `echoagent_registry.json`，找不到自动创建租户+用户+key）
2. 检查会话是否已有已提交归档（有则跳过注入）
3. `POST /api/sessions/open` 打开会话
4. 逐条 `POST /api/sessions/{id}/messages` 添加记忆文本
5. `POST /api/sessions/{id}/commit` 提交（触发异步抽取为 atom/graph/episode/向量）
6. 轮询 `GET /api/sessions/{id}/commits/{archive_id}` 等待完成（`while True` 无超时，支持 evaluator 停止中断）

### 6.8 EchoAgent 代理

`web/api/dynamic_eval.py:264` `_proxy_to_echo_agent()` 提供通用代理：
- 支持 GET/POST/PUT/DELETE 方法
- 自动管理 cookie（`http.cookiejar.CookieJar`）
- 返回 {status, headers, body, cookies}
- 前端可通过 `/api/dynamic/echo_agent` 代理任意 EchoAgent 请求

### 6.9 运行时指标采集

`RuntimeMetricsClient`（`scripts/runtime_metrics_client.py`，552 行）：
- `fetch_metrics()`：同时从 EchoAgent(31020) 和 EchoMem(8010) 的 `/metrics` 端点拉取 Prometheus 指标
- `parse_prometheus_text()`：解析 Prometheus 文本格式
- `histogram_quantile()` / `histogram_mean()` / `histogram_sum()`：计算百分位和均值
- `diff_metrics(before, after)`：计算两次快照间的增量
- `extract_turn_metrics(metrics, pipeline, status)`：按 pipeline 标签提取 TTFT P50/P95/mean、cached_tokens_sum、prompt_tokens_sum、prefill_duration、EchoMem API 耗时、检索指标

### 6.10 重试机制

- **失败问题重试**：通过 QA diagnostics 找到可重试的失败问题 ID，重新提交
- **缺失问题重试**：找到 CSV 中缺失的问题 ID，补充执行
- **错误 CSV 聚类重跑**：`resolveWrongCsvQuestionSet()` 获取错误问题集，若 CSV 不存在则自动从失败归因分析生成
- 前端为每个 benchmark 提供独立重试按钮

### 6.11 报告导出

- **HTML 报告**（`scripts/generate_html_report.py`，1301 行）：指标卡、分类准确率分解、延迟分布表、Token 分布表、逐题详情、metric 定义表（含 formula/source/meaning/boundary）
- **CSV 导出**：`GET /api/export-pending-csv` 导出待判分行
- **JSON sidecar**：`strict_blackbox_metrics.json` 缓存严格指标快照（含源签名防过期）
- **路径打开**：`POST /api/open-path` 在服务器端打开文件/目录

### 6.12 配置持久化

- `POST /api/account-config`：保存每个账号的评测配置
- `GET /api/account-config`：读取账号配置
- 前端 `localStorage`：活跃 benchmark、阶段、选中账号、后端、用户模拟器配置、评估器配置

### 6.13 双后端支持

通过 adapter/plugin 机制支持两种记忆后端：
- **EchoMemory**：`memory/plugins/echomemory/`（agent.py、inspector.py、plugin.py、tasks.py）
- **OpenViking**：`memory/plugins/openviking/`（agent.py、client.py、inspector.py、plugin.py、runtime.py、tasks.py）
- `MemoryPluginService`（`memory/plugins/service.py`）作为统一门面
- 前端顶部 `backend` 选择器切换

### 6.14 严格黑盒边界

QA 脚本 `echomemory_memory_qa.py` 强制黑盒模式：
- 禁用所有平台侧证据源（`local_session_summaries`、`local_segments`、`local_atoms`、`local_messages` 等）
- 如有任何平台证据源被启用则报错退出
- 仅允许 `POST /api/retrieval/search` 结果和可选的 `GET /fs/read` 概览增强
- 默认 `--evidence-policy blackbox`、`--retrieval-source-mode echo_http_native`

### 6.15 数据集管理

`memory/datasets.py`（734 行）：
- `infer_dataset_format()`：自动检测 locomo/longmemeval/hotpotqa/chenmo/generic 格式
- `dataset_overview()`：生成数据集预览（样本数、QA 数、分类分布）
- `benchmark_questions()`：从数据集提取标准 QA 问题
- 前端 `GET /api/datasets` 展示可用数据集列表

---

## 附录：关键文件索引

| 文件 | 行数 | 职责 |
|------|------|------|
| `scripts/echoagent_live_test.py` | 1237 | EchoAgent 交互测试主脚本：LLM 模拟用户、打字模拟、SSE 接收、质量评估 |
| `memory/dynamic_evaluator.py` | 1063 | 动态评测引擎：生成背景记忆、生成用户查询、评估回复质量 |
| `web/api/dynamic_eval.py` | 721 | 动态评测 HTTP API：记忆注入、查询生成、EchoAgent 代理、evaluator 管理 |
| `memory/llm.py` | 312 | LLM 客户端：openai_chat() / claude_chat() |
| `memory/prompt_config_loader.py` | 458 | YAML 配置加载器：用户模拟器和评估器配置 |
| `scripts/accuracy_evaluator.py` | 418 | 召回质量评估器：recall_score/factual_accuracy/relevance |
| `scripts/runtime_metrics_client.py` | 552 | Prometheus 指标采集客户端 |
| `memory/strict_blackbox.py` | 586 | 严格黑盒指标计算与 HTML 报告生成 |
| `memory/reports.py` | 1277 | QA 结果 CSV 解析、失败归因、运行对比 |
| `scripts/generate_html_report.py` | 1301 | HTML 报告生成器 |
| `server.py` | ~8500 | 评测后端 API 服务器 |
| `memory/services/task_orchestrator.py` | 609 | 任务编排：规范化配置、就绪检查、任务创建 |
| `memory/task_specs.py` | 560 | TaskSpec 构建器：为每种任务类型构建 CLI 命令 |
| `src/render/import-echoagent.js` | 41 | EchoAgent 交互评测前端表单渲染 |
| `src/action/echoagent.js` | 70 | EchoAgent 交互评测启动动作 |
| `src/form-readers.js` | 277 | 前端表单值读取器 |
| `src/controller.js` | 1465 | 前端主控制器：阶段切换、配置加载、事件绑定 |
| `configs/custom/user_simulator_default.yaml` | 120 | 用户模拟器默认配置（中文输出） |
| `configs/custom/evaluator_template.yaml` | 109 | 评估器模板（10 维度，100 分） |
| `configs/dynamic_eval/dynamic_config.yaml` | 83 | 动态评测配置模板 |
| `configs/echoagent_live_test/metrics_config.yaml` | 254 | 运行时指标和精度指标配置 |
