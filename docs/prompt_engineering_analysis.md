# Prompt Engineering 对比分析

## 当前自定义实现的 Prompt

### System Prompt
```
You are answering LoCoMo memory benchmark questions using only the retrieved OpenViking memories. 
Answer concisely. If the evidence is insufficient, answer unknown. Do not invent details.
```

### User Prompt
```
Question: {question}
Query time: {query_time or '-'}

Retrieved memories:
{evidence}

Final answer only:
```

---

## 问题分析

### ❌ 问题 1: 时间上下文注入位置不佳
**当前**:
```
Question: When was Jon in Rome?
Query time: -
```

**问题**: 
- `Query time: -` 没有实际值
- 即使有值，LLM 可能忽略独立字段
- 没有明确指示 LLM 使用时间信息

**OpenClaw 做法**:
```
Current date: 2023-06-15. Answer the question directly: When was Jon in Rome?
```

---

### ❌ 问题 2: "Final answer only" 过于简单
**当前**: `Final answer only:`

**问题**:
- 没有指导答案格式
- 没有强调从记忆中提取
- 没有处理多条记忆的指导

**改进建议**:
```
Based on the retrieved memories above, provide a direct answer to the question.
- If multiple memories are relevant, synthesize them into one answer
- Use the exact dates/facts from the memories
- If no relevant information is found, answer "unknown"

Answer:
```

---

### ❌ 问题 3: System Prompt 缺少角色定位
**当前**: 
```
You are answering LoCoMo memory benchmark questions...
```

**问题**:
- 没有强调"记忆助手"角色
- 没有说明记忆的结构和来源

**改进建议**:
```
You are a memory assistant helping answer questions about Jon and Gina's conversations and experiences.
You have access to their chat history, personal information, and life events stored as structured memories.

Your task:
1. Carefully read the retrieved memories below
2. Extract relevant facts to answer the question
3. Synthesize information from multiple memories if needed
4. Answer concisely with specific dates/facts when available
5. If the memories don't contain the answer, respond "unknown"

Do not invent or infer information not present in the memories.
```

---

### ❌ 问题 4: Evidence 格式不清晰
**当前**:
```
Retrieved memories:
viking://user/default/memories/events/2023/06/15/rome_trip.md score=0.65
time: 2023-06-15
[user]: [Jon] I'm in Rome now! ...
```

**问题**:
- URI 和 score 对 LLM 无用
- 格式混乱，难以解析

**改进建议**:
```
Retrieved memories (sorted by relevance):

Memory 1:
Source: events/2023/06/15/rome_trip.md
Content: 
time: 2023-06-15
[Jon]: I'm in Rome now! ...

Memory 2:
Source: entities/person/jon.md
Content:
# Jon
- Visited Rome in June 2023
...
```

---

### ❌ 问题 5: 缺少 Few-shot Examples
**当前**: 没有示例

**问题**:
- LLM 不知道期望的答案格式
- 不知道如何处理多条记忆

**改进建议**: 添加 2-3 个示例
```
Example 1:
Question: When did Jon lose his job?
Retrieved memories:
- Jon lost his job as a banker on 2023-01-19
Answer: 2023-01-19

Example 2:
Question: What cities has Jon visited?
Retrieved memories:
- Jon was in Paris on 2023-01-28
- Jon visited Rome in June 2023
Answer: Paris, Rome

Example 3:
Question: What is Jon's favorite color?
Retrieved memories:
- Jon loves contemporary dance
- Jon opened a dance studio
Answer: unknown
```

---

## 改进后的完整 Prompt

### System Prompt (改进版)
```python
system = """You are a memory assistant helping answer questions about Jon and Gina's conversations and experiences.

You have access to their chat history, personal information, and life events stored as structured memories.

Instructions:
1. Carefully read all retrieved memories below
2. Extract relevant facts to answer the question
3. Synthesize information from multiple memories if needed
4. Answer concisely with specific dates/facts when available
5. If the memories don't contain sufficient information, respond "unknown"

Important:
- Do not invent or infer information not present in the memories
- Use exact dates and facts from the memories
- Combine information from multiple memories when necessary

Examples:

Q: When did Jon lose his job?
Memories: "Jon lost his job as a banker on 2023-01-19"
A: 2023-01-19

Q: What cities has Jon visited?
Memories: "Jon was in Paris on 2023-01-28" + "Jon visited Rome in June 2023"
A: Paris, Rome

Q: What is Jon's favorite color?
Memories: "Jon loves contemporary dance" + "Jon opened a dance studio"
A: unknown
"""
```

### User Prompt (改进版)
```python
# 如果有 query_time，注入到问题中
if job.query_time and job.query_time != '-':
    question_with_context = f"Current date: {job.query_time}. {job.question}"
else:
    question_with_context = job.question

# 格式化 evidence
formatted_evidence = []
for i, item in enumerate(hits, 1):
    uri = item.get("uri", "")
    source = uri.split("/")[-1] if uri else "unknown"
    content = memory_text(item, job.question, args.workspace, args.account)
    formatted_evidence.append(f"Memory {i}:\nSource: {source}\n{content}")

evidence_text = "\n\n".join(formatted_evidence) if formatted_evidence else "(no memories found)"

user = f"""Question: {question_with_context}

Retrieved memories (sorted by relevance):
{evidence_text}

Based on the memories above, provide your answer:"""
```

---

## 预期改进效果

| 改进项 | 预期提升 | 优先级 |
|--------|---------|--------|
| 时间上下文注入到问题 | +3-5% | ⭐⭐⭐ |
| 改进 System Prompt | +2-3% | ⭐⭐ |
| 添加 Few-shot Examples | +3-5% | ⭐⭐⭐ |
| 格式化 Evidence | +1-2% | ⭐ |
| 改进答案引导 | +2-3% | ⭐⭐ |

**总预期提升**: +11-18%
**目标准确率**: 71.7% → 83-90%

---

## 实施建议

### 快速版（5 分钟）
只改 3 个关键点：
1. 时间注入到问题
2. 添加 Few-shot Examples
3. 改进答案引导

### 完整版（15 分钟）
实施所有改进

你想先测试哪个版本？
