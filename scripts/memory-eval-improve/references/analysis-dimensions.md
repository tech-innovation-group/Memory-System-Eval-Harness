# 分析维度与解读规则

本文档给「评测结果 → 记忆后端改进建议」的**解读层**提供统一框架。配合
`scripts/analyze_eval_results.py`（确定性计算）使用：脚本产出数值与
`observations`（带 heuristic 标记），本文档解释**每个数字意味着什么、往哪个方向
排查根因、有哪些后端无关的候选改进**。

> 重要：文中的阈值只是**启发式提示**，不是结论。判定「这是问题」之前，必须回到
> 原始产物抽查证据（retrieval_traces / judge reasoning / backend logs / 具体题目）。
>
> 配套：本文档覆盖**公用维度**（D1–D7）；各 benchmark 的**特有字段**解析见
> `benchmark-specific-fields.md`（locomo/dynamic/hotpotqa/longmemeval 各自独有的字段与读法）。

每个维度统一按以下结构描述：**定义 · 数据来源 · 计算方式 · 解读规则 · 通用改进方向**。

---

## D1 token 消耗

- **数据来源**：`summary.json`（`total_prompt/completion_tokens`）、
  `strict_blackbox_metrics.json`（answer/judge 分项、`tokens_per_correct`）、
  `qa_results.csv`（`prompt_tokens/completion_tokens`）、
  `dynamic_results.json`（`prompt_tokens/completion_tokens/cached_tokens`）、
  EchoMem `atomic_pipeline_completed` 日志的 `model_token_counts`（若经 getlog 可得）。
- **派生指标**：`tokens_per_correct = 可见总token / 判对题数`；注入记忆 token 占比 =
  注入记忆文本 token / 单问 prompt token（从 `retrieval_items_json` 或注入片段可估）。
- **解读规则**：
  - `tokens_per_correct > 2000` → 单题成本偏高，先拆 prompt/completion、再看注入记忆量与 top_k。
  - 单问 prompt token 远大于问题自身 → 通常是**注入记忆 + 历史上下文**过大（见 dynamic
    样本 avg prompt 17.4k，top_k=25 × 长记忆）。
  - completion 长尾 → 回答风格/工具链问题，与记忆关系较小。
  - `cached_tokens ≈ 0` → 无 KV 缓存复用，长会话 prompt 逐轮全量重算（可结合打字期预热优化）。
  - 内部抽取 token（extraction/仲裁/embedding）在 harness 侧**恒为 N/A**（strict_blackbox
    明确「never estimated」）；但从 `backend_logs.json` 的 `atomic_pipeline_completed.
    provider_diagnostics` 可以拿到真实值（见「backend_logs.json 后端日志解读」）。
    locomo 样本 19 commit：extraction 输入 314.9k + 输出 76.7k + repair 输入 10.3k token，
    repair 调用 26/53 ≈ 49%。这些是回答 token 之外的**隐藏成本**，优化 R7/R8 可省。
- **通用改进方向**：压缩注入记忆（更少条目/更短 snippet/只注入高相关）、收紧 top_k 与
  记忆预算、条目去重、打字期预热复用 KV、对低价值记忆降级为「需时再查」而非常驻注入。

## D2 检索延迟

- **数据来源**：`qa_results.csv` `retrieval_latency_ms` / `injection_total_ms`（=检索+编排）、
  `dynamic_results.json` `retrieval_latency_s`、EchoMem `recall_*` 阶段事件耗时。
- **派生指标**：avg/p50/p95/p99/max；**长尾比 p95/p50**；检索占 end-to-end 比例。
- **解读规则**：
  - p95/p50 > 5x → 明显长尾：少数查询极慢拖累体验（locomo 样本 8.4x、dynamic 22.8x）。
  - 检索占 end-to-end 比例高（>50%）→ 检索是主要瓶颈，先查多路召回/冷 embedding/重排。
  - p50 低但 p95 高 → 多为偶发慢路径（冷启动、首次 embedding、大结果集重排）。
