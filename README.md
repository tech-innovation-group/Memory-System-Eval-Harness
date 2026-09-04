# Memory-System-Eval-Harness

记忆系统评测框架。核心评测全 CLI，无需网页 UI；直接通过 Python 脚本完成数据集
加载、记忆注入、Agent 问答、Judge 评分和结果报告。服务器可以在此 CLI 之上增加
Web/飞书编排层，完整线上流程见 [deploy/REMOTE_SERVER_README.md](deploy/REMOTE_SERVER_README.md)。

> 服务器上的 Web/飞书任务入口不是本仓库 CLI 的替代实现，而是一个编排层：
> 它负责拉取 EchoMem、检查 PR 合并快照、复用依赖镜像、隔离 workspace、启动
> EchoMem，并最终调用本仓库的 `benchmarks/locomo/run_eval.py`。服务器实际流程、
> 入口脚本和复核命令见 [deploy/REMOTE_SERVER_README.md](deploy/REMOTE_SERVER_README.md)；
> 固定执行顺序和故障经验模板见 [deploy/ECHOMEM_EVAL_RUNBOOK.md](deploy/ECHOMEM_EVAL_RUNBOOK.md)。

## 服务器一键部署

```bash
git clone --branch v3_mcpTool \
  https://github.com/tech-innovation-group/Memory-System-Eval-Harness.git \
  /opt/memory-eval-harness
cd /opt/memory-eval-harness
sudo deploy/install_server.sh
```

首次执行会生成 `/opt/memory-eval-web/server.env`。填写
`DEFAULT_LLM_API_KEY`、`DEFAULT_EMBEDDING_API_KEY`、`SESSION_SECRET` 和
`PUBLIC_BASE_URL`；使用飞书时再填写 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`。
再次执行即可启动 Web/飞书入口，默认端口为 `8081`。完整流程见
[deploy/REMOTE_SERVER_README.md](deploy/REMOTE_SERVER_README.md)。

## 通用系统压测

除 EchoMem 专用压测套件外，平台还提供配置驱动的真实 HTTP/JSON 压测入口，
可用于 REST 服务、网关、Agent API、检索服务和模型代理。通过请求模板、
健康检查、业务断言和负载场景描述目标系统，不需要修改压测代码：

```bash
python3 stress/generic/runner.py \
  --config stress/generic/example.json \
  --out-dir results/stress/generic_$(date +%Y%m%d_%H%M%S)
