# VikingBot 官方上下文工程分析

## VikingBot 的 Prompt 架构

### 1. System Prompt 结构

VikingBot 的 system prompt 由多个部分组成：

```python
async def build_system_prompt(session_key, ov_tools_enable=True, profile_user_list=None):
    parts = []
    
    # 1. 核心身份 (IDENTITY)
    parts.append(await self._get_identity(session_key))
    
    # 2. 沙盒环境信息
    parts.append(f"## Sandbox Environment\n...")
    
    # 3. Bootstrap 文件 (AGENTS.md, SOUL.md, TOOLS.md, IDENTITY.md)
    parts.append(self._load_bootstrap_files())
    
    # 4. Always-loaded Skills
    parts.append(f"# Active Skills\n{always_content}")
    
    # 5. Available Skills Summary
    parts.append(f"# Skills\n{skills_summary}")
    
    # 6. 当前用户的 Profile (从 OpenViking 读取)
    if ov_tools_enable:
        profile = await self.memory.get_viking_user_profile(workspace_id, user_id)
        parts.append(f"## Current user's information\n{profile}")
    
    return "\n\n---\n\n".join(parts)
```

### 2. User Message 结构

```python
async def _build_user_memory(session_key, current_message, sender_id, memory_users, ov_tools_enable=True):
    parts = []
    
    # 1. 当前时间
    parts.append(f"## Current Time: {now} ({tz})")
    
    # 2. Session 上下文
    parts.append(f"## Current Session\nChannel: {session_key.type}")
    
    # 3. OpenViking 记忆检索结果 (核心！)
    if ov_tools_enable:
        viking_memory = await self.memory.get_viking_memory_context(
            current_message=current_message,
            workspace_id=workspace_id,
            sender_id=sender_id,
            user_ids=search_user_ids
        )
        if viking_memory:
            parts.append(f"## openviking_search(query=[user_query])\n{viking_memory}")
    
    # 4. 语言指示
    parts.append("Reply in the same language as the user's query, ignoring the language of the reference materials. User's query:")
    
    return "\n\n---\n\n".join(parts)
```

### 3. 核心身份 Prompt

```python
def _get_identity(session_key):
    return f"""# vikingbot 🐈

You are VikingBot, an AI assistant built based on the OpenViking context database.
When acquiring information, data, and knowledge, you **prioritize using openviking tools to read and search OpenViking (a context database) above all other sources**.

You have access to tools that allow you to:
- Read, search, and grep OpenViking files
- Read, write, and edit local files
- Execute shell commands
- Search the web and fetch web pages
- Send messages to users on chat channels
- Spawn subagents for complex background tasks

## Runtime
{runtime}

## Workspace
You have two workspaces:
1. Local workspace: {workspace_display}
2. OpenViking workspace: managed via OpenViking tools
- Custom skills: {workspace_display}/skills/{{skill-name}}/SKILL.md

IMPORTANT:
- When responding to direct questions or conversations, reply directly with your text response.
- Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).For normal conversation, just respond with text - do not call the message tool.
- Always be helpful, accurate, and concise. When using tools, think step by step: what you know, what you need, and why you chose this tool.

## Memory
- Remember important facts: using openviking_memory_commit tool to commit"""
```

### 4. 记忆检索实现

```python
async def get_viking_memory_context(current_message, workspace_id, sender_id, user_ids=None):
    # 1. 调用 OpenViking 搜索 API
    result = await client.search_memory(
        query=current_message,
        user_ids=search_user_ids,
        agent_user_id=admin_user_id,
        limit=30  # ⭐ 注意：limit=30，远大于我们的 6
    )
    
    # 2. 解析结果，分为 user_memory 和 agent_memory
    user_memory = await self._parse_viking_memory(
        result["user_memory"],
        client,
        min_score=0.1,  # ⭐ 有 score 过滤
        max_chars=4000  # ⭐ 有字符数限制
    )
    agent_memory = await self._parse_viking_memory(
        result["agent_memory"],
        client,
        min_score=0.1,
        max_chars=2000
    )
    
    # 3. 返回格式化的记忆
    return f"### user memories:\n{user_memory}\n### agent memories:\n{agent_memory}"
```

