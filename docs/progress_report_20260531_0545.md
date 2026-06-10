# Progress Report 2026-05-31 05:45 CST

## 本轮目标
继续提升分析和故障诊断能力，让系统不只是能跑，还能快速判断一次迭代为什么变好/变差、是否遇到模型限流。

## 已完成改动
- Run diff 后端新增评分迁移统计：例如 `WRONG->CORRECT`、`CORRECT->WRONG`、`UNSCORED->CORRECT`。
- Run diff 后端新增 category 级迁移统计，可看到 C2/C4 等类别分别变好或变差多少。
- Run diff 前端新增摘要区：
  - 评分迁移
  - Category 变化
  - Improved / Regressed / Changed / Shared KPI 保留。
- Task public payload 新增 `log_diagnostics`：扫描任务日志中的 `rate limit`、`429`、`quota`、`throttle`、`限流` 等信号。
- 当前任务条新增限流/429 提醒，显示最近一条命中的日志内容。
- 最近任务列表也会显示限流/429 条数，方便快速定位失败原因。
- 导出的 Markdown report 新增 `Log Diagnostics` 章节，离线报告也能看到限流/配额告警。
- `docs/improvement_backlog_100.md` 已补充本轮完成项。

## 验证
- `node --check static/app.js` 通过。
- `python3 -m py_compile server.py` 通过。
- 服务已重启并通过 `/health`：`ok locomo-eval-web`。
- 对 `/Users/chx/locomo-eval-web/runs/manual_locomo_smoke_5` 重新导出 report，确认包含 `Log Diagnostics` 和 `Rate-limit warnings`。
- 临时构造 2 行 base/candidate CSV 验证 `/api/run-diff`：
  - improved: 1
  - regressed: 1
  - transitions: `{'WRONG->CORRECT': 1, 'CORRECT->WRONG': 1}`
  - category transitions: `{'2': {'WRONG->CORRECT': 1}, '4': {'CORRECT->WRONG': 1}}`

## 当前状态
- LoCoMo smoke + formal Judge 已完成：5 题，正式准确率 20%。
- LongMemEval 100 本地 agent 结果已完成：100 行，formal Judge 仍 pending。
- LongMemEval pending 可以筛选、单行 Judge、筛选 Judge、筛选导出。

## 下一步优先级
1. 对 LongMemEval 100 做小批量 formal Judge 试跑，验证用户提供的 judge 配置是否可用。
2. 把 rate-limit 诊断加入导出报告。
3. 给 run diff 增加示例跳转到问题详情。
4. 继续清理 UI 中密集区域，让结果页更像正式 benchmark report。
