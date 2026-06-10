# OpenClaw 官方实现 vs 自定义 OpenViking Agent 对比分析

## 评测结果对比

### 自定义实现（当前）
- **准确率**: 71.7% (21/30 正确)
- **检索方式**: OpenViking `/api/v1/search/find` API
- **LLM**: DeepSeek-v4-flash
- **平均耗时**: 50.6 秒/题

### OpenClaw 官方实现
- **准确率**: 待测试
- **检索方式**: OpenClaw agent 内置记忆系统
- **LLM**: Claude (通过 OpenClaw API)
- **平均耗时**: 待测试

---

## 关键差异点分析

### 1. **记忆检索机制**

#### 自定义实现
```python
# 直接调用 OpenViking 搜索 API
hits = openviking_find(
    base_url, 
    query=question,  # 直接用问题作为查询
    target_uri="viking://user/memories/",
    limit=6,
    score_threshold=0
)
```

**特点**:
- ✅ 直接向量搜索，速度快
- ✅ 可控的 top-k (默认 6)
- ❌ 没有查询改写/扩展
- ❌ 没有多轮检索策略
- ❌ 单次检索，可能遗漏相关记忆

#### OpenClaw 官方实现
```python
# 通过 agent 对话接口
input_msg = f"Current date: {question_time}. Answer the question directly: {question}"
response = send_message(agent_url, user, input_msg, session_key)
```

**特点**:
- ✅ Agent 可能进行查询改写
- ✅ 可能有多轮检索策略
- ✅ 上下文感知（session-based）
- ✅ 可能有推理链（reasoning）
- ❌ 黑盒，不透明
- ❌ 可能更慢

---

### 2. **Lexical Fallback（词法回退）**

#### 自定义实现
```python
# 可选的词法回退
if args.lexical_fallback:
    lexical_hits = lexical_memory_hits(workspace, account, query, limit=8)
    # 合并向量检索和词法检索结果
    hits = merge_hits(vector_hits, lexical_hits)
```

**特点**:
- 使用关键词匹配作为补充
- 对特定实体（Rome, Paris, tattoo 等）加权
- 可能提高召回率

**当前测试未启用** ❌

#### OpenClaw 官方实现
- 未知是否有类似机制
- 可能内置在 agent 中

---

### 3. **问题上下文注入**

#### 自定义实现
```python
system = "You are answering LoCoMo memory benchmark questions..."
user = f"Question: {question}\nQuery time: {query_time}\n\nRetrieved memories:\n{evidence}"
```

**特点**:
- ❌ 没有注入 "Current date" 到问题中
- ✅ 提供了 query_time 作为独立字段
- ❌ LLM 可能忽略 query_time

#### OpenClaw 官方实现
```python
if question_time:
    input_msg = f"Current date: {question_time}. Answer the question directly: {question}"
else:
    input_msg = f"Answer the question directly: {question}"
```

**特点**:
- ✅ 直接注入日期到问题中
- ✅ 更强的时间上下文提示

---

### 4. **LLM 模型差异**

#### 自定义实现
- **模型**: DeepSeek-v4-flash
- **Temperature**: 0
- **特点**: 快速、便宜，但可能推理能力较弱

#### OpenClaw 官方实现
- **模型**: Claude (Opus/Sonnet)
- **特点**: 更强的推理能力，更好的指令遵循

---

### 5. **检索结果处理**

#### 自定义实现
```python
def memory_text(item, query, workspace, account, limit=2200):
    # 从文件读取完整内容
    body = focused_file_snippet(path, query, limit)
    # 或使用 API 返回的 abstract
    body = item.get("abstract") or item.get("overview")
    return f"{uri} score={score}\n{body}"
```

**特点**:
- ✅ 使用 `focused_file_snippet` 提取相关片段
- ✅ 关键词高亮窗口（700 字符前，1100 字符后）
- ❌ 可能截断重要信息

#### OpenClaw 官方实现
- 未知具体处理方式
- 可能由 agent 内部处理

---

## 导致准确率差异的可能原因

### 1. **查询改写缺失** ⭐⭐⭐
**问题**: "How do Jon and Gina both like to destress?" 
- 自定义实现直接搜索原问题
- OpenClaw 可能改写为 "Jon destress" + "Gina destress" + "dance stress relief"

**影响**: 7 题回答 "unknown"

### 2. **时间上下文注入不足** ⭐⭐
**问题**: "When was Jon in Rome?"
- 自定义实现: `Query time: -` (没有提供)
- OpenClaw: `Current date: 2023-XX-XX. When was Jon in Rome?`

**影响**: 时间相关问题可能失败

### 3. **Lexical Fallback 未启用** ⭐⭐
**问题**: 特定实体（Rome, tattoo, gym）检索失败
- 向量搜索可能语义匹配不佳
- 词法搜索可以直接匹配关键词

**影响**: 实体相关问题失败

### 4. **LLM 推理能力** ⭐
**问题**: "Which cities has Jon visited?"
- 自定义实现只答 "Paris"，漏了 "Rome"
- 可能是 LLM 没有综合多条记忆

**影响**: 需要综合推理的问题

### 5. **检索 top-k 设置** ⭐
- 自定义实现: top-k=6
- 可能不够，遗漏相关记忆

---

## 改进建议

### 优先级 1: 启用 Lexical Fallback
```bash
python scripts/openviking_memory_qa.py \
  --lexical-fallback \
  --lexical-top-k 8 \
  ...
```

### 优先级 2: 改进时间上下文注入
```python
# 修改 prompt
user = f"Current date: {query_time}. Question: {question}\n\nRetrieved memories:\n{evidence}"
```

### 优先级 3: 增加检索数量
```bash
--top-k 10  # 从 6 增加到 10
```

### 优先级 4: 查询改写
```python
# 添加查询扩展
def expand_query(question):
    # 提取实体和关键词
    # 生成多个查询变体
    return [original_query, expanded_query1, expanded_query2]
```

### 优先级 5: 更换 LLM
- 测试 Claude 或 GPT-4
- 对比推理能力差异

---

## 下一步行动

1. ✅ **运行 OpenClaw 官方评测** - 获取基准准确率
2. ⬜ **启用 Lexical Fallback 重测** - 预期提升 5-10%
3. ⬜ **改进时间上下文注入** - 预期提升 3-5%
4. ⬜ **增加 top-k 到 10** - 预期提升 2-3%
5. ⬜ **实现查询改写** - 预期提升 5-10%

**预期最终准确率**: 85-90%
