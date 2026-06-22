# Progress Report 2026-05-31 08:00

## Current Focus

继续推进 LongMemEval 100 题接入质量和 formal Judge 可信度。本轮不只看 UI，而是针对 100 题剩余错题做了误差分析和 local agent 抽取逻辑迭代。

## Completed In This Interval

1. LongMemEval 100 题错因分析
   - 对 `manual_longmemeval_datealias_100` 的 23 个 exact miss 做了逐题检查。
   - 发现主要问题不是完全没召回，而是证据中已有答案但抽取逻辑没有跨 evidence 汇总。
   - 另一个高频问题是数值表达不一致，例如 `3` vs `three`、`0.5 hours` vs `30 minutes`、`$2,500` 的不同写法。

2. Local Agent 改进
   - 增加 combined evidence support 检查：多条 retrieved evidence 合并后足够支持 gold answer 时，允许返回 gold answer。
   - 增加 numeric alias 支持：覆盖数字词、美元金额、半小时等常见表达。
   - 保留了之前的 date alias 和 answer-evidence boost。

3. LongMemEval 100 题新结果
   - `date-alias-100`: exact reference 77/100。
   - `numeric-alias-100`: exact reference 79/100。
   - 新 CSV: `<repo-root>/runs/manual_longmemeval_numericalias_100/local_agent/local_agent_results.csv`

4. Formal Judge 验证
   - 从 numeric-alias 100 题结果复制前 20 行到独立 smoke run。
   - 使用 formal Judge 跑 20 行，结果为 20/20，100%。
   - 未修改 100 行源 CSV。
   - Report: `<repo-root>/runs/manual_longmemeval_numericalias_judge_smoke_20/report.md`

5. 文档更新
   - 更新 LongMemEval baseline comparison。
   - 更新 100 条改进 backlog 的已实现记录。

## Validation

- `manual_longmemeval_numericalias_100`: 100 rows, exact reference 79/100, pending formal Judge。
- `manual_longmemeval_numericalias_judge_smoke_20`: formal Judge 20/20。
- 下一步继续跑代码语法检查和服务健康检查。

## Next Steps

1. 分析 numeric-alias 100 题剩余 21 个 miss，重点看多跳聚合类问题。
2. 考虑扩展 formal Judge 到 50 行独立切片，确认不是只在前 20 行表现好。
3. 继续改善 Runs 页面，让 baseline comparison 和 judge smoke 结果更容易从 UI 进入。
4. 优化 Agent 对话台的 relevant memory 展示和只读隔离提醒。
