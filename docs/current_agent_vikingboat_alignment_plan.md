# 当前 Agent 对齐 VikingBoat 上下文工程方案

更新时间：2026-06-01

## 目标

让当前 LoCoMo/OpenViking 评测 agent 尽量贴近 VikingBoat/VikingBot 的上下文工程，并在 30 道 LoCoMo 题测试中记录每题是否正常完成：

- OpenViking 是否能召回/提取到可用记忆
- 回答模型是否正常返回答案
- 是否出现限流、超时或 API 错误
- 模型调用失败时最多重试 5 次
- 每题输出 evidence、response、token、retry、health 状态

## 对齐点

| 模块 | VikingBoat/VikingBot 形态 | 当前 agent 对齐方案 |
| --- | --- | --- |
| 记忆来源 | 优先 OpenViking context database | 所有 QA 先调用 OpenViking `/api/v1/search/find` |
| 检索量 | `limit=30` | 默认 `top-k=30` |
| 分数过滤 | `min_score=0.1` | `score_threshold=0.1` |
| 时间上下文 | `Current date: ...` 注入问题 | LoCoMo `query_time` 存在时注入 |
| Prompt 结构 | system 身份 + user memory context | system 负责 Memory QA 规则，user 包含 Question + Retrieved memories |
| 证据格式 | openviking_search 结果作为上下文 | `### Retrieved memories`，按 score 排序编号 |
| 答案要求 | 直接、简洁、不要编造 | 不足时回答 `unknown`，要求使用 exact dates/facts |
| 工具能力差距 | VikingBoat 可多轮调用工具 | 当前批量 agent 是单轮检索 + LLM；用 top-30 和文件 snippet 缩小差距 |

## 模型健康检查

30 题测试脚本：`<repo-root>/scripts/openviking_memory_qa.py`

每行 CSV 新增/保留以下字段：

- `retrieval_status`: `ok` / `empty` / `unknown`
- `retrieval_count`: OpenViking 召回数量
- `retrieval_error`: 检索异常文本
- `model_status`: `ok` / `failed`
- `model_retry_count`: 本题模型调用重试次数
- `model_error_kind`: `rate_limited` / `timeout` / `api_error` / `no_answer_token`
- `model_error`: 模型调用失败原因
- `answer_status`: `ok` / `empty_or_unknown` / `failed`
- `health_status`: 综合健康状态

summary 新增汇总：

- `model_retries_configured`
- `model_ok_count`
- `model_failed_count`
- `model_rate_limited_count`
- `rows_with_model_retries`
- `model_retry_total`
- `retrieval_ok_count`
- `retrieval_empty_count`
- `answer_ok_count`
- `answer_empty_or_unknown_count`
- `health_counts`

## 重试策略

模型调用失败时最多重试 5 次，即最多 1 次初始调用 + 5 次 retry。

错误分类：

- 包含 `429`、`rate limit`、`too many requests`、`quota`、`throttle`、`限流`、`频率`：记为 `rate_limited`
- 包含 `timeout`、`timed out`、`temporarily unavailable`、`connection reset`：记为 `timeout`
- 其他异常：记为 `api_error`

限流时退避等待更长；其他错误使用较短指数退避。所有 retry 都会写入运行日志，格式为：

```text
[model] retry=1/5 kind=rate_limited error=...
```

## 30 题测试命令形态

实际运行时从本地 `judge.conf` 读取模型配置，不在命令或报告中暴露 token。

```bash
python3 scripts/openviking_memory_qa.py \
  --dataset dataset/locomo10.json \
  --out-dir runs/current_agent_vikingboat_aligned_30_<timestamp> \
  --sample conv-30 \
  --random-count 30 \
  --random-seed 30 \
  --openviking-url <OPENVIKING_BASE_URL> \
  --workspace <openviking-workspace> \
  --account default \
  --user-id default \
  --agent-id default \
  --top-k 30 \
  --model-retries 5
```

## 已确认前置条件

- `conv-30` 已导入 OpenViking
- 导入 summary 显示 `OPENVIKING_IMPORT_DONE`
- `expected_messages=369`
- `submitted_messages=369`
- `pending_message_count_after_commit=0`
- `archive_complete_after_commit=true`
- OpenViking 服务：`<OPENVIKING_BASE_URL>/health`

## 产出物

- 对齐方案：本文件
- 30 题 CSV：`runs/current_agent_vikingboat_aligned_30_*/openviking_memory_qa_results.csv`
- 30 题 summary：`runs/current_agent_vikingboat_aligned_30_*/summary.json`
- Web 手动对话上下文检查器：`/api/agent/context` + Agent 对话页右栏

