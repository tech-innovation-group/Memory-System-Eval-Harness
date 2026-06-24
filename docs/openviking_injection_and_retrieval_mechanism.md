# OpenViking 注入记忆与检索机制

本文基于本地源码梳理 OpenViking 在两件事上的真实机制：

1. 回答前如何检索记忆
2. 检索结果如何被“注入”到模型上下文

这里有一个很重要的边界：

- OpenViking 核心层负责 `search/find`、层级检索、返回结构化结果
- 把结果真正拼进 prompt，通常是插件或 agent 集成层来做，不是 OpenViking 核心统一硬编码成某个固定块

---

## 1. 先说结论

OpenViking 的“记忆注入”不是单一机制，而是两段式：

### 1.1 核心层

核心层提供两种检索接口：

- `find()`：不带当前会话语境的语义检索
- `search()`：带当前会话语境的复杂检索

对应源码：

- `openviking/service/search_service.py:44-118`
- `openviking/storage/viking_fs.py:1240-1493`

核心层的产物是 `FindResult` / `MatchedContext`，不是直接改写 prompt。

### 1.2 集成层

真正把记忆塞进用户消息或系统上下文，一般在插件侧完成。

例如 OpenClaw 插件会：

1. 先调用 OpenViking `find`
2. 选择叶子记忆
3. 读出摘要或全文
4. 组装成 `<relevant-memories>...</relevant-memories>`
5. 再追加到用户消息

对应源码：

- `examples/openclaw-plugin/auto-recall.ts:149-156`
- `examples/openclaw-plugin/auto-recall.ts:179-255`

所以如果你问“OpenViking 怎么注入记忆”，更准确的回答是：

- OpenViking 核心负责“召回什么”
- 插件/agent 负责“怎么注入”

---

## 2. 检索入口：`find` 和 `search`

### 2.1 `find()`

`find()` 是基础语义检索，不看当前 session 的最近消息，也不做意图分析。

链路：

1. `SearchService.find()` 调到 `VikingFS.find()`
2. `VikingFS.find()` 构造一个 `TypedQuery`
3. 调 `HierarchicalRetriever.retrieve()`
4. 返回 `FindResult`

对应源码：

- `openviking/service/search_service.py:86-118`
- `openviking/storage/viking_fs.py:1240-1341`

### 2.2 `search()`

`search()` 是增强版检索，会把当前会话上下文带进去。

链路：

1. `SearchService.search()` 先从 `session.get_context_for_search(query)` 取上下文
2. 上下文只带两类信息：
   - `latest_archive_overview`
   - `current_messages`
3. `VikingFS.search()` 收到这些上下文后，先做 `IntentAnalyzer.analyze()`
4. 生成一个或多个 `TypedQuery`
5. 并发执行这些查询
6. 聚合成一个 `FindResult`

对应源码：

- `openviking/service/search_service.py:44-84`
- `openviking/session/session.py:1073-1089`
- `openviking/storage/viking_fs.py:1343-1493`
- `openviking/retrieve/intent_analyzer.py:21-100`

这意味着 `search()` 不是简单“把 query 扔进向量库”，而是先让 LLM 根据当前问题和会话摘要做检索规划。

---

## 3. `IntentAnalyzer`：检索前的查询规划

`IntentAnalyzer` 的职责不是检索，而是把“当前问题”改写成更适合检索的查询计划。

它会把这些信息喂给 LLM：

- `compression_summary`
- 最近几条消息
- 当前用户问题
- 可选的 `context_type`
- 可选的 `target_abstract`

对应源码：

- `openviking/retrieve/intent_analyzer.py:38-65`
- `openviking/retrieve/intent_analyzer.py:102-133`

输出是 `QueryPlan`，里面包含多个 `TypedQuery`，每个查询会标记：

- `query`
- `context_type`
- `intent`
- `priority`

这就是为什么 `search()` 能同时查 memory / resource / skill，而不只是打一发向量相似度。

---

## 4. `HierarchicalRetriever`：真正的层级检索

OpenViking 的核心检索器是 `HierarchicalRetriever`。

对应源码：

- `openviking/retrieve/hierarchical_retriever.py:45-227`

### 4.1 检索输入

`retrieve()` 的主要输入：

- `TypedQuery`
- `ctx`
- `limit`
- `mode`
- `score_threshold`
- `scope_dsl`