- **通用改进方向**：query→结果缓存、top_k 调小、慢查询识别与降级（精简多路）、
  embedding 批处理/预计算、检索结果增量复用。

## D3 记忆注入延迟

- **数据来源**：`import_results.csv` `elapsed_s`（harness 侧逐 session）、
  `summary.json` import 统计、`backend_logs.json` 的
  `commit_completed` / `memory_extraction_completed` / `atomic_macro_stage_completed`
  耗时，以及 `atomic_pipeline_completed.macro_stage_timings_ms` 的 14 阶段拆分
  （EchoMem 侧阶段拆分，见「backend_logs.json 后端日志解读」）。
- **解读规则**：抽取阶段通常是注入耗时主体（locomo 样本 commit avg 23.5s/会话，几乎全部
  是 extraction）；注入阶段过长会推迟「记忆就绪」，影响首轮召回质量与导入吞吐。
  - `macro_stage_timings_ms` 里 `extraction` 通常占最大头（locomo 样本 avg 10.2s / commit
    ≈ 54%；dynamic 样本 24.6s），其次 `contradiction_resolution` / `merge_arbitration`；
    这三个 stage 高 → 抽取窗口/仲裁/去重是主优化点（R7），别误判成网络或服务慢。
- **通用改进方向**：抽取窗口/粒度调整、LLM 抽取与向量化异步化、批处理与幂等游标、
  减少冗余后处理（如向量库全量 compact）、并发数调优。

## D4 检索精度

- **数据来源**：`summary.json` `accuracy`、`diagnosis.json`
  `failure_breakdown`/`category_breakdown`/`retrieval_coverage`、
  `retrieval_traces.jsonl` 每问 `mode`/`retrieval_items`、
  `judge_results.csv` `verdict`/`reasoning`、hotpotqa/longmemeval `eval_results.csv`、
  dynamic `quality_report.json`（质量分 + `memory_utilization` 维度）。
- **失败模式 → 可能的记忆侧根因**（这是「精度 → 方案」的关键映射，需抽查证据确认）：
  - `temporal_reasoning`：证据缺失或时间信息被丢失/冲突 → 时间线检索、原子时间戳、
    时间感知排序。
  - `evidence_mismatch`：召回了错误版本/冲突事实 → 原子去重与版本仲裁（INVALIDATE/UPDATE）、
    注入时附带来源与时间。
  - `evidence_unused`：证据已召回但模型没用 → 注入位置/格式/指令问题，或证据与问题
    相关度不够（top_k 尾段噪声）。
  - `empty_retrieval`：没召回到东西 → 检索召回不足、query 改写、关键词覆盖。
  - `memory_missing`：记忆根本没生成 → 抽取环节问题。
- **解读规则**：`accuracy < 80%` 或质量分 < 80 时先按 failure mode 分类，看哪类占比高、
  哪个 category 最弱（locomo 样本 54.5% 的弱类其实是单跳事实类 Cat1；`temporal_reasoning` 失败 42%
  落在 Cat2 时间类——**不要假设 category↔失败模式等价**，用 `failure_breakdown[].question_ids`
  join `qa_results.csv[].category` 复核，类别语义以 dataset 题目为准，见
  `benchmark-specific-fields.md` locomo 节）。
- **通用改进方向**：重排（cross-encoder）、时间线/结构检索增强、原子去重与版本、
  注入格式与 top_k 调优、query 改写、按失败模式补检索策略。

## D5 生成的记忆数量

- **数据来源**：`qa_results.csv` `retrieval_count`/`num_retrieved`、
  `retrieval_items_json`（每问证据清单）、dynamic `quality_report.json`
  `recalled_memories_count`/`total_recalled_memories`/`ground_facts_count`、
  EchoMem 抽取 `work_counts`（若可得）、`import_results.csv` 成功率。
