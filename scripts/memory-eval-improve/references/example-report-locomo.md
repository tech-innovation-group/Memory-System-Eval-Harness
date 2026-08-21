# 示例分析报告：LoCoMo（示例演示用）

> 本文件是 `memory-eval-improve` skill 的**示例输出**，用于演示报告结构与证据写法，
> 数据来自真实运行 `benchmarks/locomo/results/20260812_173550_635312`。
> 改进方案是**建议**，不代表已实施；每条附「现象/根因/方案/收益/风险/验证」。

## 1. 概览

| 项 | 值 |
|---|---|
| benchmark / 类型 | locomo（conv-30，81 问） |
| 记忆后端 / 插件 | echomem / echomem_mcp（MCP 8111 检索，不经 31030 插件） |
| 模型 | deepseek-v4-flash |
| top_k / 记忆预算 | 25 / 8000 chars |
| 运行状态 | completed；19/19 session 注入成功；81/81 QA 成功 |
| 运行窗口 | 585.6s（约 9.8 min，含注入+QA+Judge） |

## 2. 逐维度明细

### D4 检索精度（核心指标）

- **accuracy 76.54%**（62/81 对，0 error）。
- 失败归因（diagnosis）：**temporal_reasoning 8（42%）** > evidence_mismatch 6（32%）>
  evidence_unused 5（26%）。
- 按 category：**Cat1 54.5%（11 题中只对 6）** < Cat2 73.1% < Cat4 84.1%。
  → 单跳事实类（Cat1，语义按 dataset 题目复核）反成最弱环；temporal 失败 8 题实际落在 Cat2 时间类。
- retrieval_coverage 1.0，无 empty_retrieval、无 retrieval error → 管线健康，纯算法问题。

### D5 生成的记忆数量

- `retrieval_count` avg **22.7/25（p50=25，几乎顶满 top_k）**；81 问共 1840 条证据，全为 atomic。
- evidence score avg 0.729（p50 0.744）——**平均得分不高却塞满 top_k**，说明尾部混入低相关证据。

### D1 token 消耗

- answer prompt avg 1485（p50 1594）/ completion avg 184；judge avg 512。
- **tokens_per_correct = 2180.7**（>2000 触发高成本提示）。
- prompt 主要被「system + 问题 + 25 条记忆 + 上下文」占据；记忆注入占比可从
  `retrieval_items_json` 的 snippet 长度估算（本例未拆，留待深挖）。

### D2 检索延迟

- avg 2.19s；**p50 1.01s / p95 8.46s（长尾比 8.4x）** → 少数查询极慢。
- 需定位最慢题目（qa_results 按 retrieval_latency_ms 排序）确认慢因。

### D3 记忆注入延迟

- 逐 session import avg 24.6s（max 36.5s），总注入 468s。
- backend_logs 拆分：`commit_completed` avg **23.5s**，其中 `memory_extraction_completed`
  avg 23.5s → **注入耗时几乎全部是抽取**；`atomic_macro_stage_completed` avg 1.68s
  （p95 8.7s，存在单 stage 长尾）。

### D7 健康度

- request_success_rate 100%、空检索率 0%、submission_rate 100%、retry 0。

## 3. 根因分析

| 现象 | 证据 | 归因环节 |
|---|---|---|
| 精度 76.5%，Cat1（单跳事实）54.5%，temporal 失败 8 题落在 Cat2 | diagnosis failure_breakdown + qa_results.category | 时间线检索/时间戳或原子冲突（见 P2） |
| evidence_mismatch 6 题 | 同一事实多版本证据（如「失去银行家工作」多条） | 原子合并仲裁/去重（见 P2b） |
| 召回 22.7/25 顶满、evidence score 均值仅 0.73 | qa_results retrieval_count / retrieval_items | top_k 过大/无截断（见 P1） |
| tokens_per_correct 2180 | strict_blackbox | 注入量与 top_k 联动（见 P1） |
| 检索 p95/p50=8.4x | strict_blackbox latency | 慢查询无缓存（见 P3） |
| commit 23.5s/会话，抽取占满 | backend_logs commit_completed | 抽取窗口/repair/compact（见 P4） |

## 4. 改进方案清单（按优先级）

