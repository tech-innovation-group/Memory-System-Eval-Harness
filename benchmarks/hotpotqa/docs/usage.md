# HotpotQA 评测

## 评测流程

1. **导入记忆** (两种模式):
   - `per_question` (默认): 每题各自导入自己的 context passages 到独立 EchoMem session
   - `global`: 所有题的 passages 合并导入到一个共享 EchoMem session
2. **逐题 QA**: search EchoMem 检索记忆 -> 组装 prompt -> LLM 生成回答
3. **官方指标**: answer、supporting-fact 和 joint precision/recall/F1/EM (无需 LLM judge)

## 使用方法

```bash
# 不指定 --dataset 则自动查找/下载
python benchmarks/hotpotqa/run_eval.py \
  --import-mode per_question \
  --llm-api-key YOUR_API_KEY

# 指定数据集路径
python benchmarks/hotpotqa/run_eval.py \
  --dataset /path/to/hotpotqa.json \
  --import-mode per_question \
  --llm-api-key YOUR_API_KEY

# global 模式 (共享 session)
python benchmarks/hotpotqa/run_eval.py \
  --dataset /path/to/hotpotqa.json \
  --import-mode global \
  --concurrency 8 \
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
| `--dataset` | (自动) | HotpotQA JSON 数据集路径。不指定时自动在 `benchmarks/hotpotqa/data/` 查找 `hotpot_dev_distractor_v1.json`, 找不到则从远程下载 |
| `--sample` | `all` | 筛选 sample |
| `--questions` | `0` | 限制 QA 数量 (0=全部) |
| `--question-ids` | (空) | 逗号分隔的 question/native/sample ID，在 `--questions` 前应用 |
| `--import-mode` | `per_question` | 导入模式: `per_question` 或 `global` |
| `--agent-plugin` | `vikingbot` | QA 阶段使用的 agent 插件名，见 `plugins/` 目录 |

### 评测参数 (benchmark 自身)
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--concurrency` | `4` | QA 并发数 |
| `--checkpoint-interval` | `10` | 每完成 N 题写一次 `qa_results.checkpoint.csv`；0 表示关闭 |
| `--resume` | (空) | **统一续跑**：复用先前运行身份，跳过已完成 import batch（只补中断/缺失的），恢复健康 QA 答案；只跑缺失/失败部分。summary 指标对合并后的整轮累计 |
| `--out-dir` | `results` | 结果目录 |
| `--allow-diagnostics` | false | 导入未完成仍继续，仅限诊断 |

### 插件参数 (LLM / QA / 记忆后端 / 插件特有)

HotpotQA 默认使用 `vikingbot` 插件。LLM 凭据、QA 检索行为、记忆后端连接
和 VikingBot 特有参数均由插件声明，不由 benchmark `run_eval` 直接定义。

benchmark 只定义数据集参数、评测基础设施参数 (`--concurrency`、`--checkpoint-interval`、
`--resume`、`--out-dir`、`--allow-diagnostics`) 和本数据集特有的 `--import-mode`。切换
`--agent-plugin` 后可用参数会变化，使用 `--help` 查看。

参数归属的完整设计说明见 `benchmarks/doc/设计意图.md`。
参数明细表见 `benchmarks/locomo/docs/usage.md`（使用相同默认插件时参数一致）。

HotpotQA 不需要 Judge 参数。每次运行创建独立身份并注入记忆，不自动删除
身份。QA 结果保留 `retrieval_items_json`，用于从真实检索证据推导
supporting-fact 和 joint 指标。每条证据同时保留 EchoMemory 返回的原始
metadata；若存在显式 `hotpotqa_title` / `hotpotqa_sent_id`，评测优先使用，
仅在缺失时从文本推断。

## 输出文件

`benchmarks/hotpotqa/results/<timestamp>/` 下:
- `config.json`, `run.log` - 配置和日志
- `import_results.csv` - 导入结果
- `qa_results.csv` - QA 结果，包含 `retrieval_items_json`
- `eval_results.csv` - answer/supporting-fact/joint precision、recall、F1、EM
- `summary.json` - 汇总 answer/supporting-fact/joint 指标和 token usage

## 失败与缺失题恢复

```bash
python benchmarks/hotpotqa/recovery.py \
  --qa /path/to/run/qa_results.csv \
  --dataset /path/to/hotpotqa.json \
  --mode failed-or-missing \
  --out-dir /path/to/recovery
```

工具会生成带 `--resume-qa` 的重跑命令。已有 retry CSV 时可
增加 `--retry-qa /path/to/retry/qa_results.csv`，仅用健康结果替换失败行。
