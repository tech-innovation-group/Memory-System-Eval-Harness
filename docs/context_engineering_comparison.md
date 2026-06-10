# 当前上下文工程实现详解

## 📊 系统对比

| 维度 | Web 系统 (server.py) | 评测脚本 (openviking_memory_qa.py) |
|------|---------------------|----------------------------------|
| **架构** | 5 个结构化块 (XML标签) | 2 部分 (System + User) |
| **System Prompt** | 3 个块 (Charter + Policy + Memory) | 1 个块 (说明 + 示例) |
| **记忆格式** | XML `<retrieved_memory>` | Markdown `**Evidence N**` |
| **时间注入** | 注入到问题中 | 注入到问题中 ✅ 相同 |
| **Few-shot** | ❌ 无 | ✅ 有 3 个示例 |
| **元数据** | ✅ XML 属性 | ⚠️ 部分（在 evidence header） |
| **上下文追踪** | ✅ context_trace | ❌ 无 |
| **可观测性** | ✅ Web UI 可视化 | ⚠️ 只有 CSV 结果 |

---

## 🎯 当前评测脚本的上下文工程

### 1. System Prompt 结构

```python
system = """
# Memory-based Question Answering

You are an AI assistant with access to OpenViking memory database.
The memories below are search results from the database for the user's question.

Instructions:
1. Carefully read all retrieved memories
2. The memories are ranked by relevance score
3. Extract relevant facts to answer the question
4. Synthesize information from multiple memories if needed
5. Answer concisely with specific dates/facts when available
6. If memories don't contain sufficient information, respond 'unknown'

Important:
- Do not invent information not present in the memories
- Use exact dates and facts from the memories
- Combine information from multiple memories when necessary

Examples:

Q: When did Jon lose his job?
Memories: 'Jon lost his job as a banker on 2023-01-19'
A: 2023-01-19

Q: What cities has Jon visited?
Memories: 'Jon was in Paris on 2023-01-28' + 'Jon visited Rome in June 2023'
A: Paris, Rome

Q: What is Jon's favorite color?
Memories: 'Jon loves contemporary dance'
A: unknown
"""
```

**特点**:
- ✅ 清晰的角色定义
- ✅ 6 步指令
- ✅ 3 个 Few-shot 示例
- ✅ 强调约束（不要编造）
- ⚠️ 没有 XML 标签结构化
- ⚠️ 没有元数据注入

### 2. Evidence 格式化

```python
evidence_parts = []
if hits:
    evidence_parts.append(f"### Retrieved memories (top {len(hits)} results, sorted by relevance):\n")
    for i, item in enumerate(hits, 1):
        score = item.get('score', 0)
        text = memory_text(item, job.question, args.workspace, args.account)
        source = item.get("source") or item.get("content_source") or "openviking_memory"
        evidence_parts.append(f"**Evidence {i}** ({source}, score: {score:.3f}):\n{text}\n")
    evidence = "\n".join(evidence_parts)
else:
    evidence = "(no memories found)"
```

**输出示例**:
```markdown
### Retrieved memories (top 30 results, sorted by relevance):

**Evidence 1** (openviking_memory, score: 0.649):
time: 2023-01-20 (Friday)
[user]: [Jon] D1:2: Hey Gina! Good to see you too. Lost my job as a banker yesterday...

**Evidence 2** (openviking_memory, score: 0.630):
# Jon
- Lost his job as a banker on 2023-01-19.
- Opened his own dance studio business by April 2023...

**Evidence 3** (archive_fallback, score: 0.587):
2023-01-20 user: Hey Jon! Good to see you...
```

**特点**:
- ✅ 显示 score（相似度分数）
- ✅ 显示 source（来源）
- ✅ Markdown 格式，易读
- ⚠️ 没有 XML 结构
- ⚠️ 元数据不够丰富（缺少 top_k, threshold 等）

### 3. User Prompt 结构

```python
# 时间注入
if job.query_time and job.query_time != '-':
    question_with_context = f"Current date: {job.query_time}. {job.question}"
else:
    question_with_context = job.question

user = f"""Question: {question_with_context}

{evidence}

Based on the memories above, provide your answer:"""
```

**输出示例**:
```
Question: When did Jon lose his job?

### Retrieved memories (top 30 results, sorted by relevance):

**Evidence 1** (openviking_memory, score: 0.649):
...

Based on the memories above, provide your answer:
```

**特点**:
- ✅ 时间注入到问题中
- ✅ 清晰的引导语
- ⚠️ 简单的文本格式

---

## 🎨 Web 系统的上下文工程（作为对比）

### 1. System Prompt 结构

