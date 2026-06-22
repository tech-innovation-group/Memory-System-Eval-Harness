# OpenViking LoCoMo 评测平台可参考的开源 Harness

日期：2026-06-06

目标：整理可参考的开源评测系统、数据集 harness、RAG/Memory 评测与可视化平台，用来改进 OpenViking + LoCoMo 评测平台的界面设计、功能设计和记忆后端适配器架构。

## 结论

OpenViking LoCoMo 平台不要只对标一个项目。更合理的是参考四层形态：

1. **Benchmark runner / 数据集注册层**：参考 lm-eval、OpenAI Evals、HELM、OpenCompass、LightEval。
2. **Agent trace / 过程可视化层**：参考 Inspect AI、Langfuse、Phoenix、Weave。
3. **RAG / Memory 质量评估层**：参考 Ragas、TruLens、DeepEval、MemoryAgentBench。
4. **回归测试 / 对比分析层**：参考 Promptfoo、DeepEval、OpenAI Evals。

对当前 OpenViking LoCoMo 平台最值得补齐的能力是：

- 数据集库：conversation / QA / category 浏览与过滤。
- 导入完整性：expected messages、submitted messages、commit_session、memory 文件、检索 smoke test。
- 每题详情：question、gold、agent response、judge、relevant memory、prompt/context、错误类型。
- Run 配置快照：模型、endpoint 标签、top-k、prompt mode、workspace、account、backend adapter、commit 策略。
- Run diff：同一批问题在不同 agent、不同 OpenViking 版本、不同参数下逐题比较。
- 失败样本聚类：无检索、证据不完整、有证据但 unknown、模型异常、Judge 分歧、时间题错误。
- 静态 HTML 报告：可脱敏分享，包含配置、结果、证据、错误分析、复现命令。

## 项目清单

| 项目 | 类型 | 开源链接 | 可借鉴功能 | 对 OpenViking LoCoMo 的启发 | 优先级 |
|---|---|---|---|---|---|
| EleutherAI lm-evaluation-harness | 通用 LLM benchmark runner | https://github.com/EleutherAI/lm-evaluation-harness | task registry、few-shot 配置、模型适配、统一运行 CLI | LoCoMo adapter 应有标准 task spec、样本过滤、模型/backend 配置快照 | 高 |
| OpenAI Evals | 自定义 eval registry | https://github.com/openai/evals | eval registry、私有 eval、model-graded eval、数据与逻辑分离 | 将 LoCoMo QA、Judge、错误重跑拆成可注册 eval | 高 |
| Stanford HELM | 透明评测与 Web 结果 | https://github.com/stanford-crfm/helm | 多维 metrics、run suite、结果 Web UI、prompt/response 检查 | 报告应展示准确率之外的耗时、token、成本、错误维度 | 中高 |
| OpenCompass | 大规模评测平台 | https://github.com/open-compass/opencompass | 配置系统、任务切分、执行调度、结果可视化 | 全量 LoCoMo 应支持分片、断点、并发、失败重试、汇总 | 高 |
| Hugging Face LightEval | 多后端 LLM 评测 | https://github.com/huggingface/lighteval | 多 backend、轻量任务定义、可复现实验 | 平台应把 OpenViking/EchoMem 做成 backend adapter | 中 |
| Inspect AI | Agent eval + log viewer | https://github.com/UKGovernmentBEIS/inspect_ai | task / solver / scorer、evaluation logs、Inspect View、样本状态 | 每道题应有 trace timeline：导入、检索、LLM、Judge、错误 | 高 |
| Promptfoo | prompt/RAG/agent 回归测试 | https://github.com/promptfoo/promptfoo | YAML eval、assertions、matrix compare、CI/CD、red team | 做“错题回归 suite”：上一轮错题、时间类题、OpenViking 错题 | 高 |
| Langfuse | LLM observability + evals | https://github.com/langfuse/langfuse | trace、prompt management、datasets、experiments、eval scores | UI 可参考 trace detail：每题按 span 展开检索、模型、Judge | 高 |
| Arize Phoenix | 开源 AI observability/evals | https://github.com/Arize-ai/phoenix | OpenTelemetry traces、RAG analysis、datasets & experiments | relevant memory 应做成可过滤表格，并和回答 span 关联 | 高 |
| W&B Weave | GenAI trace/eval toolkit | https://github.com/wandb/weave | function tracing、apples-to-apples eval、实验组织 | 后端每个步骤可记录成 op：import、search、answer、judge | 中 |
| Ragas | RAG/LLM app 评测 | https://github.com/explodinggradients/ragas | context precision/recall、faithfulness、test data generation | LoCoMo 不能只看最终准确率，还要看检索是否命中 gold evidence | 高 |
| TruLens | RAG triad 与追踪评测 | https://github.com/truera/trulens | feedback functions、RAG triad、tracking | 增加 retrieval relevance、groundedness、answer relevance 三类指标 | 中高 |
| DeepEval | Pytest 风格 LLM eval | https://github.com/confident-ai/deepeval | LLM-as-judge、RAG metrics、CI/CD、custom metrics | 把 LoCoMo QA 变成可反复跑的单元测试/回归测试 | 中高 |
| MemoryAgentBench | 记忆 agent benchmark | https://github.com/HUST-AI-HYZ/MemoryAgentBench | long-context、RAG agent、agentic memory 对比 | 当前平台应支持 long context baseline vs memory backend 对比 | 高 |
| AgentBench | LLM-as-Agent benchmark | https://github.com/THUDM/AgentBench | 多环境 agent 评测、交互式任务、环境隔离 | 未来评估 OpenViking agent 工具调用时参考任务/环境抽象 | 中 |

