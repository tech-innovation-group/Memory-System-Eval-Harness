# Benchmark 特有字段与智能解析提示词

> 本文件与 `analysis-dimensions.md` 互补：后者讲**公用维度**（D1–D7，所有 benchmark 共享的指标）；
> 本文按 benchmark 类型（locomo / dynamic / hotpotqa / longmemeval）讲**特有字段**——只有该类型
> 才产出的字段、它们的含义，以及**如何智能解析**（怎么看、和哪些字段组合看、什么数值组合暗示什么问题、
> 归到哪个维度与根因）。
>
> 原则：脚本 `analyze_eval_results.py` 只负责确定性计算，**脚本没算出来的特有字段，直接读原始
> JSON/CSV 解析**，不臆造、不把脚本输出当结论。阈值与映射仍是启发，动手下结论前回到原始产物抽查证据。
>
> 注意：`dynamic` 不是 benchmark 而是**评测模式**（模拟用户多轮对话 + 质量评测），下文按
> `detect_type` 输出的类型名分别对待。

---

## 1. locomo

### 本节适用

结果目录含 `qa_results.csv` + `judge_results.csv`（`detect_type = locomo`）。

### 特有产物清单

| 产物 | 作用 |
|---|---|
| `diagnosis.json` | 失败归因：`failure_breakdown` / `category_breakdown` / `retrieval_coverage` |
| `retrieval_traces.jsonl` | 逐问命中证据明细（每问 `verdict`/`judge_reasoning`/`retrieval_items`） |
| `qa_results.csv` | 逐问结果 + 特有列（`category`、各 `*_status`、`retrieval_items_json` 等） |
| `judge_results.csv` | Judge 判定明细（`verdict`/`reasoning`） |
| `memory_provenance.json` | 注入来源追溯（expected/actual session 数） |
| `tool_audits.jsonl` | 工具调用审计（若启用） |
| `summary.json` | `sample_filter`（如 conv-30）、`strict_blackbox` 汇总 |

### 特有字段与解析提示词

| 字段路径 | 含义 | 解析提示 |
|---|---|---|
| `diagnosis.json.failure_breakdown[].{mode,count,percentage,question_ids}` | 失败模式（`temporal_reasoning` / `evidence_mismatch` / `evidence_unused` 等）+ 命中题目清单 | **不要假设类别↔失败模式等价**。用 `mode.question_ids` join `qa_results.csv[question_id].category`，求出该失败模式真正落在哪个 category，再读那几题的 `retrieval_traces` 确证。示例里 `temporal_reasoning` 8 题全部落在 Cat2（时间类），而旧示例误挂到 Cat1。 |
| `diagnosis.json.category_breakdown.{1,2,3,4}.accuracy` | 每类准确率 | 类别**数值语义随 dataset 变化**，不硬编码——回到 `benchmarks/locomo/data/<dataset>.json` 实际题目复核（经验：Cat1=单跳事实 What/Where、Cat2=时间类 When、Cat3=假设/推断 Would、Cat4=跨事件/多跳 Why/What-does-X-think；与部分旧文档标注不一致，以题目为准）。最弱类若是「本应最简单」的类（如单跳事实），反而更像证据/注入问题而非检索召回问题。 |
| `diagnosis.json.retrieval_coverage` | 有检索结果的问题占比 | `=1.0` 且无 retrieval error → 管线健康，精度问题在算法层（召回质量/仲裁/注入），可优先用 D4→R1/R3/R4/R5 方向。 |
| `diagnosis.json.retryable/missing/unexpected/duplicate_question_ids` | 题目层面的异常集合 | 非空时先排查这些题是否被漏跑/重跑/意外样本，避免把数据问题当算法问题。 |
| `qa_results.csv[].category` | 该题类别 | join 归因的键；与 `question_id` 一一对应。 |
| `qa_results.csv[].{retrieval_status,answer_status,model_status,health_status}` | 各环节状态 | 任何非 `ok` 的题先单独看：是超时/重试/模型错误还是检索空，别混进精度统计。 |
| `qa_results.csv[].model_retry_count` | 模型调用重试次数 | 高重试 → 偶发慢路径/服务波动，是健康度问题而非算法问题。 |
| `qa_results.csv[].tool_call_count,iterations` | 单题工具调用/迭代数 | locomo 也记录；偏高说明模型靠工具兜底，常与注入不足/证据噪声同源。 |
| `qa_results.csv[].evidence_policy,evidence_origin,retrieval_source_mode` | 证据注入策略与来源 | 组合读：`evidence_origin` 说明证据来自会话内还是历史归档，跨归档证据缺失可能与 `evidence_mismatch` 相关。 |
| `retrieval_traces.jsonl[].retrieval_items[].text` | 命中证据文本（含 `[time=...]` / `current=false` 标记） | **时序诊断的关键**：时序失败题要看命中证据是否带正确 `[time=...]`、是否被 `current=false` 标为过期、是否存在同 subject 多版本冲突（→ R4/R5）。 |
| `retrieval_traces.jsonl[].retrieval_items[].{score,memory_type,evidence_uri}` | 证据分数/类型/来源 URI | 低分证据挤占 top_k → D5 过度注入（R3/R6）；`evidence_uri` 区分 session 与 archive 来源。 |
| `retrieval_traces.jsonl[].verdict,judge_reasoning` | Judge 判定与理由 | `verdict=CORRECT` 但理由里证据没被引用 → 判对但证据链可疑，深挖 `evidence_unused`。 |