它先做一次 query embedding，只做一次，避免重复计算。

对应源码：

- `openviking/retrieve/hierarchical_retriever.py:132-138`

### 4.2 起点选择

检索起点有两种来源：

1. 如果调用方显式传了 `target_directories`，直接用这些目录
2. 否则按 `context_type` 推出默认根目录

默认根目录的逻辑在：

- `openviking/retrieve/hierarchical_retriever.py:597-626`

典型根路径包括：

- `viking://user/.../memories`
- `viking://agent/.../memories`
- `viking://resources`
- `viking://agent/.../skills`

### 4.3 先做全局向量搜索

在真正递归下钻之前，它先做一次全局向量搜索，用来找到较强的起始点。

对应源码：

- `openviking/retrieve/hierarchical_retriever.py:146-155`
- `openviking/retrieve/hierarchical_retriever.py:229-252`

这个阶段会得到：

- 非叶子节点：作为递归起点
- 叶子节点（level 2）：作为初始候选

### 4.4 起点合并

检索器会把：

- 根目录
- 全局搜索命中的高分目录

合并成 `starting_points`。

对应源码：

- `openviking/retrieve/hierarchical_retriever.py:174-180`
- `openviking/retrieve/hierarchical_retriever.py:286-327`

### 4.5 递归下钻

真正的层级搜索在 `_recursive_search()`。

对应源码：

- `openviking/retrieve/hierarchical_retriever.py:355-506`

机制是：

1. 用优先队列按得分处理目录
2. 对当前目录调用 `search_children_in_tenant()`
3. 对子节点打分
4. 把父目录分数按 `score_propagation_alpha` 传播给子节点
5. 叶子节点直接作为候选
6. 非叶子节点继续入队，向下递归

分数传播公式在这里：

- `openviking/retrieve/hierarchical_retriever.py:456-460`

即：

```text
final_score = alpha * child_score + (1 - alpha) * parent_score
```

这里的 `alpha` 来自 `retrieval_config.score_propagation_alpha`。

### 4.6 收敛停止

它不是无限下钻。若 top-k 多轮不再变化，就会提前停止。

对应源码：

- `openviking/retrieve/hierarchical_retriever.py:484-499`

常量：

- `MAX_CONVERGENCE_ROUNDS = 3`

### 4.7 rerank

如果配置了 reranker，OpenViking 会在多处用 rerank 结果替换纯向量分数：

- 全局候选
- level-2 初始候选
- 目录子节点候选

对应源码：

- `openviking/retrieve/hierarchical_retriever.py:79-90`
- `openviking/retrieve/hierarchical_retriever.py:254-284`
- `openviking/retrieve/hierarchical_retriever.py:306-313`
- `openviking/retrieve/hierarchical_retriever.py:344-351`
- `openviking/retrieve/hierarchical_retriever.py:452-455`

---

## 5. L0 / L1 / L2 在检索结果里的含义

OpenViking 的层级结果不是同一种东西：

- L0: `.abstract.md`
- L1: `.overview.md`
- L2: 叶子内容

对应源码：

- `openviking/retrieve/hierarchical_retriever.py:52`
- `openviking/retrieve/hierarchical_retriever.py:583-595`

返回给上层时，如果结果是 L0 / L1，会给 URI 自动补后缀：

- `/.abstract.md`
- `/.overview.md`

这样上层能知道拿到的是摘要、概览，还是叶子记忆。

---

## 6. 检索结果如何变成最终返回对象

`_convert_to_matched_contexts()` 会把候选项转成 `MatchedContext`。

对应源码：

- `openviking/retrieve/hierarchical_retriever.py:508-581`

这里做了三件重要的事。

### 6.1 读取 relations

如果某条记忆/资源有关联 URI，会额外读取关联对象的 L0 摘要，塞到 `relations` 字段。

对应源码：

- `openviking/retrieve/hierarchical_retriever.py:522-533`

### 6.2 语义分数与热度分数混合

最终得分不一定等于向量相似度。

如果 `hotness_alpha > 0`，会混入热度分数：

```text
final_score = (1 - alpha) * semantic_score + alpha * hotness_score
```

对应源码：

- `openviking/retrieve/hierarchical_retriever.py:540-559`