## 对当前平台的功能建议

### 1. 数据集库

参考 lm-eval、OpenAI Evals、OpenCompass。

应该支持：

- 数据集注册表：LoCoMo、LongMemEval、EvolvingEvents 等作为不同 dataset adapter。
- LoCoMo 专属浏览器：conversation 列表、消息数、QA 数、category 分布。
- Category 解释：不要只显示 C1/C2/C3/C4，要展示“单跳事实、多跳推理、时间推理、开放总结”等可读标签。
- Question suite：可保存“conv-30 全部题”“时间类问题”“上一轮错题”“OpenViking 错题”。

### 2. 导入完整性检查

参考 Inspect AI 的日志思想和 Langfuse/Phoenix 的 trace 思想。

导入阶段每个 conversation 至少记录：

- `expected_messages`
- `submitted_messages`
- `commit_session_called`
- `commit_session_status`
- `memory_store_path`
- `session_path`
- `history_files`
- `memory_files`
- `import_errors`
- `retrieval_smoke_query`
- `retrieval_smoke_result_count`

UI 上不要只显示“100%”，要显示阶段：

```text
Parse dataset -> Create session -> Add messages -> Commit session -> Verify files -> Smoke retrieval
```

### 3. 每题详情页

参考 Langfuse trace detail、Phoenix trace/span、Inspect log viewer。

每题详情建议拆成 6 块：

- 题目：question、gold、category、conversation、时间上下文。
- Agent Response：最终回答、是否 unknown、模型错误。
- Relevant Memory：uri、score、type、abstract/content、是否命中 gold。
- Context：发给模型的最终上下文、prompt 版本、截断信息。
- Judge：judge result、reasoning、judge model、judge prompt 版本。
- Trace：search、answer、judge 的耗时、token、retry、错误堆栈。

### 4. Run 配置快照

参考 HELM/OpenCompass 的 suite 配置和 Promptfoo 的声明式配置。

每个 run 应保存：

- dataset path 和 dataset hash
- selected conversations/questions
- backend adapter：openviking / echomemory / local reference
- workspace/account/user/agent
- answer model、judge model、embedding model
- top-k、score threshold、context budget
- prompt mode、是否允许 unknown、是否使用 fallback
- commit 策略、重试策略、并发数
- git/source revision 或手动版本号

报告里必须展示这些配置，否则准确率不可复现。

### 5. Run diff

参考 Promptfoo 的 matrix compare 和 Weave/Langfuse 的 experiment comparison。

需要支持：

- 选择两个 run 比较。
- 只比较相同 question id。
- 分桶展示：
  - both correct
  - both wrong
  - A correct / B wrong
  - A wrong / B correct
  - judge changed
  - model error only in one run
- 每个桶可以导出 CSV。

### 6. 失败样本聚类

参考 Ragas/TruLens 的 RAG 分层指标。

建议先做规则聚类：

- `retrieval_empty`：没有召回记忆。
- `retrieval_low_score`：有召回但 score 太低。
- `evidence_missing_gold`：召回内容没有包含 gold 相关证据。
- `evidence_present_answer_unknown`：有证据但模型回答 unknown。
- `time_reasoning_error`：时间类题答错。
- `entity_confusion`：人名、关系或 conversation 混淆。
- `model_api_error`：401、429、timeout、connection reset。
- `judge_pending_or_failed`：没有完成 judge。

### 7. 报告导出

参考 HELM Web UI、Inspect View、Phoenix/Langfuse 的 trace 报告。

HTML 报告应包含：

- Executive summary：accuracy、graded、pending、token、耗时、成本。
- Run config：可复现配置。
- Dataset summary：conversation、QA、category。
- Import integrity：导入完整性和检索 smoke test。
- Results table：每题结果、gold、answer、judge、evidence count。
- Error analysis：失败类型聚类。
- Diff section：如果选择多个 run，展示差异。
- Redaction note：说明不包含 API key。

