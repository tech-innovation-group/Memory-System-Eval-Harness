# Progress Report 2026-05-31 08:10

## Current Focus

继续扩大 LongMemEval formal Judge 覆盖，同时把 Runs 页面变成更容易操作的实验结果入口。

## Completed In This Interval

1. LongMemEval 50-row formal Judge
   - 从 `manual_longmemeval_numericalias_100` 复制前 50 行到独立 smoke run。
   - 使用 formal Judge 评测 50 行，不修改 100 行源 CSV。
   - 结果：49/50，98.0% formal accuracy。
   - 唯一错题：`25e5aa4f`，问题为 UCLA / Bachelor's degree extraction。
   - Report: `/Users/chx/locomo-eval-web/runs/manual_longmemeval_numericalias_judge_smoke_50/report.md`

2. Runs 页面易用性
   - 新增 `最新 LongMemEval 结果` 按钮。
   - 新增 3 个 quick cards：
     - LongMemEval 100: 79/100 exact reference。
     - Judge Smoke 50: 49/50 formal。
     - Judge Smoke 20: 20/20 formal。
   - 点击 quick card 内的 `打开 run` 可以直接加载对应 CSV、题目列表、artifact 和 report 路径。

3. 文档更新
   - 更新 LongMemEval baseline comparison，加入 numeric-alias Judge 50。
   - 更新 100 条改进 backlog，记录 50-row formal Judge 和 Runs 快捷入口。

## Validation Pending

接下来运行：

- `node --check static/app.js`
- `python3 -m py_compile server.py scripts/benchmark_adapter.py scripts/local_memory_agent.py scripts/local_judge.py`
- `/health`
- 浏览器刷新检查新按钮和 quick cards。

## Next Steps

1. 修复 UCLA 抽取 row，目标让 50-row formal Judge 达到 50/50。
2. 继续分析剩余 100 题中多跳聚合问题。
3. 将最新 LongMemEval 快捷入口和 baseline comparison 做得更像正式实验 dashboard。
