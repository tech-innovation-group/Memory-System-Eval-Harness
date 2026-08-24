# HotpotQA 评测

## 评测流程

1. **导入记忆** (三种模式):
   - `per_question` (默认): 每题各自导入自己的 context passages 到独立 EchoMem session
   - `global`: 所有题的 passages 合并导入到一个共享 EchoMem session
   - `documents`: 所有所选题的 context passage 去重后作为**文档资源**注入记忆后端
     （EchoMem `POST /api/resources` 或 OpenViking `POST /api/v1/resources`，注入后
     自动异步索引，等待全部就绪再进入 QA），QA 走资源检索（EchoMem
     `/api/resources/search` / OpenViking `viking://user/resources/`）—— 适合
     「HotpotQA 是文档 QA」的评测
2. **逐题 QA**: 检索记忆（对话模式 search / 文档模式 resource search）-> 组装 prompt -> LLM 生成回答
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

# documents 模式 (文档资源语料, 快速 RAG 评测; 需后端启用资源索引:
# EchoMem resource_engine 或 OpenViking 19080)
python benchmarks/hotpotqa/run_eval.py \
  --dataset /path/to/hotpotqa.json \
  --import-mode documents \
  --agent-plugin vikingbot \
  --memory-backend echomem \
  --questions 100 \
  --concurrency 8 \
  --llm-api-key YOUR_API_KEY
```

> `documents` 模式要求插件提供 `path_title_map`（文档资源检索能力）——默认的
> `vikingbot` 与 `echomem_mcp` 均已支持。`vikingbot` 可配合
> `--memory-backend echomem`（`/api/resources`）或 `openviking`
> （`viking://user/resources/`）。`--questions N` 会把语料限定为这 N 题的文档，
> 语料规模随采样伸缩，适合快速跑通。
>
> **documents 模式 + 工具调用**：`vikingbot` 的 `--tools`（默认开）与 `echomem_mcp` 的
> `--tool-calling`（默认开）让 agent 在语料上跑多轮工具检索（`memory_search`/
> `memory_read_many`，检索指向 `viking://user/resources/` 或 EchoMem `/api/resources`
> 语料空间），完整工具调用链进入 `agent_traces/` 与 `tool_audits`；关闭（`--no-tools`
> / `--no-tool-calling`）回退为单次 RAG（top-N 检索 + 一次 LLM 调用，镜像 OpenViking
> KBQA benchmark 口径）。


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
| `--import-mode` | `per_question` | 导入模式: `per_question`、`global` 或 `documents`（文档资源语料；需插件支持 `path_title_map`，默认 `vikingbot`/`echomem_mcp` 均可） |
| `--agent-plugin` | `vikingbot` | QA 阶段使用的 agent 插件名，见 `plugins/` 目录 |

### 评测参数 (benchmark 自身)
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--concurrency` | `4` | QA 并发数 |
| `--checkpoint-interval` | `10` | 每完成 N 题写一次 `qa_results.checkpoint.csv`；0 表示关闭 |
| `--resume` | (空) | **统一续跑**：复用先前运行身份，跳过已完成 import batch（只补中断/缺失的），恢复健康 QA 答案；只跑缺失/失败部分。summary 指标对合并后的整轮累计 |
| `--reuse-memory-from` | (空) | 复用先前运行的**记忆**（身份 + 已导入语料），但**全新重跑全部 QA**。documents 模式不重新注入语料，仅从数据集重建 path→title 映射。适合改完记忆算法/LLM 后在同一语料上重测 |
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
- `diagnosis.json` - 失败分类、检索覆盖率、可重试/缺失/重复题目 ID
- `retrieval_traces.jsonl` - 每题检索内容和失败归因 trace（含官方指标与 retrieval items）
- `agent_traces/*.json` - 每题 agent trace（插件提供时；无 trace 时无此文件）。
  resume 复用的题会从源运行目录复制 trace，保证目录与从 0 运行等价
- `tool_audits.jsonl/.json` - 工具调用审计（插件提供 `tool_audit` 时；否则为空文件）。
  resume 恢复 trace 后会全量重写，复用题的审计同样存在
- `summary.json` - 汇总 answer/supporting-fact/joint 指标、token usage、trace 派生字段
  （`tool_call_total`、`avg_iterations`、`served_model_ids`、`tool_protocol_sha256`、
  `messages_jsonl_read_*`）和 diagnosis 摘要

失败诊断可用 `benchmarks/hotpotqa/diagnosis.py` 对历史运行目录重新生成
（`--questions` 需与原运行一致，保证题集判定正确）：

```bash
python benchmarks/hotpotqa/diagnosis.py \
  --qa-results path/to/qa_results.csv \
  --eval-results path/to/eval_results.csv \
  --dataset path/to/hotpotqa.json \
  --questions 100 \
  --out-dir path/to/run_dir
```

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