### 特有字段 → 维度/根因/方案

| 特有字段读数 | 归属维度 | 根因方向（`analysis-dimensions.md` / `improvement-catalog-echomem.md`） |
|---|---|---|
| failure 集中在某 category | D4 | 该类别对应的检索策略不足：时间类→R4 时间线/时间戳；多跳类→R3 top_k/多路召回；冲突证据→R5 仲裁 |
| `[time=...]` 缺失/冲突 | D4 | R4（抽取是否携带可信时间戳）+ R5（同 subject 版本仲裁） |
| `current=false` 证据仍进 top_k | D4/D5 | 过期/历史证据未按 current 过滤 → R5 版本仲裁 + R6 注入过滤 |
| retrieval_coverage=1.0 但 accuracy 低 | D4 | 召回全但质量低 → R1 重排 / R3 截断 / R5 去重 |
| model_retry_count/`*_status` 异常 | D7 | 健康度问题，先修管线再谈算法 |

---

## 2. dynamic

### 本节适用

结果目录含 `dynamic_results.json`（`detect_type = dynamic`）。

### 特有产物清单

| 产物 | 作用 |
|---|---|
| `dynamic_results.json` | 逐轮明细（`rounds[]`）+ 顶层 `facts`（ground-truth 事实表）/`config`/`summary` |
| `dynamic_results.csv` | 逐轮明细（同 rounds，扁平 CSV） |
| `quality_report.json` | 质量评测：10 维分 + `summary`（`total_recalled_memories` 等）+ `results[]` |
| `dataset.json` | 本轮 eval 的查询/事实（编码损坏字段的 ground truth 来源） |
| `summary.json` | 汇总（`mode`/`prefetch_committed_count`/TTFT/cached 等） |

### 特有字段与解析提示词