### 5. 评测时的 Prompt 注入

```python
# 在 run_eval.py 中
def run_vikingbot_chat(question, question_time=None, sample_id=None, question_id=None, memory_users=None):
    # 如果有 question_time，注入到 prompt 中
    if question_time:
        input = f"Current date: {question_time}. Answer the question directly: {question}"
    else:
        input = f"Answer the question directly: {question}"
    
    # 执行 vikingbot chat 命令
    cmd = ["vikingbot", "chat", "-m", input, "-e"]
    if sample_id:
        cmd.extend(["--sender", sample_id, "--session", question_id])
    if memory_users:
        for user in memory_users:
            cmd.extend(["--memory-user", user])
```

---

## 关键差异对比

| 特性 | VikingBot 官方 | 自定义实现 | 影响 |
|------|---------------|-----------|------|
| **检索 limit** | 30 | 6 | ⭐⭐⭐ 官方检索更多记忆 |
| **Score 过滤** | min_score=0.1 | score_threshold=0 | ⭐⭐ 官方过滤低质量结果 |
| **字符数限制** | user:4000, agent:2000 | 无限制 | ⭐ 官方控制 context 长度 |
| **记忆分类** | user_memory + agent_memory | 只有 user_memory | ⭐⭐ 官方区分用户和 agent 记忆 |
| **System Prompt** | 复杂多层结构 | 简单单层 | ⭐⭐ 官方更详细的角色定位 |
| **User Profile** | 自动加载用户 profile | 无 | ⭐⭐ 官方有用户背景信息 |
| **时间注入** | 同样方式 | 同样方式 | ✅ 相同 |
| **Few-shot Examples** | 无 | 有（我们添加的） | ⭐ 我们有优势 |
| **工具调用** | Agent 可以调用工具 | 纯 RAG | ⭐⭐⭐ 官方可以多轮推理 |

---

## 最关键的差异：工具调用能力 ⭐⭐⭐

VikingBot 是一个 **Agent**，不是纯 RAG 系统！

### VikingBot 的工作流程：
```
用户问题 
  → Agent 收到问题
  → Agent 可以调用 openviking_search 工具
  → Agent 可以多次调用，改写查询
  → Agent 可以调用其他工具（read_file, grep 等）
  → Agent 综合所有信息回答
```

### 我们的工作流程：
```
用户问题
  → 直接调用 OpenViking API 一次
  → 将结果喂给 LLM
  → LLM 直接回答（无法再次检索）
```

**这是最大的差异！**

---

## VikingBot 的上下文工程优势

### 1. **分层的 System Prompt** ⭐⭐
```
# vikingbot 🐈
You are VikingBot, an AI assistant built based on the OpenViking context database.
When acquiring information, data, and knowledge, you **prioritize using openviking tools to read and search OpenViking (a context database) above all other sources**.
```

**优势**:
- 明确角色定位（基于 OpenViking 的助手）
- 强调优先使用 OpenViking 工具
- 列出所有可用工具

### 2. **动态记忆注入** ⭐⭐⭐
```python
# 在每次对话时动态检索
viking_memory = await self.memory.get_viking_memory_context(
    current_message=current_message,
    workspace_id=workspace_id,
    sender_id=sender_id,
    user_ids=search_user_ids
)
parts.append(f"## openviking_search(query=[user_query])\n{viking_memory}")
```

**优势**:
- 记忆是动态检索的，不是静态的
- 使用当前问题作为查询
- 格式化为 "openviking_search(query=[user_query])" 让 Agent 知道这是检索结果

### 3. **用户 Profile 预加载** ⭐⭐
```python
profile = await self.memory.get_viking_user_profile(workspace_id, user_id)
parts.append(f"## Current user's information\n{profile}")
```

**优势**:
- Agent 知道用户的背景信息
- 可以提供个性化回答

### 4. **记忆分类** ⭐⭐
```python
return f"### user memories:\n{user_memory}\n### agent memories:\n{agent_memory}"
```

**优势**:
- 区分用户记忆和 agent 记忆
- Agent 可以理解记忆的来源

