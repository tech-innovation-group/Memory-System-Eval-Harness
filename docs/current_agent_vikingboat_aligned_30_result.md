# 当前 Agent 对齐 VikingBoat 后 30 题 LoCoMo 测试结果

更新时间：2026-06-01

## 运行产物

- 方案文档：`/Users/chx/locomo-eval-web/docs/current_agent_vikingboat_alignment_plan.md`
- Run 目录：`/Users/chx/locomo-eval-web/runs/current_agent_vikingboat_aligned_30_20260601_160050`
- QA CSV：`/Users/chx/locomo-eval-web/runs/current_agent_vikingboat_aligned_30_20260601_160050/openviking_memory_qa_results.csv`
- QA Summary：`/Users/chx/locomo-eval-web/runs/current_agent_vikingboat_aligned_30_20260601_160050/summary.json`
- Judge Summary：`/Users/chx/locomo-eval-web/runs/current_agent_vikingboat_aligned_30_20260601_160050/judge_summary.json`

## 上下文工程配置

- Dataset：LoCoMo `conv-30`
- 题数：30
- OpenViking：`http://127.0.0.1:1933`
- 记忆 workspace：`/Users/chx/openviking_workspace_locomo_20260601_014238_5b2c49`
- Account/User/Agent：`default/default/default`
- 检索：OpenViking `/api/v1/search/find`
- Top-K：30
- Score threshold：0.1
- 时间上下文：LoCoMo `query_time` 存在时注入 `Current date`
- 模型调用失败重试：5 次
- Judge 调用失败重试：5 次

## 前置记忆导入状态

导入 summary：`/Users/chx/locomo-eval-web/runs/openviking_import_20260601_014238_227a10/openviking_import/openviking_import_summary.json`

- 状态：`OPENVIKING_IMPORT_DONE`
- Sample：`conv-30`
- 对话消息：`369 / 369`
- Session 数：`19`
- 完整性：`complete`
- Commit 后 pending：`0`
- Archive complete：`true`

## 模型健康结果

30 题 QA summary：

- `model_ok_count`: 30
- `model_failed_count`: 0
- `model_rate_limited_count`: 0
- `rows_with_model_retries`: 0
- `model_retry_total`: 0
- `retrieval_ok_count`: 30
- `retrieval_empty_count`: 0
- `answer_ok_count`: 27
- `answer_empty_or_unknown_count`: 3
- `avg_retrieval_count`: 30.0

结论：本轮没有限流，没有模型调用失败，也没有触发 retry。OpenViking 每题都正常召回记忆。3 题模型选择回答 `unknown`，说明模型调用正常，但上下文证据或 prompt 判定不够支持答案。

## Judge 结果

- 题数：30
- 已 Judge：30
- Correct：19
- Wrong：11
- Accuracy：63.33%

## 需要重点分析的 3 个 unknown

| Question ID | 问题 | Gold | Response | Health |
| --- | --- | --- | --- | --- |
| `conv-30_qa10` | When did Gina team up with a local artist for some cool designs? | February, 2023 | unknown | answer_empty |
| `conv-30_qa77` | What did Gina make a limited edition line of? | Hoodies | unknown | answer_empty |
| `conv-30_qa58` | Why did Jon shut down his bank account? | for his business | unknown | answer_empty |

## 初步问题判断

1. Top-K=30 和 score threshold=0.1 已让召回覆盖面与 VikingBoat 接近，但平均 prompt token 变高，本轮 answer prompt tokens 总计 `353235`。
2. 11 个 wrong 中，有 3 个是 `unknown`，属于模型保守拒答或证据未命中关键事实。
3. 其余 wrong 多数是语义不够贴近 gold，或者回答给了更宽泛/相邻事实，需要进一步做 evidence rerank 或针对时间/实体问题增加 query rewrite。
4. 本轮没有限流，因此 5 次 retry 机制没有被触发，但字段和日志机制已具备。

## 下一步建议

1. 对 11 个 wrong 做 evidence 检查：确认正确证据是否在 top-30 内。
2. 对 `unknown` 题增加二次检索 query rewrite，例如加入核心实体、月份、物品词。
3. 对时间类问题增加 date normalization 和 “answer exact date/month if present” 的强规则。
4. 在 Web Runs 分析中显示 `model_status`、`model_retry_count`、`health_status`，方便发现限流和模型异常。