而 `hotness_score()` 由两个因子组成：

- `active_count`
- `updated_at`

公式见：

- `openviking/retrieve/memory_lifecycle.py:19-64`

核心形式：

```text
sigmoid(log1p(active_count)) * time_decay(updated_at)
```

所以 OpenViking 的排序不是“只看相似度”，而是“相似度 + 最近是否常用”。

### 6.3 统一输出

最终输出是 `MatchedContext` 列表，常见字段有：

- `uri`
- `context_type`
- `level`
- `abstract`
- `category`
- `score`
- `relations`

---

## 7. “注入记忆”在 OpenViking 核心里到底在哪里

严格说，OpenViking 核心源码里没有一个统一的“把召回记忆固定拼成 prompt 块”的总开关。

核心层做的是：

1. 提供 `find/search`
2. 返回结构化召回结果
3. 把“用什么结果、怎么拼进去”留给上层调用方

这一点从 `SearchService.search()` 和 `VikingFS.search()` 很清楚：

- 它们返回的是 `FindResult`
- 没有直接改写 session message
- 没有统一插入 `<relevant-memories>` 的核心逻辑

对应源码：

- `openviking/service/search_service.py:44-84`
- `openviking/storage/viking_fs.py:1477-1493`

所以“注入记忆”更像集成约定，而不是底层检索层的默认副作用。

---

## 8. 插件侧如何把检索结果注入 prompt

### 8.1 OpenClaw 插件

OpenClaw 示例里，自动注入逻辑很明确：

1. 分别查：
   - `viking://user/memories`
   - `viking://agent/memories`
   - 可选 `viking://resources`
2. 合并去重
3. 只保留叶子结果 `level == 2`
4. 读出内容
5. 按字符预算选择可注入条目
6. 包装成 `<relevant-memories>`

对应源码：

- `examples/openclaw-plugin/auto-recall.ts:149-156`
- `examples/openclaw-plugin/auto-recall.ts:181-245`

关键点：

- 注入前会过滤成 `leafOnly`
- 单条记忆不会截断；放不下就跳过
- 最后拼成 XML 风格块再追加

### 8.2 OpenCode 插件说明

OpenCode 的示例 README 也明确写了同样的思路：

1. 每条用户消息到来时触发 recall
2. 搜索 OpenViking
3. 排序、去重
4. 组装 `<relevant-memories>` 块
5. 追加到消息文本

对应文档：

- `examples/opencode-memory-plugin/README.md:191-217`

所以你如果在某个产品形态里看到“OpenViking 自动把记忆塞进当前问答”，那往往是这个插件层行为，不是 `HierarchicalRetriever` 自己直接改 prompt。

---

## 9. 另一条链路：对话是怎么变成长时记忆的

如果只讲检索，不讲记忆生成，整条链是不完整的。

OpenViking 的长期记忆生成发生在 `session_commit` 之后。

### 9.1 `session_commit` 分两阶段

`commit_async()` 是两阶段：

#### Phase 1

- 给 session 加锁
- 按 `keep_recent_count` 切分消息
- 写 `history/archive_NNN/messages.jsonl`

对应源码：

- `openviking/session/session.py:592-734`

#### Phase 2

后台异步跑：

- 生成 archive summary
- 写 `.abstract.md`
- 写 `.overview.md`
- 调用 `extract_long_term_memories()`
- 写 `memory_diff.json`
- 更新 relations / active_count

对应源码：

- `openviking/session/session.py:736-931`

这也解释了为什么你会看到：

- commit 已经返回
- 但后台任务、日志、统计还在继续更新

因为真正的记忆抽取在后台异步跑。

### 9.2 `SessionCompressorV2` 的提取流程

长期记忆提取由 `SessionCompressorV2.extract_long_term_memories()` 执行。

对应源码：

- `openviking/session/compressor_v2.py:100-322`

它的流程是：

1. 初始化默认记忆文件
2. 建立 `ExtractContext`
3. 建立 `MemoryIsolationHandler`
4. 计算 schema 目录并加锁
5. 创建 `SessionExtractContextProvider`
6. 跑 `ExtractLoop`
7. 得到结构化 memory operations
8. `MemoryUpdater.apply_operations()`
9. 写 `memory_diff.json`

