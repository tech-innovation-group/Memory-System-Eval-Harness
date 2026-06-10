# LoCoMo Eval Web 系统的上下文工程实现

## 📐 系统架构

你的系统是一个 **Web 评测平台**，包含：
- **前端**: HTML + JavaScript (app.js)
- **后端**: Python HTTP Server (server.py)
- **数据库**: OpenViking (向量数据库)
- **LLM**: 支持 GPT-5.5, DeepSeek 等

---

## 🧠 上下文管理核心实现

### 1. 上下文构建流程

```python
def build_agent_context_preview(payload):
    # 1. 检索记忆
    retrieval = ranked_memory_search(
        ov_url, last_user, account, user_id, agent_id, 
        api_key, top_k=30, workspace=workspace
    )
    
    # 2. 构建 5 个上下文块
    blocks = [
        Block 1: Agent Charter (静态规则)
        Block 2: Behavior Policy (行为规则)
        Block 3: Retrieved Memory (检索到的记忆) ⭐
        Block 4: Recent Conversation (最近对话)
        Block 5: Current Request (当前请求)
    ]
    
    # 3. 组装 System Prompt
    system = blocks[1] + blocks[2] + blocks[3]
    
    # 4. 构建 model_messages
    model_messages = [
        {"role": "system", "content": system},
        ...history_messages,
        {"role": "user", "content": current_request}
    ]
    
    # 5. 返回 context_trace (用于前端显示)
    return {
        "messages": model_messages,
        "retrieval": retrieval,
        "context_trace": {
            "phase": "openviking-readonly-context-v1",
            "blocks": blocks,
            "layers": trace_layers,
            "prompt_tokens_est": prompt_tokens_est,
            "memory_hits": len(retrieval["items"]),
            ...
        }
    }
```

---

## 📝 上下文块详解

### Block 1: Agent Charter (Agent 章程)

```xml
<agent_charter>
You are a read-only memory QA agent inside the LoCoMo/OpenViking evaluation harness.
Your job is to answer the current user request using retrieved long-term memory as the primary evidence.
Give a direct answer first, then briefly explain the evidence when useful.
Never claim that you wrote, committed, or updated memory during this chat.
</agent_charter>
```

**作用**: 定义 Agent 的角色和职责

---

### Block 2: Behavior Policy (行为规则)

```xml
<behavior_policy>
Use the retrieved memory block before relying on general knowledge.
If the retrieved memory is insufficient or conflicting, say what is uncertain instead of inventing facts.
Prefer specific dates, people, places, and causal links when they appear in memory.
Keep the answer concise and in the same language as the user's request when practical.
</behavior_policy>
```

**作用**: 指导 Agent 如何使用记忆和回答问题

---

### Block 3: Retrieved Memory (检索到的记忆) ⭐ 核心

```xml
<retrieved_memory 
    source="OpenViking" 
    account="default" 
    user="default" 
    agent="default" 
    top_k="30" 
    score_threshold="0.1">

viking://user/default/memories/preferences/Gina/dance_styles.md
score: 0.593
Gina loves contemporary dance, describing it as expressive and graceful...

viking://user/default/memories/entities/passion/dance.md
score: 0.587
Gina loves dance and uses it as stress relief...

[... 更多记忆 ...]

</retrieved_memory>
```

**作用**: 
- 包含检索到的所有相关记忆
- 显示 URI、score、内容
- 这是 LLM 回答问题的主要证据来源

---

### Block 4: Recent Conversation (最近对话)

```xml
<recent_conversation>
USER: How do Jon and Gina both like to destress?
ASSISTANT: Both Jon and Gina use dance as a way to destress.
USER: When did Jon lose his job?
</recent_conversation>
```

**作用**: 提供对话历史上下文（最多 10 轮）

---

### Block 5: Current Request (当前请求)

```xml
<current_request>
What cities has Jon visited?
</current_request>
```

**作用**: 当前用户的问题

---

## 🎨 前端可视化

### 1. Context Trace 显示

前端通过 `renderContextTrace()` 函数显示上下文信息：

```javascript
function renderContextTrace(data) {
    const trace = data.context_trace || {};
    const blocks = trace.blocks || [];
    
    // 显示统计信息
    - Context Phase: openviking-readonly-context-v1
    - Blocks: 5
    - Memory Hits: 30
    - Full Files: 15
    - Tokens Est.: 2500
    
    // 显示每个上下文块
    blocks.forEach(block => {
        显示:
        - #1 system · Agent 章程
        - #2 system · 行为规则
        - #3 system · Retrieved Memory (高亮)
        - #4 history · 最近对话
        - #5 user · 当前请求
        
        每个块显示:
        - 内容预览
        - 字符数
        - Token 估算
    });
}
```

### 2. Memory Evidence 显示

```javascript
function renderMemoryEvidence(items, error) {
    items.forEach((item, index) => {
        显示:
        - URI: viking://user/default/memories/...
        - Score: 0.593
        - Abstract: 记忆摘要
        - 来源: memory_file 或 search_abstract
    });
}
```

---

## ⚙️ 配置参数

