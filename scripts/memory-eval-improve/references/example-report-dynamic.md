# 示例分析报告：Dynamic（示例演示用）

> 本文件是 `memory-eval-improve` skill 的**示例输出**，用于演示报告结构与证据写法，
> 数据来自真实运行 `dynamic/results/20260812_164553_840495`。
> 改进方案是**建议**，不代表已实施。

## 1. 概览

| 项 | 值 |
|---|---|
| benchmark / 类型 | dynamic（generate，10 轮） |
| 记忆后端 / 插件 | echomem / echomem_mcp |
| 模型 | deepseek-v4-flash |
| top_k / 记忆预算 | 25 / 8000 chars |
| 运行状态 | completed，10/10 轮无 error |
| 运行窗口 | 约 9.3 min（10 轮生成+评测） |

## 2. 逐维度明细

### D4 检索精度（质量）

- **avg quality 87.2/100**（良好）；9/10 轮 task_completed；hallucination 0。
- 维度分：information_accuracy 13.9/15、task_completion 13.4/15、fact_coverage 12.5/15、
  **memory_utilization 8.7/10**（中等）、response_efficiency 4.1/5、user_experience 4.0/5。

### D5 生成的记忆数量

- **total_recalled_memories 255**；每轮 avg 25.5（p50 27），而每轮 ground_facts avg 仅 2.1
  → **注入量约为实际需求事实数的 12 倍**，明显过度注入。
- prefetch_committed_count = 0（打字期预取未生效，见下）。

### D1 token 消耗

- **avg prompt 17382 tokens（p50 9136 / p95 42870！）**，avg completion 2134。
- **cached_tokens 全 0** → 无 KV 缓存复用，长 prompt 每轮全量重算。
- 42870 的 p95 说明存在 prompt 膨胀到 4 万+ 的轮次（25 条记忆 × 长 snippet + 工具历史）。

### D2 检索延迟

- avg 4.19s；**p50 0.985s / p95 22.44s（长尾比 22.8x）** → 极端长尾，个别轮检索极慢。
- LLM 延迟 avg 24.6s/轮。

### D6 交互

- 每轮 tool_call avg **3.1**（逐轮数据；注意 summary 字段记为 3.9，与逐轮均值不符，
  属 harness 汇总小 bug，以逐轮为准）、iterations avg 2.4。
- 工具调用/迭代偏高 → 记忆注入不足或信噪比低，模型需反复工具查询补齐。

### D3 记忆注入延迟

- backend_logs 显示 commit avg 36.7s（n=1，本轮仅 1 次注入会话）。

## 3. 根因分析

| 现象 | 证据 | 归因环节 |
|---|---|---|
| 召回 25.5/轮 vs 事实 2.1/轮，prompt 17.4k | dynamic_results / quality_report | 注入过量：top_k/预算/截断不联动（P1） |
| cached 全 0、prompt 长 | dynamic_results cached_tokens | 无打字期预热/KV 复用（P3） |
| 检索 p95/p50=22.8x | per-round retrieval_latency_s | 慢查询无缓存、多路召回（P2） |
| tool_call/iterations 偏高 | 逐轮 tool_call_count | 注入信噪比低 → 模型工具兜底（P1） |
| memory_utilization 8.7/10 中等 | quality_report 维度分 | 召回多但利用率一般，与 evidence_unused 同源 |

## 4. 改进方案清单（按优先级）

### P1 [后端无关] 注入去冗余：动态条数 + score 截断（最高收益）
- **现象**：召回 25.5/轮 vs 事实 2.1/轮，prompt avg 17.4k，tool_call 3.1。
- **根因**：top_k=25 全量注入，记忆预算 8000 未与条数/分数联动 → 大量低相关记忆既费
  token 又稀释注意力，模型只能靠工具查询兜底。
- **方案**：按 query 复杂度动态 top_k；score 阈值截断；低相关记忆改按需查询。
- **收益**：prompt token 可望降数倍、quality↑（memory_utilization 与效率维度）、
  tool_call↓。
- **风险/代价**：截断过严漏关键事实 → 用 ground_facts 命中率验证。
- **验证**：同数据集跑 top_k 25 vs 12，对比 prompt_tokens / quality / tool_call_count。
- EchoMem 钩子：同 locomo 示例 P1（`atom_retriever.py` top_k、`server.py` 注入 limit/预算）。

### P2 [后端无关] 检索长尾：缓存 + 慢查询定位（低成本）
- **现象**：p95/p50=22.8x，个别轮检索 22s+。
- **根因**：无 query→结果缓存，长尾轮次多为首次冷检索或超大结果集。
- **方案**：同 session 重复查询缓存；定位最慢 round 看召回路数/结果量。
- **收益**：长尾↓、平均延迟↓。
- **风险/代价**：缓存与 commit 版本联动失效。
- **验证**：对比命中缓存轮 vs 未命中轮的 retrieval_latency_s。
- EchoMem 钩子：`local_recall_orchestrator.py` + `prefetch.py` 缓存模式。

### P3 [EchoMem+EchoAgent] 打字期预热回归（结构性，跨项目）
- **现象**：cached_tokens 全 0、无 TTFT 数据（mcp 插件不测）、prefetch_committed=0。
- **根因**：当前无 max_tokens=1 暖 KV 的 prefill 机制（已被移除）；仅剩 memory_prefetch 预取。
- **方案**：评估重新引入 prefill 预热（历史参考 EchoMem `prefetch-develop` 分支
  `prefill_pipeline.py`），或用支持 TTFT 的插件（echo_agent/echoagent_live）重测，
  量化首字延迟后决定投入。
- **收益**：长会话 TTFT↓、cached_tokens↑。
- **风险/代价**：跨 EchoMem+EchoAgent 改动、预热请求额外成本。
- **验证**：用 echo_agent 插件跑 dynamic，对比 ttft_ms / cached_tokens。

## 5. 数据质量提示（本轮特有）

- `summary.json` 的 `avg_tool_call_count=3.9` 与逐轮数据均值 3.1 不符——以逐轮数据为准。
- dynamic 结果中的中文 query/reply/weakness 存在**编码损坏**（harness 侧 GBK→UTF-8
  替换字符），文本类字段不可直接引用，以数字指标与 dataset.json 为准。
- TTFT / cached_tokens 对本插件（echomem_mcp）为 unavailable，不得臆造。