```

使用说明和适用边界见
[stress/generic/README.md](stress/generic/README.md)。每轮会生成
`summary.json`、请求/资源 CSV 和可读的 `report.html`，并把异常分为可用性、
错误率、延迟、负载达成率和资源增长问题。复杂协议如 gRPC、WebSocket 或
纯 TCP 需要新增 transport 适配器。服务器入口使用 `test_type=generic_stress`
提交配置后，会复用同一个单并发任务队列，并在任务详情页提供实时日志和报告文件。

## 设计目标

### 1. 支撑业界所有 agent 的评测

框架支撑业界所有 agent 的评测：被测 agent 通过统一插件协议接入，同一套评测流程
可在不同 agent 与记忆后端上跑出可比结果，确保结果可复现、可审计。

- **AgentPlugin 协议**：所有被测 agent 实现统一接口（`setup -> inject_memories ->
  create_session -> send_message -> getlog`），评测流程只调用接口，不接触 agent
  特定的 HTTP API。新增 agent 只需创建插件目录，无需改动框架。
- **双记忆后端**：`echomem` 和 `openviking` 两个后端实现同一 `MemoryClient`
  协议，通过 `--memory-backend` 切换，保证同一套评测可以在不同后端上跑出可比
  结果。
- **LLM Judge + provenance**：LoCoMo / LongMemEval 使用 LLM Judge 评分，
  HotpotQA 使用官方 F1/EM 指标。每次运行产出 `summary.json`、`config.json`、
  `memory_provenance.json` 和逐题 `agent_traces/*.json`，记录数据集 SHA-256、
  身份、prompt 来源和工具调用链，确保结果可复现、可审计。

### 2. 支撑内部需求

支撑压测、精度测试、定位算法改进点等内部场景。

- **动态评测**：`generate` 模式由 LLM 生成场景和提问，端到端走 EchoAgent 完整
  管线（含 prefill / TTFT）；`replay` 模式回放数据集对话，测试跨 session 召回。
- **多维质量评分**：动态评测通过 YAML 配置定义 10 个评分维度（任务完成度、
  事实覆盖、信息准确性等，满分 100），由 LLM 逐轮打分并输出诊断。
- **诊断与定位**：LoCoMo 产出 `diagnosis.json`、`retrieval_traces.jsonl` 和
  `retrieval_coverage`，标注失败题、可重试题和检索覆盖缺口。`blackbox.py` 和
  `compare.py` 支持黑盒指标导出和两次运行对比。
- **断点续跑**：QA 和 Judge 均支持 `--resume-qa` / `--resume-judge`，健康行不
  重复调用模型；`--checkpoint-interval` 定期落盘部分结果。

### 3. 简单易用 / AI 入口

直接 Python 调用，CLI 参数即配置，AI 友好。

- **直接启动**：`python benchmarks/<name>/run_eval.py` 或
  `python dynamic/run_eval.py`，一条命令完成全流程，无需额外包装层。
- **CLI 参数驱动**：所有连接地址、模型配置、记忆后端、插件选择通过 CLI 参数
 传入，可写在 `.bat` / `.sh` 脚本中固化。环境变量作为默认值，CLI 参数覆盖。
- **预检**：评测启动时自动验证数据集、记忆后端连通性和模型配置，通过后才进入
  正式评测流程。

### 4. 生产一致

确保评测结果与生产环境完全一致。

- **真实记忆注入**：评测通过 `inject_memories()` 将数据集对话写入真实 EchoMem
  或 OpenViking 后端（`open_session -> add_message -> commit -> poll`），不使用
  mock 或旁路。
- **身份隔离**：每次评测新开独立 tenant / user / agent 身份，`--resume-qa` 时
  复用原有身份。身份信息（account / user_id / auth_key）记录在 resume manifest
  中，auth key 仅掩码保存。
- **数据完整性校验**：LoCoMo 在 QA 前校验数据集 SHA-256 和实际 session
  manifest，session 数量不匹配时拒绝运行，防止复用 tenant 被污染。
- **生产管线**：动态评测的 QA 阶段走 EchoAgent 完整 HTTP 管线，含 prefill /
  typing simulation / TTFT 采集，与线上行为一致。

## 目录结构

```
plugins/                     # Agent 插件 (AgentPlugin 协议)
  base.py                    #   AgentPlugin ABC + AgentResponse / TypingResult
  registry.py                #   按名动态加载，无需手动注册
  bare_llm/                  #   纯 LLM 基线 (无记忆检索)
  echo_agent/                #   EchoAgent + EchoMem 完整管线 (动态评测默认)
  vikingbot/                 #   VikingBot 工具调用 agent (LoCoMo 默认)
  echomem_mcp/               #   LLM 通过 EchoMem MCP 工具检索记忆
  openviking_mcp/            #   LLM 通过 MemoryClient 工具检索记忆
backends/                    # 记忆后端客户端
  memory_types.py            #   MemoryClient 协议 + BaseHTTPMemoryClient + NullMemoryClient
  memory_args.py             #   add_memory_backend_args() -- 后端连接 CLI 参数
  echomem/                   #   EchoMemClient (端口 8010)
  openviking/                #   OpenVikingClient (端口 19080)
benchmarks/                  # 静态数据集评测
  locomo/                    #   LoCoMo: LLM Judge (CORRECT/WRONG)
    run_eval.py              #     入口脚本
    dataset.py               #     数据集加载与解析
    import_memory.py         #     记忆导入
    qa.py                    #     QA 任务构建与执行
    judge.py                 #     LLM Judge
    reporting.py             #     结果汇总
    data/                    #     内置 locomo10.json
    results/                 #     运行结果
  hotpotqa/                  #   HotpotQA: F1/EM 官方指标
  longmemeval/               #   LongMemEval: LLM yes/no accuracy
  doc/                       #   benchmark 通用文档
dynamic/                     # 动态评测 (generate / replay)
  run_eval.py                #   入口脚本
  workflows.py               #   generate / replay 工作流
  simulator.py               #   场景与查询生成
  metrics.py                 #   动态指标和多维质量评估
  artifacts.py               #   JSON/CSV/报告输出
  model_client.py            #   动态 LLM 客户端
  prompt_config.py           #   prompt 配置加载
  configs/                   #   evaluator / user_simulator YAML 配置
  results/                   #   运行结果
shared/                      # 共享基础设施
  eval_base.py               #   EvalConfig / EvalRun / CLI arg helpers
  llm_client.py              #   LLM 客户端 (OpenAI 兼容, urllib)
  dataset_io.py              #   通用数据集路径解析与下载
  runtime_config.py          #   环境变量映射 + 预检
  recovery.py                #   QA CSV 健康判定与恢复
  qa.py                      #   通用 QA 数据结构
  csv_io.py                  #   CSV 读写工具
  import_guard.py            #   导入完整性校验
  benchmark_qa.py            #   benchmark QA 共享逻辑
scripts/                     # 辅助工具
  backend_doctor.py          #   记忆客户端健康检查
  validate_evidence.py       #   QA 检索证据格式检查
```

正式数据集的加载、Judge、指标、重试和报告归属 `benchmarks/<dataset>/`。评测
针对 agent 插件而非记忆后端；记忆注入通过 `AgentPlugin.inject_memories()` 统一
完成，评测平台不直接感知记忆后端。

## 核心架构

### 插件生命周期

```
setup(config)
  -> inject_memories(memories, backend=...)
  -> (create_session -> [simulate_typing] -> send_message)*
  -> getlog
  -> teardown
```

评测流程只调用 `AgentPlugin` 接口方法。`setup` 初始化客户端和记忆后端；
`inject_memories` 将数据集对话写入后端；QA 阶段逐题 `create_session` ->
`send_message`（可选 `simulate_typing` 触发 prefill）；`getlog` 收集后端日志。

### Benchmark 三阶段流程

```
导入记忆 (inject_memories) -> 逐题 QA (仅检索不写入) -> Judge / Evaluate
```

- **导入**：将数据集 conversation 按 session 分批写入记忆后端，commit + poll
  直到抽取完成。LoCoMo 校验数据集 SHA-256 和 session manifest。
- **QA**：并发（`--concurrency`）逐题检索记忆 -> 构建 prompt -> LLM 回答。
  检索阶段不写入记忆。支持 `--resume-qa` 断点续跑。
- **评测**：LoCoMo / LongMemEval 使用 LLM Judge；HotpotQA 使用官方 F1/EM。
  产出 `summary.json`、`qa_results.csv`、`judge_results.csv`、`agent_traces/`。

### 服务器 PR 评测流程

服务器收到“测试 develop”或“测试 PR 227”后，实际顺序如下：

1. 刷新 GitHub 上的 `EchoMem/develop` commit，并记录为本次任务基线。
2. `develop` 任务下载或复用该 commit 的源码；PR 任务读取 PR 状态、head 和
   GitHub 生成的 `pull/<pr>/merge` 快照。关闭 PR、非 develop 目标分支或无法获得
   合并快照时停止，不进入评测。
3. 按源码中的 `configs/config.example.json` 生成任务配置。平台只注入服务器模型
   凭证和必要的运行时环境，不使用旧的平台模板覆盖 EchoMem 的 engine、recall、
   rerank、MCP 或其他 schema。
4. 根据依赖文件指纹复用长期评测镜像；源码、workspace、tenant、session 和结果
   目录按任务隔离。同一个 commit 可以重复测试，不复用旧准确率。
5. 启动任务专属 EchoMem workspace，先在容器内检查 `127.0.0.1:8010/health`，
   再做网络检查；启动超时会保存 `docker inspect`、`docker logs`，并自动重启一次。
6. 执行本仓库的 LoCoMo `conv-30`：记忆注入 -> QA -> Judge。服务器默认关闭模型
   工具调用和 MCP 对话读取，但保留每题首次 `memory_query` 检索；QA/Judge 参数、
   模型和温度以任务详情中的 `config.json` 为准。
7. 保存 `summary.json`、QA/Judge 明细、检索轨迹、agent trace、容器日志和诊断；
   完成后回传准确率，并按服务器上传配置发送结果压缩包。

这里的“测试平台流程”和“benchmark CLI 流程”要区分：上面的步骤 1--5、7 属于
服务器编排层；步骤 6 才是本仓库 benchmark 的标准评测逻辑。不要仅凭网页或飞书
消息判断实际参数，应以任务结果目录的 `config.json`、`summary.json` 和
`container.log` 为准。

### 动态评测双模式

```
generate: LLM 生成场景 -> 注入 EchoMem -> 逐轮 QA (端到端 EchoAgent 管线)
replay:   回放数据集对话 -> 注入 EchoMem -> 新会话 QA (跨 session 召回)
```

两种模式的注入阶段直连 EchoMem，不经 EchoAgent；QA 阶段走 EchoAgent 完整
管线（含 prefill / TTFT）。质量评分由 YAML 配置驱动，10 个维度满分 100。

## 快速开始

### 前置条件

- Python 3.10+
- 依赖安装：`pip install -r requirements.txt`（仅需 `tqdm` 和 `PyYAML`）
- 对应的后端服务已启动（见下表）

### 服务启动

评测前需启动对应的后端服务。以下为各服务端口说明：

| 服务 | 端口 | 用途 | 启动方式 |
|---|---|---|---|
| EchoMem | 8010 (HTTP) / 8011 (WS) | 记忆后端 (echomem) | `echomem server --host 127.0.0.1 --port 8010 --workspace <workspace>` |
| OpenViking | 19080 | 记忆后端 (openviking) | `openviking-server --config <config>` |
| EchoAgent Backend | 31020 | 动态评测 agent 后端 | `node dist/src/main.js`（EchoAgent 仓库） |
| EchoAgent Memory Engine | 31030 | EchoAgent 记忆引擎插件 | 随 EchoAgent Backend 启动 |

Benchmark 评测只需启动记忆后端（EchoMem 或 OpenViking）。动态评测还需额外
启动 EchoAgent Backend（含 Memory Engine）。

服务器或本地已有 EchoMem checkout 时，推荐使用统一启动脚本，避免每个 PR
重复安装依赖或构建镜像：

```bash
export DEFAULT_LLM_BASE_URL=https://api.deepseek.com/v1
export DEFAULT_LLM_MODEL=deepseek-v4-flash
export DEFAULT_LLM_API_KEY=YOUR_KEY
export DEFAULT_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export DEFAULT_EMBEDDING_MODEL=text-embedding-v3
export DEFAULT_EMBEDDING_API_KEY=YOUR_EMBEDDING_KEY

deploy/start_echomem_eval.sh \
  --source /path/to/EchoMem \
  --workspace /path/to/job/workspace \
  --cache-dir /path/to/shared/cache/recall
```

每次任务使用新的 workspace 和身份；只复用静态 embedding warm-up cache：
`semantic_embeddings.json`，以及存在时的 `template_embeddings.json`。依赖未变化时
复用 runner 环境，依赖变化时才重新构建。服务器 Web 入口复用长期评测 runner
镜像，不会因为测试新的 PR 重建 EchoMem 镜像。每个任务仍创建独立的源码、
workspace、tenant、session 和结果目录；provisioning 凭据只在任务容器环境中
短暂存在，结果目录使用宿主机绝对路径挂载。LLM Key 和 embedding Key 必须分别
对应各自的 endpoint，真实 Key 只放服务器 `server.env`，不写入仓库。停止任务：

```bash
deploy/stop_echomem_eval.sh /path/to/job/workspace
```

### 环境变量（可选）

CLI 参数可直接传入，也可通过环境变量设默认值：

| 变量 | 说明 |
|---|---|
| `ECHOMEM_BASE_URL` | EchoMem HTTP 地址，默认 `http://127.0.0.1:8010` |
| `ECHOMEM_ACCOUNT` / `ECHOMEM_USER_ID` / `ECHOMEM_AGENT_ID` | 记忆后端身份 |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | 回答模型配置 |
| `JUDGE_MODEL` / `JUDGE_TOKEN` / `JUDGE_BASE_URL` | Judge 模型（默认同回答模型） |
| `HOTPOTQA_DATASET` / `LONGMEMEVAL_DATASET` | 数据集路径（LoCoMo 已内置） |

### 预检

评测启动时自动执行预检：加载数据集验证非空、调用 `memory_client.health()`
检查记忆后端连通性。通过后进入正式评测流程。

## 运行评测

### Benchmark 评测

直接调用 `benchmarks/<name>/run_eval.py`，通过 `--agent-plugin` 选择被测 agent，
通过 `--memory-backend` 选择记忆后端。

#### LoCoMo + echomem_mcp（EchoMem 后端）

<div style="color: red;">

无工具调用时，测试平台仍通过 EchoMem MCP 执行每题的初始 `memory_query`：

<pre style="color: red;"><code class="language-bash">./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8010 \
  --mcp-url http://127.0.0.1:8001 \
  --sample conv-30 \
  --no-tool-calling \
  --mcp-read-mode disabled \
  --concurrency 4 \
  --judge-concurrency 4 \
  --top-k 25 \
  --memory-budget-chars 8000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --llm-temperature 0.7 \
  --question-timeout-s 600 \
  --llm-timeout-s 600 \
  --llm-retries 3</code></pre>

允许模型通过 MCP 调用工具，但禁止读取 `messages.jsonl`：

<pre style="color: red;"><code class="language-bash">./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8010 \
  --mcp-url http://127.0.0.1:8001 \
  --sample conv-30 \
  --tool-calling \
  --mcp-read-mode disabled \
  --concurrency 4 \
  --judge-concurrency 4 \
  --top-k 25 \
  --memory-budget-chars 8000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --llm-temperature 0.7 \
  --question-timeout-s 600 \
  --llm-timeout-s 600 \
  --llm-retries 3</code></pre>

允许模型通过 MCP 调用工具，并允许读取 `messages.jsonl`：

<pre style="color: red;"><code class="language-bash">./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8010 \
  --mcp-url http://127.0.0.1:8001 \
  --sample conv-30 \
  --tool-calling \
  --mcp-read-mode allow \
  --concurrency 4 \
  --judge-concurrency 4 \
  --top-k 25 \
  --memory-budget-chars 8000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --llm-temperature 0.7 \
  --question-timeout-s 600 \
  --llm-timeout-s 600 \
  --llm-retries 3</code></pre>

</div>

#### 一次注入，多组 EchoMem engine 配置 QA

如果要比较 `atomic_engine`、`atomic_engine + base_engine` 和全引擎，
先用任意一组配置完成一次 LoCoMo `conv-30` 注入。之后复用同一个
workspace、tenant/user 和注入结果目录，只重启 EchoMem 使新的 `config.json`
生效，再对每组配置执行 QA/Judge：

```bash
./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8010 \
  --mcp-url http://127.0.0.1:8001 \
  --sample conv-30 \
  --qa-only-from /path/to/injection-run \
  --no-tool-calling \
  --mcp-read-mode disabled \
  --concurrency 1 \
  --judge-concurrency 1 \
  --top-k 25 \
  --memory-budget-chars 8000 \
  --user-memory-budget-chars 4000 \
  --agent-memory-budget-chars 2000 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --llm-temperature 0.7 \
  --question-timeout-s 600 \
  --llm-timeout-s 600 \
  --llm-retries 3
```

`--qa-only-from` 会校验来源目录存在完整的 `import_results.csv` 和
`qa_resume_manifest.json`，并在日志中明确记录“严格跳过 open/add/commit”。
三组结果必须分别保存，不能复用旧的 `qa_results.csv` 或 `judge_results.csv`；
这样比较到的差异才只来自 QA 时的 engine 配置。

仓库提供了可复用脚本：

```bash
export ENGINE_MATRIX_ROOT=/opt/memory-eval-engine-matrix
export ENGINE_MATRIX_RESULTS_ROOT=/opt/memory-eval-harness-latest/results
export ECHOMEM_IMAGE=memory-eval-echomem:<dependency-tag>
export EVAL_IMAGE=memory-eval-runner:<runner-tag>
export ECHOMEM_CONFIG_EXAMPLE=/path/to/EchoMem/configs/config.example.json
export ECHOMEM_REGISTRY_MASTER_KEY=<temporary-key>
scripts/run_locomo_engine_matrix.sh
```

脚本从 `configs/config.example.json` 生成三份任务配置：
`atomic-only`、`atomic-base` 和 `full`。只有 `full` 执行注入；
另外两组使用 `--qa-only-from`，严格跳过注入 API。完成后生成
`ENGINE_MATRIX_ROOT/matrix-report.json`。

#### LoCoMo + vikingbot（OpenViking 后端）

```bash
python benchmarks/locomo/run_eval.py \
  --agent-plugin vikingbot \
  --memory-backend openviking \
  --echomem-url http://127.0.0.1:19080 \
  --workspace D:/.openviking/data \
  --sample conv-30 \
  --questions 0 \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash \
  --llm-api-key YOUR_KEY \
  --commit-timeout-s 0 \
  --question-timeout-s 0 \
  --llm-timeout-s 600
```

#### 断点续跑

```bash
./.venv/bin/python benchmarks/locomo/run_eval.py \
  --agent-plugin echomem_mcp \
  --echomem-url http://127.0.0.1:8010 \
  --mcp-url http://127.0.0.1:8001 \
  --sample conv-30 \
  --no-tool-calling \
  --resume-qa benchmarks/locomo/results/20260803_143943_618591 \
  --llm-base-url "$LLM_BASE_URL" \
  --llm-model "$LLM_MODEL" \
  --llm-api-key "$LLM_API_KEY" \
  --question-timeout-s 600 \
  --llm-timeout-s 600 \
  --llm-retries 3
```

#### 其他 benchmark

```bash
# HotpotQA
python benchmarks/hotpotqa/run_eval.py \
  --agent-plugin bare_llm \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash \
  --llm-api-key YOUR_KEY \
  --questions 10

# LongMemEval
python benchmarks/longmemeval/run_eval.py \
  --agent-plugin bare_llm \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash \
  --llm-api-key YOUR_KEY \
  --questions 10
```

| Benchmark | 默认插件 | 评测方式 | 数据集 |
|---|---|---|---|
| `locomo` | `vikingbot` | LLM Judge (CORRECT/WRONG) | 内置 `locomo10.json` |
| `hotpotqa` | `bare_llm` | F1/EM 官方指标 | 需设置 `HOTPOTQA_DATASET` |
| `longmemeval` | `bare_llm` | LLM yes/no accuracy | 需设置 `LONGMEMEVAL_DATASET` |

结果写入 `benchmarks/<name>/results/<timestamp>/`，主要文件：`qa_results.csv`、
`judge_results.csv`、`summary.json`、`config.json`、`agent_traces/`、
`backend_logs.json`。

### 动态评测

直接调用 `dynamic/run_eval.py`。需先启动 EchoAgent Backend（端口 31020）和
EchoMem（端口 8010）。

#### Generate 模式

LLM 生成场景和提问，端到端走 EchoAgent 完整管线：

```bash
python dynamic/run_eval.py \
  --echoagent-url http://127.0.0.1:31020 \
  --memory-engine-endpoint http://127.0.0.1:31030 \
  --echomem-url http://127.0.0.1:8010 \
  --username test_user \
  --password YOUR_PASSWORD \
  --num-memories 5 \
  --num-queries 5 \
  --new-session-ratio 0.3 \
  --typing-speed-ms 2 \
  --scenario-model deepseek-v4-flash \
  --scenario-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --scenario-api-key YOUR_KEY \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash \
  --llm-api-key YOUR_KEY
```

#### Replay 模式

回放已有数据集对话，测试跨 session 召回：

```bash
python dynamic/run_eval.py \
  --echoagent-url http://127.0.0.1:31020 \
  --memory-engine-endpoint http://127.0.0.1:31030 \
  --echomem-url http://127.0.0.1:8010 \
  --username test_user \
  --password YOUR_PASSWORD \
  --dataset dynamic/results/20260728_175544/dataset.json \
  --new-session-ratio 0.3 \
  --typing-speed-ms 2 \
  --scenario-model deepseek-v4-flash \
  --scenario-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --scenario-api-key YOUR_KEY \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model deepseek-v4-flash \
  --llm-api-key YOUR_KEY
```

结果写入 `dynamic/results/<timestamp>/`，主要文件：`dataset.json`、
`rounds.csv`、`summary.json`、`quality_report.json`。

## 扩展指南

### 新增 Agent 插件

1. 创建 `plugins/<name>/` 目录
2. 创建 `__init__.py`（空即可）
3. 创建 `plugin.py`，实现 `AgentPlugin` 子类：

```python
from plugins.base import AgentPlugin, AgentResponse

class MyAgentPlugin(AgentPlugin):
    def setup(self, config: dict) -> None:
        # 初始化客户端、创建 memory_client
        ...

    def inject_memories(self, memories, *, backend="echomem", session_id=""):
        # 写入记忆后端 (不支持的插件不覆盖，默认 no-op)
        ...

    def create_session(self, title=""):
        # 创建 QA 会话，返回 session_id
        ...

    def send_message(self, session_id, message, context_path="/", *, extra=None):
        # 发送消息，返回 AgentResponse
        return AgentResponse(text="...")

    def getlog(self) -> str:
        # 返回日志 JSON 字符串
        return "{}"
```

4. 实现 `add_arguments` classmethod 声明 CLI 参数，可复用
   `backends/memory_args.py` 中的 `add_memory_backend_args()`。

`registry.py` 自动扫描 `plugins.<name>.plugin` 模块中 `AgentPlugin` 的子类，
无需手动注册。运行：`python benchmarks/locomo/run_eval.py --agent-plugin <name> ...`

### 新增记忆后端

1. 创建 `backends/<name>/` 目录
2. 创建 `client.py`，实现 `BaseHTTPMemoryClient` 子类，覆盖 `_headers()` 和
   `_fetch_commit_status()` 等抽象方法，并实现 `search` / `fs_read` /
   `fs_list` / `fs_glob` 等检索方法
3. 在 `backends/memory_args.py` 的 `add_memory_backend_args()` 中添加连接参数
4. 在使用该后端的插件 `setup()` 中实例化客户端

```python
from backends.memory_types import BaseHTTPMemoryClient

class MyBackendClient(BaseHTTPMemoryClient):
    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}
    # 实现 search / commit / fs 等方法...
```

### 新增 Benchmark 数据集

1. 创建 `benchmarks/<name>/` 目录
2. 实现核心模块：
   - `dataset.py` - 数据集加载与解析
   - `import_memory.py` - 记忆导入逻辑
   - `qa.py` - QA 任务构建与执行
   - `judge.py` 或 `evaluate.py` - 评测逻辑
   - `reporting.py` - 结果汇总
   - `run_eval.py` - 入口脚本
3. 复用 `shared/` 基础设施：`EvalConfig` / `EvalRun` /
   `add_agent_plugin_args` / `add_eval_args` / `add_judge_args` / `LLMClient`

## 评测流程概览

| Benchmark | 导入方式 | QA 方式 | 评测方式 |
|---|---|---|---|
| LoCoMo | 集中导入所有 session | 仅检索不写入 | LLM judge (CORRECT/WRONG) |
| HotpotQA | per_question 或 global | 仅检索不写入 | answer/supporting-fact/joint F1/EM |
| LongMemEval | 逐题隔离导入 haystack | 仅检索不写入 | 官方 accuracy (LLM yes/no) |
| 动态 (generate) | LLM 生成场景 | 端到端 EchoAgent | 配置驱动质量评估 (0-100) |
| 动态 (replay) | 先注入对话再 QA | 跨 session 检索 | 配置驱动质量评估 (0-100) |

> **指标变更同步约定**：如果任何一个 benchmark（locomo / hotpotqa / longmemeval）
> 或 dynamic 的评估指标、产物字段（`summary.json` / `quality_report.json` /
> `eval_results.csv` / `dynamic_results.json` 等）发生增删或含义改变，必须同步更新
> `scripts/memory-eval-improve` skill 中对应的 benchmark/dynamic **特有字段描述**
> （`references/benchmark-specific-fields.md` 与 `references/analysis-dimensions.md`），
> 避免分析报告基于过时的字段定义得出结论。

## 辅助工具

```bash
# 记忆客户端健康检查
python scripts/backend_doctor.py --format json

# QA 检索证据格式检查
python scripts/validate_evidence.py --input /path/to/qa_results.csv --strict

# LoCoMo 黑盒指标导出
python benchmarks/locomo/blackbox.py \
  --qa /path/to/run/qa_results.csv \
  --judge /path/to/run/judge_results.csv \
  --import-results /path/to/run/import_results.csv \
  --summary /path/to/run/summary.json \
  --out-dir /path/to/report

# 两次运行结果对比
python benchmarks/locomo/compare.py \
  --left /path/to/run-a \
  --right /path/to/run-b \
  --out-dir /path/to/comparison
```

各 benchmark 详细参数见对应 `docs/usage.md`。插件设计细节见
`plugins/README.md`，记忆后端设计细节见 `backends/README.md`。
