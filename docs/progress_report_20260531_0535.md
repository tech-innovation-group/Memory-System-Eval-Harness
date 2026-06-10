# Progress Report 2026-05-31 05:35 CST

## 本轮目标
继续把 LoCoMo/OpenViking/LongMemEval harness 往可用的评测平台形态推进，重点补齐 LongMemEval 100 题之后的分批 Judge 能力，并修正上一轮用户反馈的视觉风格方向。

## 已完成改动
- 恢复蓝灰主色体系，保留更克制的实验平台式布局；避免紫色系误读。
- 待 Judge 面板新增明确说明：当前筛选只处理筛选匹配的 pending 行，全部 pending 会处理整个 CSV 的未评分行。
- 待 Judge 面板新增三个操作入口：
  - `Judge 当前筛选`
  - `Judge 全部 pending`
  - `Judge 此行`
- `scripts/local_judge.py` 支持子集 Judge：
  - `--only-pending`
  - `--question-ids`
  - `--row-indexes`
  - `--category`
  - `--query`
  - `--min-tokens`
  - `--max-tokens`
- 后端 `judge` task 会把这些过滤条件传入 local judge。
- 前端启动 Judge 时现在会传 UI 中填写的 Judge Base URL、Judge 模型、Judge API Key，避免静默使用默认配置。
- 导出 pending CSV 现在遵守当前筛选条件；筛选 `commute` 时只导出匹配的 1 行，而不是全部 100 行。
- 问题详情页改成报告式结构：问题头、Gold/Agent Response 对比、Judge Reasoning、Evidence 卡片、Context Preview。
- `docs/improvement_backlog_100.md` 已补充本轮完成项。

## 验证
- `node --check static/app.js` 通过。
- `python3 -m py_compile server.py scripts/local_judge.py` 通过。
- `/health` 返回 `ok locomo-eval-web`。
- `/api/pending-preview` 对 LongMemEval 100 题 CSV 筛选 `commute`：
  - matched: 1
  - pending: 100
  - shown: 1
  - question_id: `118b2229`
- 临时复制 LongMemEval 100 CSV 后运行筛选 Judge：
  - 命令选中 1/100 行。
  - 无 token 时保持 pending，不误报正式准确率。
  - 未修改真实结果 CSV。
- `/api/export-pending-csv?q=commute` 导出 1 行，question_id 为 `118b2229`。
- `/api/question-detail` 抽查 LoCoMo smoke 第 2 行：Judge=`WRONG`，relevant memory=4 条。

## 当前数据集测试状态
- LoCoMo smoke + formal Judge：5 题，正式 Judge 1/5，20%。
- LongMemEval 100：100 行本地 agent 结果已生成，formal Judge 仍 pending；Exact/reference 仅作为字符串参考，不作为正式准确率。

## 下一步优先级
1. 在结果详情页加更清晰的 evidence/context/Judge 三段式报告视图。
2. 给 LongMemEval 100 pending 做小批量正式 Judge 试跑，验证 API Key/Base URL/模型链路。
3. 增加 run diff 的可读摘要：新增 correct/wrong、从 wrong 到 correct、从 correct 到 wrong。
4. 增加 rate-limit/429 日志徽标和重试建议。