### P1 [后端无关] 收紧检索注入：top_k 截断 + 预算联动（高收益低风险）
- **现象**：召回顶满 top_k=25，evidence 平均分仅 0.73，tokens_per_correct 2180。
- **根因**：无 score 截断、注入条数不与预算联动，低相关记忆既费 token 又稀释精度
  （可能加剧 evidence_unused）。
- **方案**：按 score 阈值截断注入条目；把 top_k / 注入 limit 与 memory_budget_chars 联动；
  对低相关记忆改「按需查询」而非常驻注入。
- **收益**：prompt token↓、tokens_per_correct↓、证据信噪比↑。
- **风险/代价**：截断过严可能漏证据 → 用 failure mode 验证不新增 missing/empty。
- **验证**：同一数据跑两组 top_k（25 vs 12）对比 accuracy / tokens_per_correct / 延迟。
- EchoMem 钩子：`atom_retriever.py` 语义/关键词/结构化 top_k=50、`recall_default_limit=25`、
  `search.fusion.max_results=12`；注入 `entrypoints/plugins/echoagent/server.py`（limit=5、预算 0.4/0.6）。

### P2 [EchoMem] 时序精度：时间线召回与原子冲突治理（结构性，见效慢）
- **现象**：Cat1（单跳事实）54.5%；temporal 失败 8 题落在 Cat2、evidence_mismatch 6 题。
- **根因**：时间线召回已存在但时序题仍弱——需确认 (a) 抽取原子是否携带可信时间戳
  （`[time=...]` 标记），(b) 同 subject 冲突版本是否被仲裁/INVALIDATE，(c) 排序是否时间感知。
- **方案**：抽查 temporal 失败题的 `retrieval_traces.jsonl` 命中证据是否含正确时间；
  按结论调整时间戳抽取或时间感知排序；强化原子合并仲裁（R5）。
- **收益**：时序类与 evidence_mismatch 双降。
- **风险/代价**：时间感知排序可能干扰普通事实题 → 按 category 分策略。
- **验证**：按 category 拆 accuracy 前后对比；对 `conv-30_qa6/qa10/...` 逐题看召回证据。
- EchoMem 钩子：`atom_retriever.py`（时间线 `find_by_time_range`）、
  `atom_merge_engine.py`（ADD/UPDATE/REPLACE/INVALIDATE）。

### P3 [后端无关] 检索延迟长尾：query→结果缓存 + 慢查询定位（低成本）
- **现象**：retrieval p95/p50=8.4x。
- **根因**：每次查询全量多路召回，无 query→结果缓存；个别查询触发慢路径。
- **方案**：同 session 内重复/近重复查询走结果缓存；定位 p95 最慢题目看召回路数。
- **收益**：长尾↓、重复查询零延迟。
- **风险/代价**：需与 commit 版本联动失效。
- **验证**：对比同 session 重复 query 的 retrieval_latency_ms。
- EchoMem 钩子：`local_recall_orchestrator.py` recall 入口加缓存（参考
  `req_coordinator/prefetch.py` 的 PrefetchCache 模式）。

### P4 [EchoMem] 注入延迟：抽取成本与 compact 降频（结构性）
- **现象**：commit 23.5s/会话，抽取占满。
- **根因**：LLM 抽取（窗口=12）是耗时主体；每次 commit 全量 vector_compact 是隐藏成本。
- **方案**：按会话长度/复杂度调抽取窗口与粒度；减少 repair 重试；compact 改增量/异步。
- **收益**：注入吞吐↑、记忆就绪更快。
- **风险/代价**：窗口过大稀释原子粒度。
- **验证**：对比 import_results.elapsed_s 与 backend_logs 各阶段耗时、`model_token_counts`。
- EchoMem 钩子：`raw_atom_extractor.py`（窗口/repair）、`atomic_memory_engine.py:294`（vector_compact）。

## 5. 不可用指标说明

- 内部抽取/注入 token（`internal_memory_injection_tokens`）：harness 恒为 N/A，需从
  EchoMem `atomic_pipeline_completed` 日志的 `model_token_counts` 获取（本次未拉取）。
- 打字期 TTFT / cached tokens：locomo 走 mcp 插件，不测。
