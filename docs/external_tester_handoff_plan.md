# EchoMem + LoCoMo 外部测试方案

这份方案给外部测试同学使用。目标是把他们自己的 EchoMem 记忆系统接入当前评测平台，跑通 LoCoMo 数据导入、记忆检索问答、Judge 打分和 HTML 报告。

当前交付只围绕两条记忆后端路线：

- `EchoMem / EchoMemory`：外部同学主要要接入和验证的记忆系统。
- `OpenViking`：平台内置基线和对照后端，用来验证 LoCoMo 流程、workspace/account 隔离和报告结构。

不要把 embedding key、大模型 key、Judge key 写进 README、截图、日志、报告或压缩包。所有密钥都只放在测试者自己的 `.env.local` 或 Web 密码输入框里。

## 1. 交付包内容

核心文件：

- `server.py`：本地 Web 评测平台后端
- `web/static/`：Web 页面和前端交互
- `memory/`：评测平台后端包
- `memory/adapters/contract.py`：统一记忆后端契约
- `memory/adapters/echomemory/`：EchoMem/EchoMemory 适配入口
- `scripts/echomemory_locomo_import.py`：LoCoMo 对话导入 EchoMem
- `scripts/echomemory_memory_qa.py`：基于 EchoMem 检索结果运行 LoCoMo QA
- `scripts/local_judge.py`：Judge
- `scripts/generate_html_report.py`：生成静态 HTML 报告
- `dataset/locomo10.json`：内置 LoCoMo 10 conversation smoke 数据
- `dataset/manifest.json`：数据集注册表
- `env.echomem.example`：EchoMem 接入环境变量模板
- `preflight.sh` / `start.sh`：预检和启动脚本

不要交付：

- `.env.local`
- `judge.conf`
- `runs/`
- OpenViking workspace
- EchoMem workspace
- 真实 runtime yaml
- 任何 `sk-...`、Bearer token、API key 或包含密钥的截图

## 2. 输入到输出

```text
LoCoMo JSON
  -> Web Harness
  -> EchoMemory adapter
  -> echomemory_locomo_import.py
  -> EchoMem SDK: create_session / add_message / commit_session
  -> EchoMem workspace/account/user/agent memory store
  -> echomemory_memory_qa.py
  -> EchoMem SDK: find / search
  -> Answer LLM
  -> QA CSV + relevant memory + token usage
  -> local_judge.py
  -> Judge result
  -> report.html
```

平台只负责任务编排、进度展示、日志保存、结果汇总和报告导出。EchoMem 内部的图记忆、向量索引、抽取、融合召回和记忆落盘逻辑仍由 EchoMem 自己实现。

## 3. EchoMem 需要保持的接口

如果外部同学修改了 EchoMem，例如加入图记忆模块，建议保持下面的平台调用契约不变：

- `from echomem.runtime.runtime import open_runtime`
- `from echomem.protocol.local_sdk.sdk import EchoMemSDK`
- `await sdk.create_session(...)`
- `await sdk.add_message(...)`
- `await sdk.commit_session(...)`
- `await sdk.find(query, ctx=...)`
- `await sdk.search(query, ctx=..., budget={"max_results": top_k})`

检索 evidence 建议至少返回：

- `content`
- `uri` 或 `source_uri`
- `score` 或 `confidence`
- `memory_type`
- `evidence_uri`
- `trace`

图记忆可以作为 EchoMem 内部实现，只要最终 evidence 仍能映射到上述结构，Web 报告就能继续展示证据、context 和失败归因。

## 4. 本机准备

测试者机器需要：

- Python 3.11+
- EchoMemory `version_0.0.5` 源码目录，例如 `/absolute/path/to/echo_memory`
- EchoMem 自己的依赖环境
- 可用的 embedding 服务
- 可用的 answer LLM 服务
- 可用的 Judge LLM 服务
- LoCoMo JSON 数据。可以先用 `dataset/locomo10.json` 做 smoke test，再换成全量数据

如果使用官方 release tag：

```bash
git clone -b version_0.0.5 https://github.com/tech-innovation-group/echo_memory.git /absolute/path/to/echo_memory
```

## 5. 配置

进入平台目录：

```bash
cd /absolute/path/to/locomo-eval-web
cp env.echomem.example .env.local
```

编辑 `.env.local`。下面只放占位符，不能把真实 key 发给别人：

```bash
export LOCOMO_EVAL_HOST=127.0.0.1
export LOCOMO_EVAL_PORT=19181
export LOCOMO_DATA=/absolute/path/to/locomo10.json

export ECHOMEM_ROOT=/absolute/path/to/EchoMem
export ECHOMEM_WORKSPACE=/absolute/path/to/echomem_workspace
export ECHOMEM_ACCOUNT=locomo_eval_account
export ECHOMEM_USER_ID=locomo_user
export ECHOMEM_AGENT_ID=locomo_agent

export DASHSCOPE_API_KEY=<your-embedding-api-key>
export DASHSCOPE_BASE_URL=https://<embedding-provider-host>/compatible-mode/v1

export ECHOMEM_CHAT_PROVIDER=openai-compatible
export ECHOMEM_CHAT_MODEL=gpt-5.5
export ECHOMEM_CHAT_API_KEY=<your-answer-api-key>
export ECHOMEM_CHAT_BASE_URL=https://<chat-provider-host>/v1

export JUDGE_BASE_URL=https://<judge-provider-host>/v1
export JUDGE_MODEL=gpt-5.5
export JUDGE_TOKEN=<your-judge-api-key>
```

