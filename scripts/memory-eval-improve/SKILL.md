---
name: memory-eval-improve
description: >
  分析 Memory-System-Eval-Harness 的记忆后端评测结果，从 token 消耗、检索延迟、
  记忆注入延迟、检索精度、生成的记忆数量等多维度诊断记忆系统，并输出可落地的
  记忆算法改进方案。
  当用户给出一个或多个评测结果目录（locomo / dynamic / hotpotqa / longmemeval，
  任意记忆后端/插件），要求定位记忆问题、提出改进建议、分析评测结果时使用。
  输入：结果目录路径（如 benchmarks/locomo/results/<run_id>），或 --latest 取最新一轮。
---

# 记忆后端评测结果分析 → 改进方案

把「评测结果」翻译成「记忆算法改进方案」。脚本负责确定性计算，本 skill 负责
**解读、根因定位、方案设计**——不要直接把脚本输出当结论，也不要臆造脚本没算出的指标。

## 输出形式

一份 Markdown 分析报告，结构固定：

1. **概览表**：benchmark / 后端 / 插件 / 模型 / top_k / 记忆预算 / 运行窗口 / 状态。
2. **逐维度明细**（D1–D7）：每个维度的关键数值 + 解读 + 是否触发异常标记。
3. **benchmark 特有字段明细**：按结果类型（locomo/dynamic/hotpotqa/longmemeval）解析该类型
   独有的字段（见步骤 3.5），给出读数 + 解读。
4. **根因分析**：把异常现象映射到后端环节，给出证据（引用具体题目/文件）。
5. **改进方案清单**：每条含 **现象 / 根因 / 方案 / 预期收益 / 风险代价 / 验证方式**，
   标注「后端无关」或「EchoMem 钩子（文件:行 配置项）」。
6. **不可用指标说明**：哪些维度拿不到、为什么（如 TTFT 对 mcp 插件为 null）。

## 执行步骤

### 1. 解析输入

- 一个或多个结果目录；目录缺失时若给出的是 `results` 父目录，用 `--latest` 取最新一轮。
- 读取 `config.json` / `summary.json`，确认 `benchmark` 类型、`memory_backend`、
  `agent_plugin`、`top_k`、`memory_budget_chars`。这些决定用哪份 schema 和哪份钩子目录。

### 2. 提取指标

对每个目录运行解析脚本（只读、无依赖、输出固定 schema JSON）：

```bash
python "Memory-System-Eval-Harness/scripts/memory-eval-improve/scripts/analyze_eval_results.py" "<结果目录>" [<更多目录>] [--latest] [--report] [--out <file>]
```

- 脚本自动识别 locomo / dynamic / hotpotqa / longmemeval / generic，缺失的产物按
  strict_blackbox 惯例标 `unavailable` + 原因，**不得补造**。
- `observations[]` 里的 flag 是启发式提示，不是结论——下一步要回原始产物验证。

### 3. 逐维度诊断

对照 `references/analysis-dimensions.md` 解读每个维度的数值。对触发异常或值得深挖的点，
**回到原始产物抽查证据**（这一步不能省）：

- 精度异常 → `diagnosis.json` 的 failure_breakdown、`retrieval_traces.jsonl` 的
  mode/label、`judge_results.csv` 的 verdict/reasoning。
- token 高 → 拆 prompt/completion；用 `qa_results.csv` 的 `retrieval_items_json` 估算注入记忆占比。
- 检索延迟长尾 → 在 `qa_results.csv` 找出 p95 对应的最慢题目，看它调了几路召回。
- 注入慢 → `backend_logs.json` 的 `commit_completed` / `memory_extraction_completed` 阶段耗时，
  及 `atomic_pipeline_completed.macro_stage_timings_ms`（extraction 通常占 commit 大头，其次
  contradiction_resolution / merge_arbitration）。
- 内部 token / embedding → `backend_logs.json` 的 `atomic_pipeline_completed.provider_diagnostics`
  （extraction/repair/仲裁 LLM token + embedding 去重/缓存命中，见
  `references/analysis-dimensions.md` 的「backend_logs.json 后端日志解读」）。
- 后端健康 → `backend_logs.json` 的 `diagnostics`（records_missing_user_id / parse_errors）与
  `page.complete`（日志窗口是否完整），以及 `atomic_pipeline_completed.outcome_reason`
  （`completed_with_extraction_gaps` → 抽取缺口，可能与 `memory_missing` 相关）。
- dynamic 质量 → `quality_report.json` 的维度分、strengths/weaknesses、hallucination。
  **注意**：dynamic 结果里的中文 query/reply/weakness 可能有编码损坏（harness 侧
  GBK→UTF-8 替换字符），以数字指标和 dataset.json 为准。

### 3.5 按 benchmark 类型解析特有字段

公用维度（D1–D7）之外，每个结果类型还有**只有它才产出**的特有字段。对照
`references/benchmark-specific-fields.md` 对应节，逐条按「字段 + 解析提示」读取原始产物：