- **解读规则**（两个方向都要看）：
  - **过多**：召回数逼近 top_k、证据里低分项多 → token 浪费 + 稀释精度；表现为
    prompt token 高 + `evidence_unused` 占比高。
  - **过少**：召回数远小于 top_k、空检索/召回缺失 → 精度受损；表现为
    `empty_retrieval` / `memory_missing`。
  - 对照 `ground_facts_count`（dynamic）：召回数 vs 实际需要的事实数，看是否冗余。
- **通用改进方向**：动态 top_k（按 query 复杂度）、score 阈值截断、去重、
  注入预算与条目上限联动、对低相关记忆降级为按需查询。

## D6 质量 / 交互（dynamic 扩展）

- **数据来源**：`quality_report.json`（10 维分 + strengths/weaknesses + hallucination）、
  `dynamic_results.json`（tool_call_count / iterations / llm_latency / elapsed）。
- **解读规则**：`memory_utilization` 维度低 → 记忆没被充分利用（对照 D4 的
  evidence_unused）；`hallucination_detected` 多 → 记忆缺失或证据不可靠；
  tool_call_count / iterations 高 → 记忆注入不足导致模型反复工具查询。
- **注意**：dynamic 结果中的中文 query/reply/weakness 可能存在**编码损坏**
  （harness 侧 GBK→UTF-8 替换字符，已见实例），分析文本类字段时应留意，必要时以
  dataset.json 或数字指标为准。

## D7 健康度

- **数据来源**：`strict_blackbox_metrics.json`（request_success_rate / empty_retrieval_rate /
  failure_rate / submission_rate / retry_rate）、`summary.json` 各 errors、`run.log`、
  `backend_logs.json` 的 `index_diagnostics` / `page.complete`（见「backend_logs.json 后端日志解读」）。
- **解读规则**：成功率高但精度低 → 管线健康、算法问题；成功率高且精度高 → 维持；
  空检索率 > 10% → 召回覆盖问题；submission_rate < 1 → 注入不完整，记忆就绪度存疑。
  另外检查：`page.complete=false` → 日志窗口不完整，后端指标可能偏小；
  `records_missing_user_id` 占比高 → 身份隔离/日志归属存疑；
  `atomic_pipeline_completed.outcome_reason` 出现 `completed_with_extraction_gaps` → 抽取有缺口，
  可能影响 `memory_missing` 类失败。
- **通用改进方向**：注入失败重试、记忆就绪事件、检索降级。

## backend_logs.json 后端日志解读

`backend_logs.json` 是 `agent_plugin.getlog()` 拉取的本轮 EchoMem 服务日志（所有 benchmark 与
dynamic 都有），结构固定：`{query, page, items[], diagnostics}`。脚本的 `backend` 维度已确定性提取，
本文解释每个字段的含义与组合读法。

### 顶层字段

| 字段 | 含义 | 解析提示 |
|---|---|---|
| `query` / `page` | 拉取时的分页参数与结果统计（`total_matched`/`has_more`/`returned`） | `page.complete=false`（has_more 或 truncated 或条数 < total）→ **日志窗口不完整**，用后端指标下结论前先确认全量 |
| `diagnostics` | 索引健康：`files_indexed`/`records_indexed`/`records_missing_user_id`/`parse_errors`/`partial_lines_skipped`/`index_rebuilt` | `records_missing_user_id / records_indexed` 高（样本 95%+）→ 日志归属/身份隔离存疑；`parse_errors`/`partial_lines_skipped` > 0 → 日志截断 |
| `items[]` | 事件流：`http_request_completed` / `commit_*` / `memory_extraction_*` / `atomic_*` | 每项含 `ts`/`event`/`duration_ms`/`method`/`route`/`status_code` |

### 关键事件与组合读法