| 字段路径 | 含义 | 解析提示 |
|---|---|---|
| `summary.json.mode` | 评测模式 | 不同 mode 的任务形态不同，解读指标前先确认，避免拿错基线。 |
| `summary.json.{avg,median,p95}_ttft_ms` / `avg_cached_tokens` | 打字期首字延迟 / KV 缓存复用 | **组合判断**：`avg_cached_tokens≈0` + `ttft_ms` 高 → 无预热/KV 复用（R9）；`ttft_ms=null` → 插件不测（echomem_mcp），标 `unavailable` 不臆造。 |
| `summary.json.prefetch_committed_count` 与逐轮 `prefetch_committed` | 打字期预取是否生效 | 恒 0 且 `cached_tokens≈0` → 预取/预热链路未生效（R9），别把「没做预取」写成「预取失败」。 |
| `summary.json.avg_tool_call_count,avg_iterations` vs 逐轮均值 | 汇总 vs 逐轮 | **已知 harness 汇总小 bug**：summary 的 `avg_tool_call_count`（样本 3.9）与逐轮均值（3.1）可能不符——**以逐轮 `rounds[]` 数据为准**。 |
| `rounds[].{tool_call_count,iterations}` | 每轮工具调用/迭代数 | 偏高 → 注入信噪比低，模型反复工具查询补齐（D6 → R6 注入精简）。 |
| `rounds[].complexity` | 轮次复杂度（low/medium/high） | 按复杂度分桶对比指标（如高复杂度轮检索慢/召回多），比整体均值更有信息量。 |
| `rounds[].is_new_session` | 是否新会话 | 新会话首轮无记忆上下文，与老会话后段混在一起算会稀释结论；分桶看跨会话 vs 会话内。 |
| `rounds[].is_injection` | 是否注入轮 | 注入轮不计入正常问答统计，解析时排除或单独标注。 |
| `rounds[].ground_facts[]` / `dynamic_results.json.facts` | 该轮需要的事实（ground truth） | **过度注入判断**：`recalled_memories_count`（quality_report 每轮召回数）与 `ground_facts_count` 的倍数（样本 25.5 vs 2.1 ≈ 12x）→ 注入远超实际需求（D5 → R6）。 |
| `quality_report.json.summary.avg_dimension_scores.*` + `dimension_info.*.max_score` | 10 维质量分（含满分信息） | 看**比例**而非裸分（如 8.7/10 与 4.0/5 不可直接比）。重点：`memory_utilization` 低 → 记忆没被利用（D4 evidence_unused 同源）；`response_efficiency` 低 → 冗余/啰嗦（可能被过度注入拖累）。 |
| `quality_report.json.results[].{strengths,weaknesses,quality_reason}` | 逐轮评语 | 文本类字段**可能编码损坏**（GBK→UTF-8 替换字符），不可逐字引用；可看结构/关键词，结论以数字与 dataset.json 为准。 |
| `quality_report.json.results[].{hallucination_detected,task_completed}` | 幻觉/任务完成标记 | `hallucination_detected` 多 → 记忆缺失或证据不可靠（D6）；`task_completed` 低 → 记忆不足以完成任务。 |
| `dynamic_results.json.config` | 运行配置（mode/num_memories/num_queries/agent_plugin） | 跨运行对比时确认配置可比的键，别拿不同配置的轮数直接比。 |

### 特有字段 → 维度/根因/方案

| 特有字段读数 | 归属维度 | 根因方向 |
|---|---|---|
| recalled/ground_facts 倍数大 | D5/D1 | 注入过量 → R6 动态条数/score 截断/预算联动 |
| cached_tokens≈0 + ttft 高/null | D1/D6 | 无打字期预热/KV 复用 → R9 |
| memory_utilization 低 | D4 | 注入未被利用 → R6（少而精）+ R1 重排提信噪比 |
| tool_call/iterations 高 | D6 | 注入不足或噪声 → R6 截断 + 按需查询 |
| complexity 分桶后某桶异常 | D2/D4 | 复杂查询检索慢/召回差 → R2 缓存 / R3 动态 top_k |

---

## 3. hotpotqa

### 本节适用

结果目录含 `eval_results.csv` 且 `summary.json.benchmark = hotpotqa`（`detect_type = hotpotqa`）。

### 特有产物清单

| 产物 | 作用 |
|---|---|
| `eval_results.csv` | 逐题 `answer_*` / `supporting_facts_*` / `joint_*` 指标 |
| `summary.json` | `answer_em/f1`、`supporting_facts_em/f1`、`joint_em/f1` 汇总 |

### 特有字段与解析提示词

| 字段路径 | 含义 | 解析提示 |
|---|---|---|
| `eval_results.csv[].answer_em/answer_f1/answer_precision/answer_recall` | 答案匹配指标 | 只看答案对错，不反映证据是否召回。 |
| `eval_results.csv[].supporting_facts_em/supporting_facts_f1` | 支撑事实（证据文档）匹配指标 | **多跳特有、最容易漏**：answer 高但 supporting_facts 低 → 答案蒙对但**证据文档没召回/召回错** → 检索完整性问题（R3 多路召回、R5 证据去重）。 |
| `eval_results.csv[].joint_em/joint_f1` | 联合指标（答案+证据同时正确） | 真正的多跳得分。`joint` 远低于 `answer` → 检索证据链断裂（缺一跳）→ R3 top_k/多路 + R1 重排。 |
| `summary.json.{answer,supporting_facts,joint}_*` | 汇总 | 与逐题一致即可直接引用；不一致时以逐题 `eval_results.csv` 为准。 |

