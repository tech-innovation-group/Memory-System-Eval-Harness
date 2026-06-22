# Progress Report 2026-05-31 08:45

## Current Focus

继续处理 LongMemEval 100 题剩余错题。本轮重点是多跳聚合题：总费用、总小时、总天数、平均年龄、不同对象数量。

## Completed In This Interval

1. 剩余错题审计
   - 对 acronym-alias 100 的 20 个 exact miss 做逐题分析。
   - 发现多题 evidence 已召回完整信息，但需要计算或计数，而不是返回第一条 evidence。

2. Local Agent 聚合能力
   - 增加 money total / difference 计算。
   - 增加 hour / minute、day 求和。
   - 增加 average age 计算。
   - 增加 count-style 问题的 evidence 条目计数支持。

3. LongMemEval 100 新结果
   - Acronym baseline: 80/100 exact reference。
   - Aggregate run: 88/100 exact reference。
   - 新增修复 8 题，无 exact 回退。
   - CSV: `<repo-root>/runs/manual_longmemeval_aggregate_100/local_agent/local_agent_results.csv`

4. Formal Judge 验证
   - 将新增修复的 8 题复制到独立 judge run。
   - Formal Judge: 8/8，100.0%。
   - Report: `<repo-root>/runs/manual_longmemeval_aggregate_newfix_judge/report.md`

5. UI / 文档
   - Runs quick cards 更新为 `88/100 exact`。
   - LongMemEval baseline comparison 更新 aggregate rows。
   - Improvement backlog 更新聚合能力与 8/8 formal Judge 结果。

## Validation Pending

接下来运行：

- `node --check static/app.js`
- `python3 -m py_compile server.py scripts/benchmark_adapter.py scripts/local_memory_agent.py scripts/local_judge.py`
- `/health`
- 浏览器刷新检查 Runs quick cards。

## Next Steps

1. 继续分析剩余 12 个 exact miss。
2. 将聚合结果做成前端 evidence reasoning 展示，让用户知道答案是怎么算出来的。
3. 做一次收尾审计，整理 9 点前最终报告。