- **`atomic_pipeline_completed`（注入最肥的一行）**：
  - `outcome_reason`：`completed_with_extraction_gaps` 表示抽取有缺口（locomo 样本 7/19、dynamic
    1/1）→ 结合 D4 的 `memory_missing` 看是否导致召回缺失。
  - `macro_stage_timings_ms`：14 个阶段耗时。`extraction` 通常占 commit 的大头（locomo 样本
    avg 10.2s ≈ 54%，dynamic 24.6s），其次 `contradiction_resolution` / `merge_arbitration`。
    → 注入慢先看这三者（R7），别误判网络。
  - `provider_diagnostics`：**内部 token 与 embedding 的唯一来源**（harness 侧恒 N/A）。
    - `llm_atom_extraction_input/output_tokens` + `llm_atom_extraction_repair_*`：抽取 LLM 消耗
      （locomo 样本 19 commit 合计输入 314.9k / 输出 76.7k / repair 输入 10.3k）。
    - `repair_calls / extraction_calls`：repair 率（locomo 样本 26/53 ≈ 49%）→ 高则抽取 prompt
      质量差、纯浪费 token（R8）。
    - `embedding_logical_texts` / `embedding_cache_hit_texts` / `embedding_unique_misses`：
      embedding 去重/缓存命中率（locomo 89%，dynamic 仅 41%）→ 低命中 = 每次注入重复调 embedding
      provider（R8），同时推高注入延迟。
    - `llm_contradiction_detection_*` / `llm_graph_arbitration_*`：仲裁 LLM 消耗（对应
      `contradiction_resolution` / `merge_arbitration` 阶段的 token）。
- **`http_request_completed`（按 method+route 聚合，见脚本 `http_routes`）**：
  - `POST /api/sessions/{id}/messages`、`POST .../commit`、`GET .../commits/{id}`（轮询）：
    注入阶段的写与轮询耗时。
  - `POST /api/sessions/open`：建会话。
  - 检索相关路由慢 → D2 检索延迟的根因定位（结合 `retrieval_latency_ms`）。
- **`commit_completed` / `memory_extraction_completed` / `commit_accepted` / `commit_stage_completed`**：
  commit 生命周期：`commit_accepted.message_count`（本轮消息数）、`preparation_ms`、
  `commit_stage_completed.queue_wait_ms`（队列等待）。`commit_completed.duration_ms` 是全量
  注入端到端（样本 18.9s，与 `import_results.csv.elapsed_s` 对应）。

### 结合维度

- 抽取 token 高 → D1（隐藏成本）+ R8。
- `extraction`/`contradiction_resolution`/`merge_arbitration` stage 高 → D3 + R7。
- repair 率高 → R7/R8（抽取 prompt 质量）。
- `completed_with_extraction_gaps` 多 → D4 `memory_missing` 根因 + R7。
- embedding 缓存命中低 → D3 + R8。
- `records_missing_user_id` 高 → D7 身份隔离/日志归属。
- `page.complete=false` → 以上所有后端指标先打折扣。



## TTFT / cached tokens（专项说明）

- 只有 `echo_agent` / `echoagent_live` 类插件才测量打字期 TTFT 与 cached tokens；
  `echomem_mcp` 插件不测 → 这些字段为 null，skill 应标注 `unavailable` 而非臆造。
- 当 TTFT 可用时：TTFT 高 → 打字期预热/预取不足；cached_tokens 高 → KV 复用有效。

## 跨维度交叉判断示例

| 现象组合 | 初步方向 |
|---|---|
| 召回数高 + prompt token 高 + evidence_unused 多 | 注入过多 → 收紧 top_k/预算/截断 |
| 召回数高 + 精度低 + evidence_mismatch 多 | 结果质量差 → 重排/去重/版本仲裁 |
| 召回数低 + temporal 失败多 | 时间线召回不足 → 时间索引/时间感知 |
| 检索 p95 高 + 精度尚可 | 缓存 + 慢查询降级，不必改算法 |
| 注入慢 + 抽取 token 高 | 抽取窗口/粒度/repair 调优 |