> **脚本覆盖缺口**：`analyze_eval_results.py` 找 `support_*` 列，但 hotpotqa 实际列名是
> `supporting_facts_*`，因此**支撑事实与 joint 指标脚本取不到**——必须直接读 `eval_results.csv`。

### 特有字段 → 维度/根因/方案

| 特有字段读数 | 归属维度 | 根因方向 |
|---|---|---|
| answer 高 / supporting_facts 低 | D4 | 证据召回不完整或错档 → R3（多路/top_k）+ R5（证据去重/仲裁） |
| joint 双低 | D4 | 多跳证据链断裂 → R3 + R1 重排 + 检查第二跳证据是否注入 |
| supporting_facts_f1 中但 precision 低 | D4/D5 | 召回多但噪声大 → R3 截断 + R6 注入条数 |

---

## 4. longmemeval

### 本节适用

结果目录含 `eval_results.csv` 且 `summary.json.benchmark = longmemeval`（`detect_type = longmemeval`）。

### 特有产物清单

| 产物 | 作用 |
|---|---|
| `eval_results.csv` | 逐题 `question_type` / `correct` / `judge_error` |
| `summary.json` | `overall_accuracy` / `task_averaged_accuracy` / `abstention_accuracy` / `abstention_count` / `per_type{correct,total,accuracy}` |

### 特有字段与解析提示词

| 字段路径 | 含义 | 解析提示 |
|---|---|---|
| `summary.json.per_type.{single-session-user,single-session-preference,single-session-assistant,multi-session,temporal-reasoning,knowledge-update}.accuracy` | 6 类任务各自的准确率 | **弱场景定位**：哪类最差即哪个记忆场景最弱——`multi-session` 低 → 跨会话召回不足；`knowledge-update` 低 → 冲突/版本仲裁（R5）；`temporal-reasoning` 低 → 时间线检索（R4）。 |
| `summary.json.task_averaged_accuracy` vs `overall_accuracy` | 类别平均 vs 总体 | 两者差距大 → 类别不均衡（某类题多且差被稀释）→ 报告时分开看，别只给一个数。 |
| `summary.json.abstention_accuracy,abstention_count` | 弃权（拒绝作答）表现与数量 | `abstention_count` 高 → 模型过度保守拒绝 → 注入不足或指令问题；`abstention_accuracy` 低 → 该拒的没拒，判断阈值问题。 |
| `eval_results.csv[].question_type` | 逐题任务类型 | 与 `correct` 一起可复算 per_type；核查 judge_error 多的类是否污染该类型统计。 |

> **脚本覆盖缺口**：longmemeval 的 `eval_results.csv` 无 `answer_em` 等列，`analyze_eval_results.py`
> 的 eval 分支取不到任何精度指标，`per_type` 也未暴露——必须直接读 `summary.json.per_type` 与
> `eval_results.csv`。

### 特有字段 → 维度/根因/方案

| 特有字段读数 | 归属维度 | 根因方向 |
|---|---|---|
| multi-session 类低 | D4 | 跨会话召回/身份路由 → R3 检索范围 + `retrieval_scope` 核查 |
| knowledge-update 类低 | D4 | 冲突/版本仲裁 → R5 |
| temporal-reasoning 类低 | D4 | 时间线/时间戳 → R4 |
| abstention 高 | D4/D7 | 保守拒绝 → 注入充分性 + 指令（R6） |

---

## 通用坑位（所有类型都要看）

- **dynamic 中文文本字段可能编码损坏**（GBK→UTF-8 替换字符），query/reply/weakness 不可逐字引用，
  以数字指标与 `dataset.json` 为准。
- **summary 与逐轮数据不一致时以逐轮为准**（已见 `avg_tool_call_count` 3.9 vs 逐轮均值 3.1）。
- **类别数值语义不硬编码**：locomo 的 category 以 dataset 实际题目复核（经验 Cat2 才是时间类，
  与部分旧文档标注不符）。
- **脚本没算出来的特有字段直接读原始文件**，并标注数据来源，便于复现。
