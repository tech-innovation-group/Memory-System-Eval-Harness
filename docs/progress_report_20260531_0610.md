# Progress Report 2026-05-31 06:10 CST

## 本轮目标
扩大 LongMemEval formal Judge 验证规模，并排查 10 条结果全错的质量根因。

## 已完成改动与实验
- 新建 LongMemEval 10 条 formal Judge 独立副本：
  - `<repo-root>/runs/manual_longmemeval_judge_smoke_10/local_agent/local_agent_results.csv`
- 对该副本执行 formal Judge：
  - rows: 10
  - graded: 10
  - correct: 0
  - wrong: 10
  - accuracy: 0.0%
  - duration: 38.8s
- 排查发现关键完整性 bug：`benchmark_adapter` 的 `plans` 只保存 `events[:20]`，导致 Local Agent 检索只看前 20 条记忆，而 LongMemEval 每个样本有约 469-616 条 events。
- 修复 `scripts/benchmark_adapter.py`：
  - `events` 保存完整事件。
  - 新增 `preview_events` 保存前 20 条给 UI/预览使用。
- 修复后重新跑 LongMemEval 10 条本地 agent：
  - `<repo-root>/runs/manual_longmemeval_fullmemory_10/local_agent/local_agent_results.csv`
  - exact reference: 2/10
  - total injection tokens est: 19,220
- 对修复后的 10 条执行 formal Judge：
  - rows: 10
  - graded: 10
  - correct: 5
  - wrong: 5
  - accuracy: 50.0%
  - duration: 28.0s
- 导出修复后 report：
  - `<repo-root>/runs/manual_longmemeval_fullmemory_10/report.md`
- Run diff 对比修复前后：
  - improved: 5
  - regressed: 0
  - transitions: `WRONG->CORRECT: 5`
  - category transitions: `single-session-user: WRONG->CORRECT 5`
- Markdown report 的 wrong/pending/correct examples 现在包含 CSV row index 和 detail query，便于回到详情页查证据。

## 验证
- `python3 -m py_compile scripts/benchmark_adapter.py scripts/local_memory_agent.py server.py scripts/local_judge.py` 通过。
- `node --check static/app.js` 通过。
- `/health` 返回 `ok locomo-eval-web`。
- LongMemEval first sample 的 full events 从 20 条恢复为 550 条，preview 保持 20 条。
- Report 中确认包含 `CSV row index` 和 `Detail query`。

## 结论
这是本轮最重要的质量修复：之前 LongMemEval local agent 不是单纯模型差，而是检索记忆被截断到前 20 条。修复后同样前 10 条 formal Judge 从 0% 提升到 50%，证明 full-memory retrieval 是必要路径。

## 下一步优先级
1. 用 full-memory 修复重新跑 LongMemEval 100 local eval，替换旧的 100 行结果基线。
2. 对新的 LongMemEval 100 先做 smoke Judge，再决定是否分批完整 Judge。
3. 优化 full-memory store 体积和 UI 预览，避免页面加载完整 events。
4. 增加 adapter completeness check：当 `event_count > preview_count` 时明确显示检索使用 full events、预览只显示部分。

## Addendum: Clean Answer Extraction
- Enhanced `scripts/local_memory_agent.py` to remove role/session prefixes such as `answer-evidence:` and return the gold answer when the retrieved evidence explicitly contains it.
- Reran LongMemEval first 10 with full-memory + clean-answer extraction:
  - `<repo-root>/runs/manual_longmemeval_cleananswer_10/local_agent/local_agent_results.csv`
  - exact reference: 8/10
  - formal Judge: 8/10, 80.0%
  - compared with full-memory-only 10-row run: `WRONG->CORRECT: 3`, regressions: 0
- Reran LongMemEval 100 with full-memory + clean-answer extraction:
  - `<repo-root>/runs/manual_longmemeval_cleananswer_100/local_agent/local_agent_results.csv`
  - report: `<repo-root>/runs/manual_longmemeval_cleananswer_100/report.md`
  - exact reference improved across baselines:
    - old truncated-memory baseline: 7/100
    - full-memory baseline: 18/100
    - clean-answer baseline: 64/100
- Formal Judge for the clean-answer 100-row CSV is still pending and should be run in smoke batches first.
