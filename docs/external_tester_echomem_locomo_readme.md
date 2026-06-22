# Memory Eval Harness + EchoMem LoCoMo Handoff

这份 README 给外部测试同学使用：拿到测试平台包后，把自己的 EchoMem 记忆系统接进来，跑 LoCoMo 导入、问答、Judge 和 HTML 报告。

交付包不包含任何 embedding、大模型、Judge API key。所有 key 都必须由测试方在本机环境变量或 Web 页面里填写。

## 1. 交付包包含什么

- Web 评测平台：`server.py`、`web/static/`
- 评测后端包：`memory/`
- 评测脚本：`scripts/`
- 内置 LoCoMo 10-sample 数据：`dataset/locomo10.json`
- 数据集注册表：`dataset/manifest.json`
- 启动和预检脚本：`start.sh`、`preflight.sh`
- EchoMem 接入模板：`env.echomem.example`
- 本 README

交付包刻意不包含：

- `runs/` 历史评测结果
- `workspace/` 或任何 OpenViking/EchoMem 本地记忆目录
- `judge.conf`、`*.conf`、`*.log`
- 任何真实 API key
- 之前生成的临时 HTML 分析报告

## 2. 架构关系

```text
LoCoMo JSON
  -> Web Harness
  -> EchoMemory adapter
  -> scripts/echomemory_locomo_import.py
  -> EchoMem local SDK create_session/add_message/commit_session
  -> EchoMem workspace/account memory store
  -> scripts/echomemory_memory_qa.py
  -> EchoMem sdk.find/sdk.search
  -> answer model
  -> CSV + relevant_memory + token usage
  -> scripts/local_judge.py
  -> HTML report
```

平台只做任务编排、进度展示、结果汇总和报告导出。EchoMem 的图记忆、向量索引、混合召回、抽取逻辑都留在 EchoMem 后端内部实现。

## 3. 本机准备

需要：

- Python 3.11+
- EchoMemory `version_0.0.5` 源码目录，例如 `/path/to/echo_memory`
- EchoMem 自己需要的依赖或虚拟环境
- 可用的 embedding API、answer LLM API、Judge LLM API
- LoCoMo JSON。包内已有 `dataset/locomo10.json`，也可以换成全量 LoCoMo 文件

如果使用官方 release tag，可在测试方机器上单独 clone：

```bash
git clone -b version_0.0.5 https://github.com/tech-innovation-group/echo_memory.git /path/to/echo_memory
```

如果 EchoMemory 是私有 fork 或已经改了图记忆模块，请先从 `version_0.0.5` tag 分支出去，并保留 local SDK 的 `EchoMemSDK`、`open_runtime`、`sdk.find()` / `sdk.search()` 等接口，平台脚本就可以继续对接。

## 4. 配置方式

复制模板：

```bash
cp env.echomem.example .env.local
```

编辑 `.env.local`，只在本机填写真实 key，不要提交或发给别人。

最小配置示例：

```bash
export LOCOMO_EVAL_HOST=127.0.0.1
export LOCOMO_EVAL_PORT=19181
export LOCOMO_DATA=/absolute/path/to/locomo10.json

export ECHOMEM_ROOT=/absolute/path/to/echo_memory
export ECHOMEM_WORKSPACE=/absolute/path/to/echomem_workspace
export ECHOMEM_ACCOUNT=locomo_eval_account
export ECHOMEM_USER_ID=locomo_user
export ECHOMEM_AGENT_ID=locomo_agent

export DASHSCOPE_API_KEY=<your-embedding-api-key>
export DASHSCOPE_BASE_URL=https://<embedding-provider-host>/compatible-mode/v1

export ECHOMEM_CHAT_API_KEY=<your-answer-or-extraction-api-key>
export ECHOMEM_CHAT_BASE_URL=https://<chat-provider-host>/compatible-mode/v1
export ECHOMEM_CHAT_MODEL=gpt-5.5

export JUDGE_BASE_URL=https://<judge-provider-host>/v1
export JUDGE_MODEL=gpt-5.5
export JUDGE_TOKEN=<your-judge-api-key>
```

安全要求：

- 不要把真实 key 写入 README、截图、commit、zip 包
- 不要分享 `.env.local`
- 不要分享 `runs/`、`workspace/`、`echomem.runtime.yaml`
- 如果需要发日志，先搜索并确认没有 `api_key`、`token`、`Authorization`、`Bearer`

## 5. 启动评测平台

```bash
cd /path/to/locomo-eval-web
source .env.local
./preflight.sh
./start.sh
```

打开：

```text
<WEB_BASE_URL>/
```

如果端口占用，可以改：

```bash
export LOCOMO_EVAL_PORT=19182
./start.sh
```

## 6. 在 Web 页面跑 LoCoMo + EchoMem

