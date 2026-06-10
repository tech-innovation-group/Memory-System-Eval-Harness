# EchoMem + LoCoMo 评测平台交付说明

这份说明给外部测试同学使用。目标是：拿到本测试平台包后，把自己的 EchoMem 记忆系统接入进来，完成 LoCoMo 数据导入、记忆检索问答、Judge 打分和 HTML 报告导出。

交付包不包含任何 embedding 或大模型 API key。所有 key 都必须由测试者在自己的机器上通过环境变量或 Web 页面填写，不能写进 README、截图、压缩包、日志或提交记录。

## 1. 交付包内容

包内主要包含：

- `server.py`：本地 Web 评测平台后端
- `web/static/`：Web 页面和前端交互
- `memory/`：评测平台后端包和记忆后端适配接口
- `memory/adapters/contract.py`：统一记忆后端契约，检查导入、commit、检索、完整性、浏览和 LoCoMo task 构建能力
- `scripts/adapter_doctor.py`：命令行后端自检，支持 text/json/markdown 输出，不包含真实密钥
- `memory/adapters/echomemory/`：EchoMem/EchoMemory 适配器入口
- `scripts/echomemory_locomo_import.py`：LoCoMo 对话导入 EchoMem
- `scripts/echomemory_memory_qa.py`：基于 EchoMem 检索结果运行 LoCoMo QA
- `scripts/local_judge.py`：对 QA CSV 进行 Judge
- `scripts/generate_html_report.py`：生成静态 HTML 报告
- `dataset/locomo10.json`：内置 LoCoMo 10 conversation smoke 数据
- `dataset/manifest.json`：数据集注册表
- `env.echomem.example`：EchoMem 接入环境变量模板
- `start.sh` / `preflight.sh`：启动和预检脚本

包内刻意不包含：

- `runs/` 历史运行结果
- `workspace/`、EchoMem workspace、OpenViking workspace 等本地记忆目录
- `judge.conf`、`.env.local`、任何真实配置文件
- `*.log`、历史运行输出、真实 token
- 本机临时分析报告和旧 `outputs/`

## 2. 整体链路

```text
LoCoMo JSON
  -> Web Harness
  -> EchoMemory adapter
  -> echomemory_locomo_import.py
  -> EchoMem local SDK: create_session / add_message / commit_session
  -> EchoMem workspace/account/user/agent 记忆空间
  -> echomemory_memory_qa.py
  -> EchoMem local SDK: find / search
  -> Answer LLM 生成回答
  -> local_judge.py
  -> Judge LLM 打分
  -> CSV / summary.json / HTML report
```

平台只负责任务编排、进度展示、日志保存、结果汇总和报告导出。EchoMem 的图记忆、向量索引、抽取、融合召回等逻辑仍由 EchoMem 自己实现。

## 3. 本机准备

测试者机器需要准备：

- Python 3.11+
- EchoMemory `version_0.0.5` 源码目录，例如 `/absolute/path/to/echo_memory`
- EchoMem 自己的依赖环境或虚拟环境
- 可用的 embedding 服务
- 可用的 answer LLM 服务
- 可用的 Judge LLM 服务
- LoCoMo JSON 数据。可以先用包内 `dataset/locomo10.json` 做 smoke test，再换成全量数据

如果使用官方 release tag，可以参考：

```bash
git clone -b version_0.0.5 https://github.com/tech-innovation-group/echo_memory.git /absolute/path/to/echo_memory
```

如果测试者使用的是自己的 EchoMemory fork 或图记忆改造版本，建议从 `version_0.0.5` tag 分支出去；只要保留下面的外部接口，平台脚本就可以继续对接：

- `from echomem.runtime.runtime import open_runtime`
- `from echomem.protocol.local_sdk.sdk import EchoMemSDK`
- `await sdk.create_session(...)`
- `await sdk.add_message(...)`
- `await sdk.commit_session(...)`
- `await sdk.find(query, ctx=...)`
- `await sdk.search(query, ctx=..., budget={"max_results": top_k})`

建议检索返回的 evidence 至少包含：

- `content`
- `source_uri` 或 `uri`
- `memory_type`
- `confidence` 或 `score`
- `evidence_uri`
- `trace`

## 4. 配置方式

进入解压后的平台目录：

```bash
cd /absolute/path/to/locomo-eval-web-echomem-handoff
cp env.echomem.example .env.local
```

编辑 `.env.local`。下面只是格式示例，不能把真实 key 发给别人：

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

export ECHOMEM_CHAT_PROVIDER=deepseek
export ECHOMEM_CHAT_MODEL=gpt-5.5
export ECHOMEM_CHAT_API_KEY=<your-answer-or-extraction-api-key>
export ECHOMEM_CHAT_BASE_URL=https://<chat-provider-host>/compatible-mode/v1