### 检索参数

```python
top_k = 30  # 检索记忆数量
score_threshold = 0.1  # 相似度阈值
```

### 上下文限制

```python
# 记忆片段限制
def memory_snippet(item):
    return item.get("content", "")[:2200]  # 每条记忆最多 2200 字符

# 对话历史限制
history_messages = messages[-10:]  # 最多保留 10 轮对话

# Prompt 压缩
def compact_for_prompt(text, limit=1200):
    return text[:limit-3] + "..." if len(text) > limit else text
```

---

## 🔄 完整流程示例

### 用户提问: "How do Jon and Gina both like to destress?"

#### 1. 前端发送请求

```javascript
POST /api/agent/chat
{
    "messages": [
        {"role": "user", "content": "How do Jon and Gina both like to destress?"}
    ],
    "model": "gpt-5.5",
    "account": "default",
    "user_id": "default",
    "agent_id": "default",
    "top_k": 30,
    "use_memory": true
}
```

#### 2. 后端检索记忆

```python
# 调用 OpenViking API
retrieval = ranked_memory_search(
    "http://127.0.0.1:19080",
    "How do Jon and Gina both like to destress?",
    account="default",
    user_id="default",
    agent_id="default",
    top_k=30
)

# 返回 30 条记忆，按相似度排序
retrieval = {
    "items": [
        {
            "uri": "viking://user/default/memories/preferences/Gina/dance_styles.md",
            "score": 0.593,
            "abstract": "Gina loves contemporary dance...",
            "content": "完整内容..."
        },
        ...
    ]
}
```

#### 3. 构建上下文

```python
# 组装 System Prompt
system = """
<agent_charter>
You are a read-only memory QA agent...
</agent_charter>

<behavior_policy>
Use the retrieved memory block before relying on general knowledge...
</behavior_policy>

<retrieved_memory source="OpenViking" top_k="30" score_threshold="0.1">
viking://user/default/memories/preferences/Gina/dance_styles.md
score: 0.593
Gina loves contemporary dance, describing it as expressive and graceful. She uses dance for stress relief...

viking://user/default/memories/entities/passion/dance.md
score: 0.587
Gina loves dance and uses it as stress relief...

[... 28 more memories ...]
</retrieved_memory>
"""

# 构建 messages
model_messages = [
    {"role": "system", "content": system},
    {"role": "user", "content": "How do Jon and Gina both like to destress?"}
]
```

#### 4. 调用 LLM

```python
response = call_openai_chat(
    messages=model_messages,
    model="gpt-5.5",
    temperature=0.2
)

# LLM 回答
answer = "Jon and Gina both like to destress through dance. Gina uses dance for stress relief, and Jon says dance has been his stress-buster since childhood."
```

#### 5. 返回结果 + Context Trace

```python
return {
    "answer": answer,
    "tokens": {
        "prompt": 1344,
        "completion": 35,
        "total": 1379
    },
    "retrieval": {
        "items": [...30 items...],
        "query_plan": ["How do Jon and Gina both like to destress?"],
        "errors": []
    },
    "context_trace": {
        "phase": "openviking-readonly-context-v1",
        "blocks": [
            {
                "index": 1,
                "role": "system",
                "title": "Agent 章程",
                "content": "<agent_charter>...</agent_charter>",
                "char_count": 250,
                "tokens_est": 65
            },
            {
                "index": 3,
                "role": "system",
                "title": "Retrieved Memory",
                "content": "<retrieved_memory>...</retrieved_memory>",
                "char_count": 5000,
                "tokens_est": 1250
            },
            ...
        ],
        "layers": [
            {
                "name": "Agent Charter",
                "item_count": 2,
                "char_count": 500
            },
            {
                "name": "Retrieved Memory",
                "item_count": 30,
                "char_count": 5000,
                "highlight": true
            }
        ],
        "prompt_tokens_est": 1344,
        "memory_hits": 30,
        "memory_file_hits": 15,
        "alignment_notes": [
            "所有回答先读取 OpenViking search/find 结果。",
            "如果 workspace 可解析，top hits 会读取本地 memory 文件全文。",
            "当前 Web Agent 是一次性检索+回答，不是完整多轮工具调用 agent。"
        ]
    },
    "isolation": {
        "account": "default",
        "user_id": "default",
        "agent_id": "default",
        "memory_write": "disabled"
    }
}
```

#### 6. 前端显示

```
✅ 回答显示在对话框

📊 右侧面板显示:

【上下文预览】
- Context: openviking-readonly-context-v1
- Blocks: 5
- Memory Hits: 30
- Tokens Est.: 1344

【组装后的模型上下文】
#1 system · Agent 章程
  <agent_charter>...</agent_charter>
  250 chars · 65 tokens est

#2 system · 行为规则
  <behavior_policy>...</behavior_policy>
  250 chars · 65 tokens est

#3 system · Retrieved Memory (高亮)
  <retrieved_memory>...</retrieved_memory>
  5000 chars · 1250 tokens est

#4 history · 最近对话
  <recent_conversation>...</recent_conversation>
  0 chars · 0 tokens est

#5 user · 当前请求
  <current_request>...</current_request>
  50 chars · 15 tokens est

【Relevant Memory】
Memory 1 (score: 0.593)
  viking://user/default/memories/preferences/Gina/dance_styles.md
  Gina loves contemporary dance...

Memory 2 (score: 0.587)
  viking://user/default/memories/entities/passion/dance.md
  Gina loves dance and uses it as stress relief...

[... 28 more memories ...]
```