```python
system = """
<agent_charter>
You are a read-only memory QA agent inside the LoCoMo/OpenViking evaluation harness.
Your job is to answer the current user request using retrieved long-term memory as the primary evidence.
Give a direct answer first, then briefly explain the evidence when useful.
Never claim that you wrote, committed, or updated memory during this chat.
</agent_charter>

<behavior_policy>
Use the retrieved memory block before relying on general knowledge.
If the retrieved memory is insufficient or conflicting, say what is uncertain instead of inventing facts.
Prefer specific dates, people, places, and causal links when they appear in memory.
Keep the answer concise and in the same language as the user's request when practical.
</behavior_policy>

<retrieved_memory source="OpenViking" account="default" user="default" agent="default" top_k="30" score_threshold="0.1">
viking://user/default/memories/events/2023/01/20/lost_job_banker.md
score: 0.593
Gina loves contemporary dance, describing it as expressive and graceful...

viking://user/default/memories/entities/passion/dance.md
score: 0.587
Gina loves dance and uses it as stress relief...
</retrieved_memory>
"""
```

**特点**:
- ✅ XML 标签结构化
- ✅ 元数据注入（account, top_k, threshold）
- ✅ 分离角色定义和行为规则
- ❌ 没有 Few-shot 示例

---

## 🔄 两种方案的完整对比

### 评测脚本方案

```python
messages = [
    {
        "role": "system",
        "content": """
# Memory-based Question Answering

You are an AI assistant with access to OpenViking memory database.
...
Examples:
Q: When did Jon lose his job?
A: 2023-01-19
...
"""
    },
    {
        "role": "user",
        "content": """
Question: When did Jon lose his job?

### Retrieved memories (top 30 results, sorted by relevance):

**Evidence 1** (openviking_memory, score: 0.649):
time: 2023-01-20
[user]: [Jon] D1:2: Lost my job as a banker yesterday...

Based on the memories above, provide your answer:
"""
    }
]
```

### Web 系统方案

```python
messages = [
    {
        "role": "system",
        "content": """
<agent_charter>
You are a read-only memory QA agent...
</agent_charter>

<behavior_policy>
Use the retrieved memory block before relying on general knowledge...
</behavior_policy>

<retrieved_memory source="OpenViking" top_k="30" score_threshold="0.1">
viking://user/.../lost_job_banker.md
score: 0.593
...
</retrieved_memory>
"""
    },
    {
        "role": "user",
        "content": "When did Jon lose his job?"
    }
]
```

---

## 📈 优缺点分析

### 评测脚本的优势

1. **✅ 有 Few-shot Examples**
   - 3 个具体示例
   - 教 LLM 如何回答
   - 明确 "unknown" 的使用场景

2. **✅ Markdown 格式易读**
   - 清晰的标题层级
   - 代码块突出
   - 人类和 LLM 都易读

3. **✅ 显示 score 和 source**
   - 帮助 LLM 判断可信度
   - 区分不同来源（openviking vs archive_fallback）

### 评测脚本的不足

1. **❌ 缺少结构化标签**
   - 没有 XML 标签分隔不同部分
   - LLM 可能混淆指令和数据

2. **❌ 元数据不够丰富**
   - 没有注入 top_k, threshold 等参数
   - LLM 不知道检索配置

3. **❌ 没有上下文追踪**
   - 无法观察 LLM 看到的完整上下文
   - 难以调试

### Web 系统的优势

1. **✅ XML 标签结构化**
   - 清晰分隔不同部分
   - LLM 更容易理解结构

2. **✅ 元数据注入**
   - 显示检索参数
   - LLM 知道数据来源和配置

3. **✅ 完整的可观测性**
   - context_trace 记录所有信息
   - Web UI 实时可视化

### Web 系统的不足

1. **❌ 缺少 Few-shot Examples**
   - 没有具体示例
   - LLM 需要自己理解任务

---

## 🚀 改进建议

### 对评测脚本

#### 改进 1: 添加 XML 标签结构化

```python
system = """
<role>
You are an AI assistant with access to OpenViking memory database.
</role>

<instructions>
1. Carefully read all retrieved memories
2. The memories are ranked by relevance score
3. Extract relevant facts to answer the question
4. Synthesize information from multiple memories if needed
5. Answer concisely with specific dates/facts when available
6. If memories don't contain sufficient information, respond 'unknown'
</instructions>

<constraints>
- Do not invent information not present in the memories
- Use exact dates and facts from the memories
- Combine information from multiple memories when necessary
</constraints>

<examples>
Q: When did Jon lose his job?
Memories: 'Jon lost his job as a banker on 2023-01-19'
A: 2023-01-19

Q: What is Jon's favorite color?
Memories: 'Jon loves contemporary dance'
A: unknown
</examples>
"""
```

#### 改进 2: 增强元数据注入

```python
evidence_parts = []
if hits:
    # Header with metadata
    evidence_parts.append(
        f"<retrieved_memories "
        f"source=\"OpenViking\" "
        f"account=\"{args.account}\" "
        f"top_k=\"{args.top_k}\" "
        f"score_threshold=\"0.1\" "
        f"total_hits=\"{len(hits)}\">\n"
    )
    
    for i, item in enumerate(hits, 1):
        score = item.get('score', 0)
        text = memory_text(item, job.question, args.workspace, args.account)
        source = item.get("source") or "openviking_memory"
        uri = item.get("uri", "")
        
        evidence_parts.append(
            f"<memory index=\"{i}\" source=\"{source}\" score=\"{score:.3f}\" uri=\"{uri}\">\n"
            f"{text}\n"
            f"</memory>\n"
        )
    
    evidence_parts.append("</retrieved_memories>")
    evidence = "\n".join(evidence_parts)
```