export JUDGE_BASE_URL=https://<judge-provider-host>/v1
export JUDGE_MODEL=gpt-5.5
export JUDGE_TOKEN=<your-judge-api-key>
```

安全规则：

- `.env.local` 只放在测试者本机，不要打包、提交或截图
- Web 页面里填过 token 后，不要把浏览器截图发出去
- 不要分享 `runs/`、EchoMem workspace、`echomem.runtime.yaml` 或日志，除非确认已经脱敏
- 发送任何压缩包前，必须做第 10 节的密钥扫描

## 5. 启动平台

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

## 6. Web 页面跑 EchoMem + LoCoMo

推荐先跑 `conv-30` 的 5 到 10 道题做 smoke test。

1. 打开左侧 `LoCoMo评测`
2. 在数据集路径中填写 LoCoMo JSON，或使用包内 `dataset/locomo10.json`
3. 点击 `校验数据集`
4. 选择 EchoMem/EchoMemory 接入
5. 填写 EchoMem root、workspace、account、user、agent
6. 选择要导入的 conversation，例如 `conv-30`，也可以选择 `all`
7. 点击导入，观察日志中是否有 `create_session`、`add_message`、`commit_session`
8. 导入完成后查看完整性摘要，重点看：
   - expected messages
   - submitted messages
   - committed session
   - integrity status
9. 在 QA 区域选择部分题目或设置随机题数
10. 运行 QA，观察进度、模型错误、检索数量、token 消耗
11. QA 完成后运行 Judge
12. 生成 HTML 报告，查看准确率、耗时、token、evidence、context 和失败样本

## 7. 命令行 smoke test

如果 Web 页面还没接好，可以先用命令行确认 EchoMem 接入是否可用。

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

## 8. 关键产物

导入阶段：

- `runs/<task_id>/echomemory_import/echomemory_import_summary.json`
- `runs/<task_id>/echomemory_import/<conv_id>_messages.json`
- `runs/<task_id>/echomemory_import/echomem.runtime.yaml`

QA 阶段：

- `runs/<task_id>/echomemory_qa/echomemory_memory_qa_results.csv`
- `runs/<task_id>/echomemory_qa/summary.json`

Judge 阶段：

- QA CSV 会追加或更新 `result`、`reasoning` 等字段
- `judge_summary.json`

报告阶段：

- `report.md`
- `report.html`

这些文件可能包含本机路径、模型输出或业务数据。外发前要脱敏。

## 9. 常见问题

`校验数据集为什么很快完成？`

正常。校验只读 JSON 结构和题目统计，不会调用模型，也不会写入 EchoMem。

`导入成功但检索为空？`

优先检查 EchoMem workspace、account、user、agent 是否和 QA 阶段完全一致。任一项不同，都会变成另一个干净记忆空间。

`expected messages 和 submitted messages 不一致？`

说明导入阶段有消息没有成功写入，需要查看 import 日志和 EchoMem SDK 报错。

`commit_session 成功但 memory 文件少？`

可能是 EchoMem 抽取阈值、图记忆开关、异步落盘或 extractor 配置导致。先确认 EchoMem 的 commit 任务是否真正完成，再看 EchoMem workspace 下的索引和图存储目录。

`有 evidence 但 answer 是 unknown？`

通常是 evidence 不够完整、prompt 约束过严、模型空响应、模型限流、context 太长被截断，或者检索结果与问题实体/时间不匹配。看 CSV 的 `retrieval_count`、`context_preview`、`model_error_kind` 和报告里的 evidence。

`准确率为空？`

QA 只生成答案，准确率需要 Judge 阶段完成后才会出现。

## 10. 打包和外发前安全检查

在要发送的目录或解压后的目录运行：

```bash
rg -n "sk-[A-Za-z0-9_-]{12,}|api_key:|Authorization:|Bearer |JUDGE_TOKEN=.*[A-Za-z0-9_-]{12,}|ECHOMEM_CHAT_API_KEY=.*[A-Za-z0-9_-]{12,}|DASHSCOPE_API_KEY=.*[A-Za-z0-9_-]{12,}" .
```

允许出现：

- 代码变量名，例如 `api_key`
- 环境变量名，例如 `DASHSCOPE_API_KEY`
- 占位符，例如 `<your-api-key>`
- 模板引用，例如 `${ECHOMEM_CHAT_API_KEY}`

不允许出现：

- 真实 `sk-...` token
- 真实 `Authorization: Bearer ...`
- 真实 `.env.local`
- 历史 `runs/` 里的 `ov.web.conf`、`openviking.runtime.conf`、`echomem.runtime.yaml`
- 带真实 key 的日志或截图

## 11. 建议给测试者的最小验收标准

先不要直接跑全量。建议按下面顺序验收：

1. `./preflight.sh` 通过
2. Web 页面能打开
3. `dataset/locomo10.json` 校验通过
4. `conv-30` 导入完成，消息数完整
5. EchoMem workspace 下能看到对应 account 的存储变化
6. 选择 1 道 QA，能看到 answer、evidence、token 和耗时
7. 跑 10 道 QA，生成 CSV
8. Judge 完成，报告能打开
9. 再切换到全量 LoCoMo 或更多 conversation