---

## 🎯 关键特性

### 1. **结构化上下文**
- 使用 XML 标签明确标识不同部分
- `<agent_charter>`, `<behavior_policy>`, `<retrieved_memory>` 等
- 让 LLM 清楚知道每部分的作用

### 2. **元数据注入**
- 在 `<retrieved_memory>` 标签中注入配置信息
- `account`, `user_id`, `agent_id`, `top_k`, `score_threshold`
- 让 LLM 知道检索的参数

### 3. **完整的可观测性**
- `context_trace` 记录完整的上下文构建过程
- 前端可视化每个上下文块
- 显示 token 估算、字符数、记忆命中数

### 4. **分层架构**
- **Layers**: 逻辑层（Agent Charter, Retrieved Memory, Recent Conversation）
- **Blocks**: 物理块（5 个具体的上下文块）
- 便于理解和调试

### 5. **对齐说明 (Alignment Notes)**
```python
"alignment_notes": [
    "所有回答先读取 OpenViking search/find 结果。",
    "如果 workspace 可解析，top hits 会读取本地 memory 文件全文，避免只看 API abstract。",
    "当前 Web Agent 是一次性检索+回答，不是完整多轮工具调用 agent。"
]
```
- 明确说明系统的工作方式
- 帮助用户理解系统的限制

---

## 🔄 与我们评测脚本的对比

| 特性 | 你的 Web 系统 | 我们的评测脚本 |
|------|-------------|--------------|
| **架构** | Web UI + Python Server | 命令行脚本 |
| **上下文结构** | 5 个结构化块 (XML 标签) | 简单的 System + User Prompt |
| **可观测性** | ✅ 完整的 context_trace | ❌ 无可视化 |
| **记忆显示** | ✅ 显示 URI + score + 内容 | ✅ 显示 score + 内容 |
| **对话历史** | ✅ 支持多轮对话 | ❌ 单次问答 |
| **配置灵活性** | ✅ Web UI 可调整 | ✅ 命令行参数 |
| **适用场景** | 交互式测试 + 调试 | 批量评测 |

---

## 💡 可以借鉴的地方

### 1. **结构化 Prompt**
你的系统使用 XML 标签清晰分隔不同部分，我们可以借鉴：

```python
system = f"""
<agent_charter>
You are an AI assistant with access to OpenViking memory database.
</agent_charter>

<behavior_policy>
Use the retrieved memory block before relying on general knowledge.
If the retrieved memory is insufficient, say what is uncertain.
Prefer specific dates, people, places when they appear in memory.
</behavior_policy>

<retrieved_memory source="OpenViking" top_k="{top_k}" score_threshold="0.1">
{evidence}
</retrieved_memory>

<examples>
Q: When did Jon lose his job?
Memories: 'Jon lost his job as a banker on 2023-01-19'
A: 2023-01-19
</examples>
"""
```

### 2. **元数据注入**
在 prompt 中注入配置信息，让 LLM 知道检索参数：

```xml
<retrieved_memory 
    source="OpenViking" 
    top_k="30" 
    score_threshold="0.1"
    memory_hits="30">
```

### 3. **对齐说明**
明确告诉 LLM 系统的工作方式和限制：

```python
alignment_notes = """
Note: This is a one-shot retrieval system. The memories above are all the evidence available.
If the answer is not in the memories, respond 'unknown'.
"""
```

---

## 🚀 建议改进

### 对你的系统：

1. **添加查询改写**
   - 当前是单次检索
   - 可以生成多个查询变体，合并结果

2. **添加 Few-shot Examples**
   - 当前没有示例
   - 可以在 behavior_policy 后添加

3. **支持 Agent 模式**
   - 当前是一次性检索+回答
   - 可以支持多轮工具调用

### 对我们的评测脚本：

1. **借鉴结构化 Prompt**
   - 使用 XML 标签
   - 添加元数据注入

2. **添加 context_trace**
   - 记录上下文构建过程
   - 便于调试和分析

3. **生成可视化报告**
   - 在 HTML 报告中显示上下文信息
   - 类似你的 Web UI

---

## 📊 总结

你的系统有一个**非常完善的上下文管理实现**：

✅ **结构化**: 5 个清晰的上下文块
✅ **可观测**: 完整的 context_trace
✅ **可视化**: 前端实时显示上下文
✅ **灵活**: Web UI 可调整参数
✅ **专业**: XML 标签 + 元数据注入

这是一个**生产级别的实现**，非常适合：
- 交互式测试和调试
- 上下文工程实验
- 记忆质量分析
- 系统演示

我们的评测脚本可以借鉴很多设计理念！