输出示例：
```xml
<retrieved_memories source="OpenViking" account="default" top_k="30" score_threshold="0.1" total_hits="30">

<memory index="1" source="openviking_memory" score="0.649" uri="viking://user/.../lost_job_banker.md">
time: 2023-01-20 (Friday)
[user]: [Jon] D1:2: Lost my job as a banker yesterday...
</memory>

<memory index="2" source="openviking_memory" score="0.630" uri="viking://user/.../jon.md">
# Jon
- Lost his job as a banker on 2023-01-19.
- Opened his own dance studio business by April 2023...
</memory>

</retrieved_memories>
```

#### 改进 3: 添加上下文追踪

```python
def answer_question(args, job):
    # ... 检索记忆 ...
    
    # 构建上下文
    system = build_system_prompt()
    user = build_user_prompt(question_with_context, evidence)
    
    # 上下文追踪
    context_trace = {
        "phase": "memory_qa_v1",
        "system_prompt_chars": len(system),
        "user_prompt_chars": len(user),
        "total_chars": len(system) + len(user),
        "estimated_tokens": token_estimate(system + user),
        "memory_hits": len(hits),
        "memory_sources": {
            "openviking": sum(1 for h in hits if h.get("source") != "archive_fallback"),
            "archive_fallback": sum(1 for h in hits if h.get("source") == "archive_fallback"),
        },
        "query_plan": query_plan,
        "retrieval_error": retrieval_error,
    }
    
    # 保存到结果中
    result["context_trace"] = json.dumps(context_trace, ensure_ascii=False)
```

---

## 📊 最佳实践总结

### 结合两种方案的优点

```python
system = """
<role>
You are an AI assistant with access to OpenViking memory database.
The memories below are search results from the database for the user's question.
</role>

<instructions>
1. Carefully read all retrieved memories
2. The memories are ranked by relevance score
3. Extract relevant facts to answer the question
4. Synthesize information from multiple memories if needed
5. Answer concisely with specific dates/facts when available
6. If memories don't contain sufficient information, respond 'unknown'
</instructions>

<constraints>
- Do not invent information not present in the memories
- Use exact dates and facts from the memories
- Combine information from multiple memories when necessary
</constraints>

<examples>
Q: When did Jon lose his job?
Memories: 'Jon lost his job as a banker on 2023-01-19'
A: 2023-01-19

Q: What cities has Jon visited?
Memories: 'Jon was in Paris on 2023-01-28' + 'Jon visited Rome in June 2023'
A: Paris, Rome

Q: What is Jon's favorite color?
Memories: 'Jon loves contemporary dance'
A: unknown
</examples>
"""

user = f"""
<question time="{job.query_time}">
{question_with_context}
</question>

<retrieved_memories source="OpenViking" account="{args.account}" top_k="{args.top_k}" score_threshold="0.1" total_hits="{len(hits)}">
{format_memories_with_xml(hits)}
</retrieved_memories>

<task>
Based on the memories above, provide your answer:
</task>
"""
```

### 关键原则

1. **✅ 结构化**: 使用 XML 标签清晰分隔不同部分
2. **✅ 元数据**: 注入检索配置和参数
3. **✅ 示例**: 提供 Few-shot examples
4. **✅ 可观测**: 记录完整的 context_trace
5. **✅ 约束**: 明确告诉 LLM 不要编造
6. **✅ 引导**: 清晰的任务描述

---

## 🎯 当前状态总结

### 评测脚本 (openviking_memory_qa.py)

**当前实现**:
- ✅ Few-shot Examples (3 个)
- ✅ 时间注入
- ✅ 显示 score 和 source
- ✅ Markdown 格式
- ⚠️ 没有 XML 结构化
- ⚠️ 元数据不够丰富
- ❌ 没有上下文追踪

**准确率**: 71.7% → 73.3% (Prompt 改进) → 预期 82-87% (VikingBot 启发)

### Web 系统 (server.py)

**当前实现**:
- ✅ XML 标签结构化
- ✅ 元数据注入
- ✅ 完整的 context_trace
- ✅ Web UI 可视化
- ❌ 没有 Few-shot Examples

**用途**: 交互式测试和调试

---

## 💡 下一步

1. **应用改进到评测脚本**
   - 添加 XML 标签
   - 增强元数据
   - 添加上下文追踪

2. **完整评测对比**
   - 基线版 vs 改进版
   - 不同 LLM 对比 (DeepSeek vs GPT-5.5)

3. **生成可视化报告**
   - 在 HTML 报告中显示上下文信息
   - 类似 Web UI 的可视化
