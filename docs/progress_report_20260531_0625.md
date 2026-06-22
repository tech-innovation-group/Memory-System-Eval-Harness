# Progress Report 2026-05-31 06:25 CST

## 本轮目标
验证 clean-answer LongMemEval 100 新基线的 formal Judge smoke，并把“完整 memory events vs 页面预览”这个关键完整性约束产品化。

## 已完成
- 基于 `<repo-root>/runs/manual_longmemeval_cleananswer_100/local_agent/local_agent_results.csv` 复制前 3 行到独立 Judge smoke run：
  - `<repo-root>/runs/manual_longmemeval_cleananswer_100_judge_smoke_3/local_agent/local_agent_results.csv`
- 对该 3 行副本执行 formal Judge：
  - rows: 3
  - graded: 3
  - correct: 3
  - wrong: 0
  - accuracy: 100.0%
  - source 100-row CSV 未被修改，仍保持 pending。
- 导出 smoke report：
  - `<repo-root>/runs/manual_longmemeval_cleananswer_100_judge_smoke_3/report.md`
- 数据集页补充 memory completeness 说明：
  - 小数据集扫描时 KPI 显示 `Memory Events`。
  - 大文件 lazy 概览明确提示：页面概览不会全量扫描，正式任务会完整读取 memory events。
- `importSample` 下拉中的 `sessions` 文案改成更准确的 `events`，减少误解。

## 验证
- `python3 -m py_compile server.py scripts/benchmark_adapter.py scripts/local_memory_agent.py scripts/local_judge.py` 通过。
- `node --check static/app.js` 通过。
- `/health` 返回 `ok locomo-eval-web`。
- `/api/dataset` 对 LongMemEval-S 返回：
  - `runner_status=large_dataset_lazy`
  - note: `页面概览不会全量扫描，正式任务会完整读取 memory events。`
- clean-answer 100-row first 3 formal Judge smoke: 3/3 correct。

## 当前 LongMemEval 状态
- 旧 100 基线 exact: 7/100。
- full-memory 100 基线 exact: 18/100。
- clean-answer 100 基线 exact: 64/100。
- clean-answer 100 formal Judge 尚未全量执行；已完成前 3 条 smoke，3/3 correct。

## 下一步优先级
1. 对 clean-answer 100 做 10 条或 20 条分批 formal Judge，观察真实准确率和限流情况。
2. 在 UI Runs 分析里突出显示三代 LongMemEval baseline 的对比。
3. 给 Local Agent 增加更多 answer extraction 规则，尤其是时间/数量类。
4. 为 full-memory local store 增加轻量索引，避免长期保存过大的 JSON 影响浏览。

## Addendum: 10-row Formal Judge Smoke
- Copied first 10 rows from clean-answer 100 baseline into an isolated run:
  - `<repo-root>/runs/manual_longmemeval_cleananswer_100_judge_smoke_10/local_agent/local_agent_results.csv`
- Formal Judge result:
  - rows: 10
  - graded: 10
  - correct: 8
  - wrong: 2
  - accuracy: 80.0%
  - duration: 5.9s
- Report:
  - `<repo-root>/runs/manual_longmemeval_cleananswer_100_judge_smoke_10/report.md`
- The 100-row source CSV remains pending and unmodified.
