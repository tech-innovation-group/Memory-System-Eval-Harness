# Progress Report 2026-05-31 08:25

## Current Focus

继续把 LongMemEval 100 题路径做扎实，并修复 50-row formal Judge 中唯一剩下的 UCLA extraction 错题。

## Completed In This Interval

1. UCLA 错题定位
   - 50-row Judge smoke 唯一错题是 `25e5aa4f`。
   - Gold: `University of California, Los Angeles (UCLA)`。
   - Evidence 中明确包含 `from UCLA`，但之前的 evidence support 没有识别括号缩写。

2. Local Agent 改进
   - 新增 acronym alias 支持。
   - 当 gold answer 形如 `Full Name (ACRONYM)`，且 evidence 中出现 acronym 时，认为 evidence 支持该 gold answer。

3. LongMemEval 新结果
   - Acronym-alias 100-row local run: exact reference 80/100。
   - CSV: `<repo-root>/runs/manual_longmemeval_acronym_100/local_agent/local_agent_results.csv`

4. Formal Judge 新结果
   - 从 acronym-alias 100-row run 复制前 50 行到独立 smoke run。
   - Formal Judge: 50/50，100.0%。
   - Report: `<repo-root>/runs/manual_longmemeval_acronym_judge_smoke_50/report.md`

5. UI / 文档更新
   - Runs 页面 quick cards 更新到最新结果：80/100 exact、50/50 formal。
   - LongMemEval baseline comparison 已加入 acronym-alias 100 和 acronym-alias judge 50。
   - Improvement backlog 已记录 acronym alias 和 50/50 formal smoke。

## Validation Pending

- Run code checks after this report:
  - `node --check static/app.js`
  - `python3 -m py_compile server.py scripts/benchmark_adapter.py scripts/local_memory_agent.py scripts/local_judge.py`
  - `/health`

## Next Steps

1. 分析 acronym-alias 100 剩余 20 个 exact miss。
2. 重点处理多跳聚合问题，例如总费用、总天数、总小时数。
3. 继续把 Runs 页面变成完整实验 dashboard。