## 6. 启动

```bash
source .env.local
./preflight.sh
./start.sh
```

浏览器打开：

```text
http://127.0.0.1:19181/
```

如果端口被占用：

```bash
export LOCOMO_EVAL_PORT=19182
./start.sh
```

## 7. Web 测试流程

推荐先跑 `conv-30` 的 5 到 10 道题做 smoke test。

1. 打开左侧 `LoCoMo评测`
2. 选择或填写 LoCoMo JSON
3. 点击 `校验数据集`
4. 在 `系统配置` 中选择 `EchoMem / EchoMemory`
5. 填写 EchoMem root、workspace、account、user、agent
6. 回到 `LoCoMo评测`，选择要导入的 conversation，例如 `conv-30`
7. 点击导入，观察日志中是否出现 `create_session`、`add_message`、`commit_session`
8. 导入完成后检查完整性摘要
9. 选择部分 QA 或随机 10 题运行 QA
10. QA 完成后运行 Judge
11. 生成 HTML 报告，查看 accuracy、token、耗时、evidence、context 和失败样本

导入完整性重点看：

- `expected_messages`
- `submitted_messages`
- `committed session`
- `integrity status`
- workspace、account、user、agent 是否和 QA 阶段一致

## 8. 命令行 Smoke Test

导入 `conv-30`：

```bash
source .env.local

python3 scripts/echomemory_locomo_import.py \
  --dataset "$LOCOMO_DATA" \
  --out-dir runs/echomemory_smoke/import \
  --echomem-root "$ECHOMEM_ROOT" \
  --workspace "$ECHOMEM_WORKSPACE" \
  --account "$ECHOMEM_ACCOUNT" \
  --user-id "$ECHOMEM_USER_ID" \
  --agent-id "$ECHOMEM_AGENT_ID" \
  --sample conv-30 \
  --session-mode locomo
```

跑 10 道 QA：

```bash
python3 scripts/echomemory_memory_qa.py \
  --dataset "$LOCOMO_DATA" \
  --out-dir runs/echomemory_smoke/qa10 \
  --echomem-root "$ECHOMEM_ROOT" \
  --workspace "$ECHOMEM_WORKSPACE" \
  --account "$ECHOMEM_ACCOUNT" \
  --user-id "$ECHOMEM_USER_ID" \
  --agent-id "$ECHOMEM_AGENT_ID" \
  --sample conv-30 \
  --random-count 10 \
  --top-k 30 \
  --score-threshold 0.1 \
  --memory-budget-chars 6000 \
  --retrieval-mode both \
  --answer-base-url "$ECHOMEM_CHAT_BASE_URL" \
  --answer-model "$ECHOMEM_CHAT_MODEL" \
  --answer-token "$ECHOMEM_CHAT_API_KEY"
```

跑 Judge：

```bash
python3 scripts/local_judge.py \
  --input runs/echomemory_smoke/qa10/echomemory_memory_qa_results.csv \
  --base-url "$JUDGE_BASE_URL" \
  --model "$JUDGE_MODEL" \
  --token "$JUDGE_TOKEN"
```

## 9. 关键产物

导入阶段：

- `runs/<task_id>/echomemory_import/echomemory_import_summary.json`
- `runs/<task_id>/echomemory_import/<conv_id>_messages.json`
- `runs/<task_id>/echomemory_import/echomem.runtime.yaml`

QA 阶段：

- `runs/<task_id>/echomemory_qa/echomemory_memory_qa_results.csv`
- `runs/<task_id>/echomemory_qa/summary.json`
- `runs/<task_id>/echomemory_qa/relevant_memory.json`

Judge 阶段：

- QA CSV 中的 `result`、`reasoning`
- `judge_summary.json`

报告阶段：

- `report.md`
- `report.html`

这些文件可能包含本机路径、模型输出或业务数据。外发前必须脱敏。

## 10. 常见问题

`校验数据集为什么很快完成？`

正常。校验只读 JSON 结构和题目统计，不调用模型，也不写记忆。

`导入成功但检索为空？`

优先检查 EchoMem workspace、account、user、agent 是否和 QA 阶段完全一致。任一项不同，都会变成另一个干净记忆空间。

`answer 是 unknown 但 evidence 有内容？`

先看 CSV 的 `health_status`、`model_error_kind`、`retrieval_count`、`context_preview`。常见原因是 prompt 约束过严、证据被截断、模型空响应、限流重试失败，或者 evidence 相关但没有直接答案。

`准确率为空或待 Judge？`

QA 只生成答案。正式准确率需要 Judge 后才有。

`模型限流或连接中断？`

降低并发、提高 timeout，确认 base URL、模型名和 token 是当前 provider 支持的配置。

## 11. 外发前安全检查

发送给别人前运行：

```bash
./preflight.sh
rg -n "api_key|token|Authorization|Bearer|sk-" .
```

允许出现：

- README 中的占位符，例如 `<your-api-key>`
- 代码变量名，例如 `api_key`

不允许出现：

- 真实密钥
- 未脱敏日志
- `.env.local`
- `judge.conf`
- 原始 `runs/`
- EchoMem workspace
- OpenViking workspace

外部测试者回传结果时，只需要给：

- 平台版本或 commit
- EchoMem fork/branch/commit
- LoCoMo 数据范围
- EchoMem workspace/account/user/agent 是否一致
- 导入 summary
- QA CSV
- Judge summary
- HTML report
- 已脱敏日志