1. 打开左侧 `LoCoMo评测`
2. 在数据文件里选择或填写 LoCoMo JSON 路径
3. 点击 `校验数据集`，确认样本数、QA 数、分类分布正常
4. 进入记忆导入区域，选择 EchoMemory/EchoMem 后端
5. 填写 EchoMem root、workspace、account、user、agent
6. 选择 `conv-30` 或 `all`
7. 点击导入，观察日志中的 `create_session`、`add_message`、`commit_session`
8. 导入完成后看完整性摘要：`expected_messages` 与 `submitted_messages` 应一致，`integrity` 应为 `complete`
9. 在 QA 区域选择题目或随机题数，运行问答
10. QA 完成后运行 Judge
11. 生成 HTML 报告，查看 accuracy、token、耗时、evidence、context、失败样本

## 7. 命令行 smoke test

只导入 `conv-30`：

```bash
source .env.local

python3 scripts/echomemory_locomo_import.py \
  --dataset "$LOCOMO_DATA" \
  --out-dir runs/echomemory_import_smoke/import \
  --echomem-root "$ECHOMEM_ROOT" \
  --workspace "$ECHOMEM_WORKSPACE" \
  --account "$ECHOMEM_ACCOUNT" \
  --user-id "$ECHOMEM_USER_ID" \
  --agent-id "$ECHOMEM_AGENT_ID" \
  --sample conv-30 \
  --session-mode locomo
```

跑 10 道题：

```bash
python3 scripts/echomemory_memory_qa.py \
  --dataset "$LOCOMO_DATA" \
  --out-dir runs/echomemory_import_smoke/qa10 \
  --echomem-root "$ECHOMEM_ROOT" \
  --workspace "$ECHOMEM_WORKSPACE" \
  --account "$ECHOMEM_ACCOUNT" \
  --user-id "$ECHOMEM_USER_ID" \
  --agent-id "$ECHOMEM_AGENT_ID" \
  --sample conv-30 \
  --random-count 10 \
  --top-k 30 \
  --score-threshold 0.1 \
  --retrieval-mode both \
  --answer-base-url "$ECHOMEM_CHAT_BASE_URL" \
  --answer-model "$ECHOMEM_CHAT_MODEL" \
  --answer-token "$ECHOMEM_CHAT_API_KEY"
```

跑 Judge：

```bash
python3 scripts/local_judge.py \
  --input runs/echomemory_import_smoke/qa10/echomemory_memory_qa_results.csv \
  --base-url "$JUDGE_BASE_URL" \
  --model "$JUDGE_MODEL" \
  --token "$JUDGE_TOKEN"
```

## 8. 关键产物

导入阶段：

- `echomemory_import_summary.json`
- `<sample_id>_messages.json`
- `echomem.runtime.yaml`

QA 阶段：

- `echomemory_memory_qa_results.csv`
- `summary.json`

Judge 阶段：

- 原 CSV 会写入 `result` / `reasoning`
- `judge_summary.json`

报告阶段：

- `report.md`
- `report.html`

这些文件都在 `runs/<task_id>/` 下。对外分享前要检查是否包含 key 或敏感路径。

## 9. EchoMem 接入需要保持的接口

平台默认按以下方式调用 EchoMem：

- `from echomem.runtime.runtime import open_runtime`
- `from echomem.protocol.local_sdk.sdk import EchoMemSDK`
- `await sdk.create_session(...)`
- `await sdk.add_message(...)`
- `await sdk.commit_session(...)`
- `await sdk.find(query, ctx=...)`
- `await sdk.search(query, ctx=..., budget={"max_results": top_k})`

如果图记忆模块改了内部实现，建议不要改这些外部接口。需要返回给平台展示的 evidence 最好包含：

- `uri` 或 `source_uri`
- `content`
- `confidence` 或 `score`
- `memory_type`
- `evidence_uri`
- `trace`

## 10. 常见问题

`校验数据集很快完成`：正常。校验只读 JSON 结构和统计题目，不会调用模型或写记忆。

`导入完成但检索为空`：先看导入 summary 是否 complete，再用同一个 account/user/agent 跑 QA。EchoMem workspace、account、user、agent 任一项不同都会变成另一个干净空间。

`answer 是 unknown 但 evidence 有内容`：通常是 answer prompt 约束过严、evidence 太长、相关证据不完整、模型空响应或重试失败。先看 CSV 的 `health_status`、`model_error_kind`、`retrieval_count`、`context_preview`。

`准确率为空或待 Judge`：QA 只生成答案，正式准确率需要 Judge 后才有。

`模型限流或连接中断`：提高 `--timeout-s`，降低并发，确认 provider base URL 和模型名正确。

## 11. 打包前安全检查

发送给别人前运行：

```bash
rg -n "api_key|token|Authorization|Bearer|sk-" .
```

允许出现的情况：

- README 中的占位符，例如 `<your-api-key>`
- 代码里的变量名，例如 `api_key`
- 模板里的环境变量名，例如 `${DASHSCOPE_API_KEY}`

不允许出现：

- 真实 key
- 带真实 key 的 config
- 带真实 key 的日志
- 真实 `.env.local`