### 5. **语言指示** ⭐
```python
parts.append("Reply in the same language as the user's query, ignoring the language of the reference materials. User's query:")
```

**优势**:
- 明确指示用同样语言回答
- 避免被参考材料的语言影响

---

## 我们可以借鉴的改进

### 优先级 1: 增加检索数量 ⭐⭐⭐
```python
# 当前: top-k=6
# 改为: top-k=30 (与官方一致)
--top-k 30
```

### 优先级 2: 添加 Score 过滤 ⭐⭐
```python
# 当前: score_threshold=0
# 改为: score_threshold=0.1 (过滤低质量结果)
payload = {
    "query": query,
    "target_uri": "viking://user/memories/",
    "limit": 30,
    "score_threshold": 0.1,  # ⭐ 添加这个
}
```

### 优先级 3: 改进 System Prompt ⭐⭐
```python
system = """You are an AI assistant with access to OpenViking memory database.

When answering questions:
1. Carefully read all retrieved memories below
2. The memories are search results from OpenViking database for the user's question
3. Extract relevant facts and synthesize information from multiple memories
4. Answer concisely with specific dates/facts when available
5. If memories don't contain sufficient information, respond "unknown"

Important:
- Do not invent information not present in the memories
- Use exact dates and facts from the memories
- Combine information from multiple memories when necessary

Examples:
[保留现有的 Few-shot Examples]
"""
```

### 优先级 4: 添加记忆格式化 ⭐
```python
# 当前格式
evidence = "\n\n".join(memory_text(item, job.question, args.workspace, args.account) for item in hits)

# 改进格式
evidence_parts = []
evidence_parts.append(f"### Retrieved memories (top {len(hits)} results):")
for i, item in enumerate(hits, 1):
    score = item.get('score', 0)
    text = memory_text(item, job.question, args.workspace, args.account)
    evidence_parts.append(f"\n**Memory {i}** (relevance: {score:.2f}):\n{text}")
evidence = "\n".join(evidence_parts)
```

### 优先级 5: 添加字符数限制 ⭐
```python
def memory_text(item, query, workspace, account, limit=2200):
    # 当前已经有 limit=2200
    # 但可以添加总字符数限制
    pass

# 在 answer_question 中
MAX_EVIDENCE_CHARS = 6000  # user:4000 + agent:2000
if len(evidence) > MAX_EVIDENCE_CHARS:
    evidence = evidence[:MAX_EVIDENCE_CHARS] + "\n...[truncated]"
```

---

## 实施建议

### 快速改进（10 分钟）
1. ✅ 增加 top-k 到 30
2. ✅ 添加 score_threshold=0.1
3. ✅ 改进 System Prompt（借鉴官方）

### 中期改进（30 分钟）
4. ⬜ 添加记忆格式化
5. ⬜ 添加字符数限制
6. ⬜ 启用 Lexical Fallback

### 长期改进（需要重构）
7. ⬜ 实现 Agent 架构（支持工具调用和多轮推理）
8. ⬜ 添加用户 Profile 预加载
9. ⬜ 实现记忆分类（user_memory vs agent_memory）

---

## 预期效果

| 改进 | 预期提升 | 实施难度 |
|------|---------|---------|
| top-k: 6→30 | +5-8% | 简单 |
| score_threshold: 0→0.1 | +2-3% | 简单 |
| 改进 System Prompt | +2-3% | 简单 |
| 记忆格式化 | +1-2% | 中等 |
| Lexical Fallback | +5-10% | 中等 |
| Agent 架构 | +10-15% | 困难 |

**快速改进预期**: 73.3% + 9-14% = **82-87%**
**中期改进预期**: 82-87% + 6-12% = **88-99%**

---

## 结论

VikingBot 的上下文工程核心优势：
1. ⭐⭐⭐ **更大的检索量** (30 vs 6)
2. ⭐⭐⭐ **Agent 架构**（可以多轮推理和工具调用）
3. ⭐⭐ **Score 过滤**（提高检索质量）
4. ⭐⭐ **分层 System Prompt**（更清晰的角色定位）
5. ⭐⭐ **用户 Profile 预加载**（个性化回答）

我们可以快速借鉴 1、3、4，预期提升 9-14%。
