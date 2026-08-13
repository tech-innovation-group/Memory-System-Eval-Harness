# LongMemEval 评测

## 评测流程

1. **逐题隔离导入**: 每题各自导入自己的 haystack_sessions (每题一个 EchoMem session, 包含所有 haystack 消息)
2. **逐题 QA**: search EchoMem 检索记忆 -> 组装 prompt -> LLM 生成回答 (仅检索不写入)
3. **官方 accuracy 评测**: 按题型用 LLM judge yes/no 判定回答正确性

题型: `single-session-user`, `single-session-assistant`, `multi-session`,
`temporal-reasoning`, `knowledge-update`, `single-session-preference`,
`single-session-abstention`

## 使用方法

```bash
# 不指定 --dataset 则自动查找/下载
python benchmarks/longmemeval/run_eval.py \
  --llm-api-key YOUR_API_KEY

# 指定数据集路径
python benchmarks/longmemeval/run_eval.py \
  --dataset /path/to/longmemeval.json \
  --llm-api-key YOUR_API_KEY

# 限制数量
python benchmarks/longmemeval/run_eval.py \
  --dataset /path/to/longmemeval.json \
  --questions 20 \
  --concurrency 8 \
  --llm-api-key YOUR_API_KEY

# 使用独立 judge 模型
python benchmarks/longmemeval/run_eval.py \
  --dataset /path/to/longmemeval.json \
  --judge-model gpt-4o \
  --judge-api-key YOUR_JUDGE_KEY \
  --judge-base-url https://api.openai.com/v1 \
  --llm-api-key YOUR_API_KEY

# 指定题目 ID
python benchmarks/longmemeval/run_eval.py \
  --dataset /path/to/longmemeval.json \
  --question-ids q1,q2,q3 \
  --llm-api-key YOUR_API_KEY

# 分成 8 个隔离 shard，最多并行 4 个进程，完成后自动合并
python benchmarks/longmemeval/run_eval.py \
  --dataset /path/to/longmemeval.json \
  --parallel-shards 8 \
  --parallel-workers 4 \
  --llm-api-key YOUR_API_KEY

# 只生成分片命令和 manifest，不启动评测
python benchmarks/longmemeval/run_eval.py \
  --dataset /path/to/longmemeval.json \
  --parallel-shards 8 \
  --parallel-dry-run \
  --llm-api-key YOUR_API_KEY
```

## 参数说明

### 必填参数
| 参数 | 说明 |
|---|---|
| `--llm-api-key` | LLM API Key |

### 数据集参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--dataset` | (自动) | LongMemEval JSON 数据集路径。不指定时自动在 `benchmarks/longmemeval/data/` 查找 `longmemeval_s_cleaned.json`, 找不到则从 HuggingFace 下载 |
| `--sample` | `all` | 筛选 sample |
| `--questions` | `0` | 限制 QA 数量 (0=全部) |
| `--question-ids` | (空) | 逗号分隔的 question/native/sample ID |
| `--random-count` | `0` | 从已筛选题目中稳定随机抽取数量 |
| `--random-seed` | `30` | 随机抽样 seed |
| `--agent-plugin` | `vikingbot` | QA 阶段使用的 agent 插件名，见 `plugins/` 目录 |

### 评测参数 (benchmark 自身)
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--concurrency` | `4` | QA 并发数 |
| `--checkpoint-interval` | `10` | 每完成 N 题写一次 `qa_results.checkpoint.csv`；0 表示关闭 |
| `--resume` | (空) | **统一续跑**：复用先前运行身份，跳过已完成 import batch，恢复健康 QA 答案，复用匹配的 Judge 判定；只跑缺失/失败部分。summary 指标对合并后的整轮累计。不支持与 `--parallel-shards` 同用 |
| `--out-dir` | `results` | 结果目录 |
| `--allow-diagnostics` | false | 导入未完成仍继续，仅限诊断 |

### 并行参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--parallel-shards` | `1` | 按稳定 round-robin 切分为多少个隔离 CLI 进程 |
| `--parallel-workers` | `2` | 同时运行的最大 shard 进程数 |
| `--parallel-dry-run` | false | 只写 `parallel_manifest.json`，不执行 shard |

### Judge 参数
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--judge-model` | (同 `--llm-model`) | Judge LLM 模型名 |
| `--judge-api-key` | (同 `--llm-api-key`) | Judge API Key |
| `--judge-base-url` | (同 `--llm-base-url`) | Judge base URL |

### 插件参数 (LLM / QA / 记忆后端 / 插件特有)

LongMemEval 默认使用 `vikingbot` 插件。LLM 凭据、QA 检索行为、记忆后端
连接和 VikingBot 特有参数均由插件声明，不由 benchmark `run_eval` 直接定义。

benchmark 只定义数据集参数和评测基础设施参数 (`--concurrency`、`--checkpoint-interval`、
`--resume`、`--out-dir`、`--allow-diagnostics`)。切换 `--agent-plugin` 后可用参数会变化，
使用 `--help` 查看。

参数归属的完整设计说明见 `benchmarks/doc/设计意图.md`。
参数明细表见 `benchmarks/locomo/docs/usage.md`（使用相同默认插件时参数一致）。

## 输出文件

`benchmarks/longmemeval/results/<timestamp>/` 下:
- `config.json`, `run.log` - 配置和日志
- `import_results.csv` - 导入结果 (含 sessions 数量)
- `qa_results.csv` - QA 结果
- `qa_results.csv` 中的 `retrieval_items_json` - 原始检索证据及后端 metadata
- `eval_results.csv` - Judge 结果 (question_id, question_type, correct)
- `summary.json` - 汇总 (accuracy, per_type accuracy, token usage)

并行运行额外生成 `parallel_manifest.json`、每个 shard 的 `runner.log`、
`parallel_summary.json`，以及 `merged/` 下去重合并后的 CSV 和
`summary.json`。`recovery.py` 提供失败/缺失题识别、成功重试替换和稳定
CSV 合并能力，供分片合并及后续恢复命令复用。

可直接运行恢复工具：

```bash
python benchmarks/longmemeval/recovery.py \
  --qa /path/to/run/qa_results.csv \
  --dataset /path/to/longmemeval.json \
  --out-dir /path/to/recovery
```
