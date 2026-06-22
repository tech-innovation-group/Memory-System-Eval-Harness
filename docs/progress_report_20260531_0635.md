# Progress Report 2026-05-31 06:35 CST

## 本轮目标
扩大 clean-answer LongMemEval 100 的 formal Judge smoke 覆盖，并把多代基线对比固化到 UI 和文档。

## 已完成
- 从 clean-answer 100 基线复制前 20 行到独立 formal Judge run：
  - `<repo-root>/runs/manual_longmemeval_cleananswer_100_judge_smoke_20/local_agent/local_agent_results.csv`
- Formal Judge 20 条结果：
  - rows: 20
  - graded: 20
  - correct: 16
  - wrong: 4
  - accuracy: 80.0%
  - duration: 16.6s
  - rate-limit warnings: 0
  - source 100-row CSV 未修改。
- 导出 20 条 smoke report：
  - `<repo-root>/runs/manual_longmemeval_cleananswer_100_judge_smoke_20/report.md`
- 新增 LongMemEval 基线对比文档：
  - `<repo-root>/docs/longmemeval_baseline_comparison_20260531.md`
- 对比文档包含：
  - truncated-100: exact 7/100, formal pending
  - full-memory-100: exact 18/100, formal pending
  - clean-answer-100: exact 64/100, formal pending
  - clean-answer-judge-3: formal 3/3
  - clean-answer-judge-10: formal 8/10
  - clean-answer-judge-20: formal 16/20
- Runs 分析页面新增按钮和面板：`LongMemEval 基线对比`。
  - 可直接读取并展示 `/docs/longmemeval_baseline_comparison_20260531.md`。
  - 包含 report 路径复制、摘要卡片和完整 Markdown 展开。

## 验证
- `python3 -m py_compile server.py scripts/benchmark_adapter.py scripts/local_memory_agent.py scripts/local_judge.py` 通过。
- `node --check static/app.js` 通过。
- `/health` 返回 `ok locomo-eval-web`。
- `/api/file` 可读取 LongMemEval baseline comparison 文档，包含 `clean-answer-judge-20` 和 `64/100`。

## 当前 LongMemEval 状态
- clean-answer 100 local baseline 已完成，exact 64/100。
- clean-answer 100 formal Judge 全量尚未执行。
- clean-answer 100 的 smoke coverage 已到 20 条，formal Judge 16/20，稳定 80%。

## 下一步优先级
1. 判断是否继续扩大 formal Judge 到 50 条独立副本，或先针对 4 个 wrong 做错误聚类/抽取规则修复。
2. 在 LongMemEval baseline 面板中加入一键填入 diffBase/diffCandidate。
3. 增强时间类/数量类 answer extraction，降低剩余 wrong。
4. 优化 memory_store 存储体积，保留 full events 但避免 UI/文件过重。
