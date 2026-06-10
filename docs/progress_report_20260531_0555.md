# Progress Report 2026-05-31 05:55 CST

## 本轮目标
验证 LongMemEval 100 题后续 formal Judge 链路，不直接修改 100 行原始结果；同时把 Judge 配置 smoke test 做成页面按钮。

## 已完成改动
- 检查到 `judge.conf` 已配置 Judge Base URL、模型 `gpt-5.5` 和 API key。
- 复制 LongMemEval 100 题结果的前 3 行到独立 run：
  - `/Users/chx/locomo-eval-web/runs/manual_longmemeval_judge_smoke_3/local_agent/local_agent_results.csv`
- 对独立 3 行副本执行 formal Judge：
  - rows: 3
  - graded: 3
  - correct: 0
  - wrong: 3
  - accuracy: 0.0%
  - pending: 0
- 原始 LongMemEval 100 行 CSV 未被修改，仍保持 100 行 pending，便于后续分批正式 Judge。
- 为 smoke run 写入 manifest 并导出 Markdown report：
  - `/Users/chx/locomo-eval-web/runs/manual_longmemeval_judge_smoke_3/manifest.json`
  - `/Users/chx/locomo-eval-web/runs/manual_longmemeval_judge_smoke_3/report.md`
- `scripts/local_judge.py` 增加重试和更清晰错误：
  - HTTP 错误显示状态码和响应片段。
  - 空响应显示 `empty judge API response`。
  - 非 JSON 响应显示响应片段。
  - 默认 transient error 最多重试 2 次。
- Eval 页面新增安全按钮：`Judge 前 3 条 pending`。
  - 它通过 pending-preview 获取前三条 pending 的 row index。
  - 只 Judge 这几行，不会触发全部 100 题。

## 验证
- `node --check static/app.js` 通过。
- `python3 -m py_compile server.py scripts/local_judge.py` 通过。
- `/health` 返回 `ok locomo-eval-web`。
- `/api/pending-preview` 对 LongMemEval 100 题 CSV 返回前三条 pending indexes `[0, 1, 2]`。
- Judge smoke 第三行首次遇到 API parse error，增强 retry 后单行重跑成功，最终 3/3 graded。

## 当前状态
- LoCoMo smoke + formal Judge：已完成。
- LongMemEval 100 local agent：已完成，formal Judge 源 CSV 仍 pending。
- LongMemEval formal Judge smoke：已完成 3 行独立副本，证明 Judge 配置和链路可用。

## 下一步优先级
1. 在 UI 里更明确区分 `正式 Judge smoke` 和 `全量 Judge`。
2. 给 report 中的 wrong/pending 示例增加一键定位详情的路径信息。
3. 做 LongMemEval 10 条分批 formal Judge 独立副本，观察错误率和耗时。
4. 继续压缩结果页视觉密度，尤其是 Evidence 和 Context 的折叠默认状态。