- **locomo** → 读 `diagnosis.json` 的 failure_breakdown / category_breakdown、`retrieval_traces.jsonl`
  的 `[time=...]`/`current=false`/score/evidence_uri、`qa_results.csv` 的 category 与各 `*_status`。
- **dynamic** → 读 `dynamic_results.json` 逐轮（tool_call/iterations/complexity/is_new_session/
  is_injection/prefetch_committed/cached_tokens/ttft_ms/ground_facts）、`quality_report.json` 的
  10 维分与 recalled vs ground_facts、`summary.json` 的 mode/TTFT/cached。
- **hotpotqa** → 读 `eval_results.csv` 的 `answer_*`/`supporting_facts_*`/`joint_*`（脚本未提取，必读原文）。
- **longmemeval** → 读 `summary.json.per_type` 与 `eval_results.csv`（脚本未暴露，必读原文）。

解析要点：
- **失败归因先 join 再下结论**：如 locomo 的 `failure_breakdown[].question_ids` 要 join
  `qa_results.csv[question_id].category` 确定失败真正落在哪类，不假设类别↔失败模式等价。
- **类别数值语义以 dataset 实际题目复核**，不硬编码（locomo 经验：Cat2 才是时间类）。
- **summary 与逐轮数据不一致时以逐轮为准**（已见 dynamic `avg_tool_call_count` 汇总与逐轮均值不符）。
- 特有字段的发现并入步骤 4 的根因定位与步骤 5 的方案，报告里单列「benchmark 特有字段明细」。

### 4. 根因定位

把现象归到后端环节（按 `references/analysis-dimensions.md` 的「失败模式→根因」映射）：
- 检索环节：多路召回、重排、缓存、top_k、意图路由、时间线。
- 写入环节：抽取窗口/粒度、repair 重试、合并仲裁、向量 compact、异步化。
- 注入环节：注入格式、预算拆分、条数上限、注入位置。
- 打字期环节：prefetch 预取、prefill 预热（若可用）。

### 5. 产出改进方案

- 后端是 EchoMem 时，从 `references/improvement-catalog-echomem.md` 的 R1–R10 中挑选
  与证据匹配的方案；每个方案按「现象/根因/方案/收益/风险代价/验证方式」完整写出，
  附上**已核实的**文件:行引用与配置项。若某条引用的行号你未核实，写文件路径即可，不要编造行号。
- 后端不是 EchoMem 时，只用 `analysis-dimensions.md` 的通用方向，不套用 EchoMem 路径。
- 优先级排序：优先低成本高收益（缓存、top_k 调参、截断），再做高成本结构性改动
  （重排、prefill 预热回归、意图 LLM 化）。区分「立即能试的配置改动」与「需写代码的改动」。

### 6. 输出与卫生

- 输出 Markdown 报告；`memory_identity.auth_key` 等密钥**必须脱敏**为 `[REDACTED]`。
- 明确标注不可用指标及原因；不要用「内部记忆 token」这种 harness 恒为 N/A 的值当结论。
- 需要时把脚本 `--json` 结果与报告一起保留，便于复现。

## 注意事项

- 脚本与 references 均为**只读**：不修改评测结果、不修改 EchoMem/EchoAgent 代码。
- 改进方案是**建议**，不是已实施改动；落地前应与用户确认。
- 跨运行对比：多个目录一起传入即可横向对比（accuracy / tokens_per_correct / 延迟分布）；
  也可借助 `benchmarks/locomo/compare.py` 得到逐题 improved/regressed 清单再归因。
- 阈值只是启发：`tokens_per_correct>2000`、`accuracy<80%`、`p95/p50>5x`、`空检索率>10%`
  都需回原始证据确认后才写进「根因」。
- 特有字段解析按 `references/benchmark-specific-fields.md`：脚本没算出的字段（hotpotqa
  `supporting_facts_*`/`joint_*`、longmemeval `per_type`、dynamic summary 级 TTFT/mode 等）
  直接读原始 JSON/CSV，不臆造。
- locomo 的 category 数值语义以 dataset 实际题目为准（经验 Cat2 才是时间类），不沿用旧文档的
  「Cat1 时序」标注。
- dynamic 中文文本字段（query/reply/weakness）可能编码损坏，只以数字指标与 dataset.json 为证。
- `backend_logs.json` 用后端指标前先看 `page.complete`（脚本 `backend.page`）：`false` 表示日志
  窗口不完整（has_more/truncated/条数不足），此时内部 token/stage 耗时等会偏小，不能当全量结论。
- `backend_logs.json` 的 `atomic_pipeline_completed.outcome_reason` 出现
  `completed_with_extraction_gaps` 只说明抽取有缺口，是否影响精度要回到 D4 `memory_missing`
  的实际失败题目确认，不直接下结论。