这里的关键不是“直接把一段对话转成一段文本”，而是“让 LLM 产出结构化 memory update 操作”。

### 9.3 `SessionExtractContextProvider`

这个 provider 负责给“记忆抽取 LLM”准备上下文。

对应源码：

- `openviking/session/memory/session_extract_context_provider.py:39-370`

它会：

1. 把会话对话整理成 `Conversation History`
2. 根据 memory schema 找出相关目录
3. 对多文件 schema 先做 `search`
4. 对单文件 schema 直接 `read`
5. 在 `eager_prefetch=true` 时，把搜索命中的 top-N 再提前读出来

关键逻辑：

- `prefetch()`：`openviking/session/memory/session_extract_context_provider.py:194-327`
- `get_tools()`：`openviking/session/memory/session_extract_context_provider.py:341-346`

如果 `eager_prefetch=true`，它会直接不给 LLM 暴露工具，只把预读结果塞进上下文。

这是为了减少多轮 tool-loop 开销。

### 9.4 `ExtractLoop`

`ExtractLoop` 不是检索器，而是“记忆更新操作生成器”。

对应源码：

- `openviking/session/memory/extract_loop.py:44-257`

它的做法是：

1. 根据 memory schema 生成严格 JSON Schema
2. 先加载 prefetch 结果
3. 调 LLM
4. 如果 LLM 要继续读工具，就继续一轮
5. 如果 LLM 直接给出最终 operations，就解析并落库

所以 OpenViking 的长期记忆生成，本质上是：

- 对话 -> 结构化操作
- 结构化操作 -> 更新记忆文件/索引

不是简单的“把整段聊天全文塞到向量库里”。

---

## 10. 一张简化流程图

```text
用户新问题
  ->
SearchService.search/find
  ->
VikingFS.search/find
  ->
IntentAnalyzer(仅 search)
  ->
HierarchicalRetriever
  ->
FindResult / MatchedContext
  ->
插件或 Agent 层决定是否注入
  ->
<relevant-memories> 或其他上下文拼装
  ->
最终 LLM 回答
```

另一条后台链：

```text
会话消息
  ->
session_commit
  ->
archive_NNN/messages.jsonl
  ->
.abstract.md + .overview.md
  ->
SessionCompressorV2
  ->
SessionExtractContextProvider + ExtractLoop
  ->
MemoryUpdater.apply_operations
  ->
memory_diff.json + 向量索引/关系更新
```

---

## 11. 对你排查问题时最有用的判断

### 11.1 如果你看到“记忆没注入”

先区分是哪个层出问题：

- 核心检索没召回
- 插件没把结果拼进 prompt
- 插件只保留了 `level == 2`，把目录级结果过滤掉了
- 字符预算/阈值把条目裁掉了

### 11.2 如果你看到“commit 后日志还在跑”

优先怀疑是 `session_commit` 的 Phase 2 还在后台执行，而不是前端卡死。

看这些产物是否继续出现：

- `history/archive_NNN/.abstract.md`
- `history/archive_NNN/.overview.md`
- `history/archive_NNN/memory_diff.json`
- `.done`

### 11.3 如果你看到“检索结果怪”

优先检查：

- `IntentAnalyzer` 生成的 query 是否偏题
- rerank 是否开启
- `score_threshold` 是否过高
- `hotness_alpha` 是否把旧但高相似的记忆压下去了
- 插件是否只取了 leaf 结果

---

## 12. 最终总结

OpenViking 的“注入记忆和检索机制”最好拆成三层理解：

### A. 检索规划层

- `IntentAnalyzer`
- 根据当前问题和 session 摘要生成多个查询

### B. 召回排序层

- `HierarchicalRetriever`
- 全局向量搜索 + 目录递归下钻 + rerank + 热度混排

### C. 上下文注入层

- 插件 / agent 集成逻辑
- 把 `FindResult` 转成 `<relevant-memories>` 或别的 prompt 片段

而长期记忆本身的生成，则是另一条后台链：

- `session_commit`
- `SessionCompressorV2`
- `SessionExtractContextProvider`
- `ExtractLoop`
- `MemoryUpdater`

所以，从工程角度看，OpenViking 不是“一个直接往 prompt 里塞记忆的单函数”，而是：

- 一套长期记忆抽取机制
- 一套层级检索机制
- 再配一层可替换的注入策略