## 界面设计参考

当前平台应更像“实验工作台”，而不是大而杂的 dashboard。

推荐布局：

- 顶部：当前 account、backend、workspace、OpenViking/EchoMem health、当前 active task。
- 左侧：数据集、LoCoMo 评测、对话人工评测、Runs、系统配置、README。
- 主区域：只展示当前任务相关控件，不混 LongMemEval 到 LoCoMo 页面。
- 右侧或下方：任务日志、trace、artifact 链接。

视觉上参考：

- Langfuse/Phoenix：trace detail、span timeline、过滤表格。
- Promptfoo：测试矩阵、diff、断言状态。
- Inspect AI：样本日志、任务状态、solver/scorer 分离。
- HELM：多维指标和透明 prompt/response 检查。

## 三阶段路线

### 1 天内

- 分类标签从 C1/C2/C3/C4 改成中文解释。
- LoCoMo 页面去掉无关 LongMemEval 文案。
- 数据集校验按钮放在数据集路径旁。
- 报告里展示 run config、model、token、耗时。
- 导入阶段显示 expected/submitted/commit/verify/smoke retrieval。

### 3 天内

- Question suite：保存时间类题、上一轮错题、OpenViking 错题。
- 每题详情页：evidence/context/judge/trace。
- Run diff：两个报告逐题对比。
- 失败样本规则聚类。
- HTML 报告支持脱敏分享。

### 1 周内

- 后端 adapter interface 稳定化：OpenViking、EchoMem 只实现统一接口。
- 前端拆到独立 web package。
- memory package 下稳定 adapters/base、adapters/openviking、adapters/echomemory 接口。
- 接入可选 OpenTelemetry/Langfuse/Phoenix 风格 trace schema。
- 增加 regression gate：关键 suite 跑不过时标红。

## 当前仓库落点

当前代码可对应到：

- 前端页面：`<repo-root>/web/static/index.html`
- 前端逻辑：`<repo-root>/web/static/app.js`
- 前端样式：`<repo-root>/web/static/styles.css`
- 后端入口：`<repo-root>/server.py`
- Adapter 基类：`<repo-root>/memory/adapters/base.py`
- Adapter 契约：`<repo-root>/memory/adapters/contract.py`
- Adapter Doctor：`<repo-root>/scripts/adapter_doctor.py`
- OpenViking adapter：`<repo-root>/memory/adapters/openviking/`
- EchoMemory adapter：`<repo-root>/memory/adapters/echomemory/`
- 报告导出：`<repo-root>/memory/report_export.py`
- LoCoMo 导入脚本：`<repo-root>/scripts/openviking_locomo_import.py`
- EchoMem 导入脚本：`<repo-root>/scripts/echomemory_locomo_import.py`

## 建议优先级

| 优先级 | 事项 | 原因 |
|---|---|---|
| P0 | 导入完整性 + 检索 smoke test | 没有这个，准确率无法解释 |
| P0 | 每题 evidence/context/judge 详情 | 能定位 unknown、错答、Judge 分歧 |
| P0 | Run config snapshot | 能复现结果 |
| P1 | Run diff | 能比较 OpenViking vs EchoMem vs 参数变化 |
| P1 | 失败样本聚类 | 能把问题从“看日志”变成“看原因分布” |
| P1 | Question suite | 能持续回归时间题、错题、关键 conv |
| P2 | Trace timeline | 体验和可解释性更接近 Langfuse/Phoenix |
| P2 | CI/regression gate | 给外部团队接入时做自动验收 |

## 官方链接

- EleutherAI lm-evaluation-harness: https://github.com/EleutherAI/lm-evaluation-harness
- OpenAI Evals: https://github.com/openai/evals
- Stanford HELM: https://github.com/stanford-crfm/helm
- OpenCompass: https://github.com/open-compass/opencompass
- Hugging Face LightEval: https://github.com/huggingface/lighteval
- Inspect AI: https://github.com/UKGovernmentBEIS/inspect_ai
- Promptfoo: https://github.com/promptfoo/promptfoo
- Langfuse: https://github.com/langfuse/langfuse
- Arize Phoenix: https://github.com/Arize-ai/phoenix
- W&B Weave: https://github.com/wandb/weave
- Ragas: https://github.com/explodinggradients/ragas
- TruLens: https://github.com/truera/trulens
- DeepEval: https://github.com/confident-ai/deepeval
- MemoryAgentBench: https://github.com/HUST-AI-HYZ/MemoryAgentBench
- AgentBench: https://github.com/THUDM/AgentBench
