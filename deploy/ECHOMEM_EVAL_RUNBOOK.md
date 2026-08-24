# EchoMem 评测固定执行手册

本文档是服务器 Web/飞书评测任务的唯一操作手册。后续执行任务时按本文档顺序
操作；遇到新问题时，只在“故障经验”章节追加案例，不临时修改 EchoMem 源码。

## 目标和边界

- 测试对象：EchoMem 的 `develop` 或指定开放 PR。
- Benchmark：`locomo-conv30`，固定 81 道题。
- 评测顺序：记忆注入 -> QA -> Judge -> 汇总。
- 工具调用：关闭。
- MCP 对话读取：关闭。
- 首次检索：按当前 harness 合同保留一次 MCP `memory_query`，不能因为关闭工具
  调用而变成 81 道空召回。
- Judge 异常：按错题计入 `81` 的分母，不能隐藏。
- 测试平台不得修改、修复、打补丁或提交 EchoMem 代码。

## 唯一固定配置

以下配置由服务器环境提供，不写入 EchoMem 源码，也不在聊天消息中填写：

```text
DEFAULT_LLM_BASE_URL=https://api.deepseek.com/v1
DEFAULT_LLM_MODEL=deepseek-v4-flash
DEFAULT_LLM_API_KEY=<服务器环境变量>
DEFAULT_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DEFAULT_EMBEDDING_MODEL=text-embedding-v3
DEFAULT_EMBEDDING_API_KEY=<服务器环境变量，必须是 Embedding provider 的 Key>

ECHOMEM_AUTO_COMMIT_THRESHOLD=20000
ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE=0.7

QA concurrency=1
Judge concurrency=1
top-k=25
```

API Key 处理规则：

1. 拉取 checkout 后读取该版本 `configs/config.example.json` 中声明的
   `api_key_env`。
2. 使用服务器的 `DEFAULT_LLM_API_KEY` 为 LLM 类环境变量提供值，使用
   `DEFAULT_EMBEDDING_API_KEY` 为 embedding/rerank 类环境变量提供值；真实 Key
   不写入 `config.json`、日志、结果文件或 Git。两者不能因为模型名称相似而混用。
3. 只覆盖 LLM、Intent、Memrouter 等模型的 endpoint/model；embedding、rerank、
   engine 开关和其他字段以被测 checkout 为准。
4. 如果配置声明了新的 API Key 环境变量，先在任务日志记录变量名，检查它是否被
   注入，再启动服务；缺少 Key 时在启动前失败并明确提示。

## 固定执行流程

### 0. 新任务快速启动

服务器编排层应优先复用长期 Python/runner 环境，不为每个 PR 重建 EchoMem
镜像。源码、配置、workspace 和 tenant 仍然按任务隔离。对于已经 checkout 好的
EchoMem commit，可直接使用：

```bash
export DEFAULT_LLM_BASE_URL=https://api.deepseek.com/v1
export DEFAULT_LLM_MODEL=deepseek-v4-flash
export DEFAULT_LLM_API_KEY=<服务器环境变量>
export DEFAULT_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export DEFAULT_EMBEDDING_MODEL=text-embedding-v3
export DEFAULT_EMBEDDING_API_KEY=<服务器环境变量>
export ECHOMEM_AUTO_COMMIT_THRESHOLD=20000
export ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE=0.7

deploy/start_echomem_eval.sh \
  --source /work/source-root/<job_id>/echomem \
  --workspace /work/source-root/<job_id>/workspace \
  --cache-dir /opt/memory-eval-web/cache/recall \
  --port 8010 \
  --mcp-port 8001
```

脚本会从当前 checkout 的 `configs/config.example.json` 生成配置，导出该配置声明
的 `api_key_env`，复用只读的 `semantic_embeddings.json` 和（存在时）
`template_embeddings.json`，并等待 `/health` 成功后才返回。它不会修改 EchoMem
checkout。任务结束后执行：

```bash
deploy/stop_echomem_eval.sh /work/source-root/<job_id>/workspace
```

服务器上应由 Docker、systemd 或 Web worker 托管启动脚本产生的进程；本地临时
shell 退出时，桌面执行器可能主动回收后台子进程，这不代表服务器托管方式失败。

### 1. 任务前检查

```text
[ ] Docker daemon 可访问
[ ] 磁盘空间和 Docker 空间足够
[ ] 共享 cache/recall/semantic_embeddings.json 存在且非空
[ ] 若 checkout 使用模板检索，共享 cache/recall/template_embeddings.json 存在
[ ] 没有其他运行中的评测任务
[ ] 当前 runner 镜像和依赖指纹可复用，或明确需要重建
```

服务器预检：

```bash
/data/skills/echomem-eval-startup/preflight.sh
```

### 2. 固定代码版本

对每个任务都重新读取最新 `EchoMem/develop` commit，并记录：

```text
develop_commit
pr_head_sha
merge_commit
```

`develop` 任务使用最新 develop。PR 任务必须满足：

- PR 状态为 `open`；
- 目标分支为 `develop`；
- GitHub 当前 `pull/<pr>/merge` 快照可下载；
- 关闭 PR、合并快照不可用或确实存在冲突时，不启动 EchoMem。

源码按 commit 缓存。缓存只避免重复下载，不复用旧的准确率结果。

### 3. 准备配置和 API Key

```text
[ ] 使用 checkout 内的 configs/config.example.json
[ ] 解析并记录所有 api_key_env
[ ] 生成任务专属 config.json
[ ] 仅覆盖服务器模型 endpoint/model
[ ] 注入 API Key 环境变量
[ ] 写入 ECHOMEM_AUTO_COMMIT_THRESHOLD=20000
[ ] 写入 ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE=0.7
[ ] 不修改 source/ 下任何 EchoMem 文件
```

没有 `configs/config.example.json` 时直接失败，不回退到旧平台模板。

### 4. 复用环境并隔离任务

- 依赖声明文件没有变化：复用长期 runner 镜像。
- 依赖声明文件变化：按新的依赖指纹构建镜像。
- 每次任务都切换到自己的源码目录。
- 每次任务都使用新的 workspace、tenant、session、memory 状态和结果目录。
- 只共享静态 `semantic_embeddings.json` 和（存在时）`template_embeddings.json`；
  禁止共享 tenant、session、memory DB、
  traces 或旧任务 workspace。
- 每个任务生成临时 Registry provisioning capability，只注入 EchoMem/评测容器
  环境；harness 用它完成 tenant/user/key 创建，任务结束后随容器销毁，不写入
  jobs、日志或结果文件。若任务显式提供 `--echomem-auth-key`，则直接复用该身份，
  不重复 provision。
- 评测容器统一复用长期 runner 镜像；当前服务器镜像为
  `memory-eval-harness:20260823-auth-fix`。依赖指纹未变化时不重建镜像，
  EchoMem 镜像也按 commit 缓存复用。
- Web 容器内的 `/results` 与 Docker 宿主机挂载路径分离；创建评测容器时使用
  `HOST_RESULTS_DIR=/opt/memory-eval-harness/results`，避免结果目录权限错误。
- 任务级 `cache/recall/semantic_embeddings.json` 和（存在时）
  `template_embeddings.json` 从共享缓存复制；semantic cache 按
  `model=text-embedding-v3, dimensions=1024` 校正 fingerprint；不会共享记忆、
  session 或 tenant。
- 启动新任务前停止旧 EchoMem 进程，确认端口 `8010` 已释放。

### 5. 启动和健康检查

启动命令必须使用当前 checkout：

```bash
PYTHONPATH=/work/EchoMem/src \
echomem server \
  --host 0.0.0.0 \
  --port 8010 \
  --workspace /work/source-root/<job_id>/workspace \
  --config /work/source-root/<job_id>/config.json
```

健康检查顺序：

1. 容器内 `http://127.0.0.1:8010/health`；
2. 容器内 MCP 端口 `8001`；
3. Docker bridge IP 的 `8010/health`。

健康窗口为 300 秒。失败时：

1. 先保存 `docker inspect` 和 `docker logs`；
2. 保存退出码、OOMKilled、容器状态、启动命令和 EchoMem workspace 日志；
3. 自动重启 EchoMem 一次；
4. 再次检查仍失败则停止任务并分类，不继续注入记忆。

### 6. 执行评测

核心脚本：

```text
benchmarks/locomo/run_eval.py
```

服务器固定参数：

```text
--agent-plugin echomem_mcp
--sample conv-30
--no-tool-calling
--no-search-in-tools
--mcp-read-mode disabled
--top-k 25
--memory-budget-chars 8000
--user-memory-budget-chars 4000
--agent-memory-budget-chars 2000
--llm-temperature 0.7
--question-timeout-s 600
--llm-timeout-s 600
--llm-retries 3
--concurrency 1
--judge-concurrency 1
```

阶段顺序必须是：

```text
导入全部 LoCoMo session
-> 等待 commit/extraction 完成
-> 81 题 QA（只检索，不写回记忆）
-> 81 题 Judge
-> 汇总准确率
```

### 7. 结果验收

成功任务必须保留：

```text
summary.json
config.json
qa_results.csv
judge_results.csv
retrieval_traces.jsonl
agent_traces/
container.log
```

启动失败或健康检查失败还必须保留：

```text
echomem.inspect.*.json
echomem.logs.*.txt
echomem.runner-log.*.txt
```

最终消息必须明确：

```text
LoCoMo / conv-30
分支和 PR
develop commit / merge commit
准确率: 正确数/81
Judge 异常数量
任务 ID和详情链接
```

任务结束后停止 EchoMem 服务，保留长期 runner 和 commit 镜像。

## 故障经验追加模板

每次遇到新问题，在本节末尾追加一条，不覆盖历史案例：

```markdown
### YYYY-MM-DD 任务 <job_id>：<简短标题>

- 现象：
- 阶段：prepare / health / import / qa / judge / upload
- 代码：develop 或 PR <number>
- develop commit：
- PR head / merge commit：
- 实际命令或关键配置：
- 关键证据文件：
  - `container.log`
  - `echomem.inspect.<stage>.json`
  - `echomem.logs.<stage>.txt`
- 根因分类：EchoMem 代码 / EchoMem 配置 / 测试平台 / 依赖 / 模型服务 /
  Docker-服务器 / Git 合并
- 是否需要修改 EchoMem：是 / 否 / 证据不足
- 自动处理动作：
- 下次固定动作：
- 是否已加入 `incidents.jsonl`：是 / 否
```

### 2026-08-22 任务 pr340-local：新版身份 bootstrap 与启动缓存

- 现象：harness 创建 tenant 后创建 user 返回 401；另一次启动在
  `service_starting` 阶段长时间等待模型网络连接。
- 阶段：prepare / health
- 代码：PR 340 合入后的固定 commit
  `89108010bf77b35a383039603c6ff7db52d17d41`
- 根因分类：测试平台配置 / 模型服务连接
- 关键证据：
  - 本地 EchoMem 日志中的 `Registry provisioning access is denied`
  - 启动进程的 socket/TLS 连接等待
  - 旧 `semantic_embeddings.json` 的 fingerprint 与当前
    `text-embedding-v3` 配置不一致
- 是否需要修改 EchoMem：否
- 自动处理动作：
- 每任务临时 Registry provisioning capability 通过请求 header 传递，用于后续
  user/key 请求；
- 显式 auth key 任务直接复用身份，不重复 provision；
- 评测 runner 修复代码固化到 `memory-eval-harness:20260823-auth-fix`，不再只
  修改常驻 runner 容器；
- Docker 评测结果使用宿主机绝对路径挂载，修复 `/app/results/<job_id>` 权限；
  - 启动脚本按当前 embedding model/dimensions 校正共享 cache 元数据；
  - 复用 Python/runner 环境，不重复构建镜像。
- 下次固定动作：使用 `deploy/start_echomem_eval.sh`，为每个任务提供新
  workspace、tenant 和结果目录；健康通过后再进入注入。

分类原则：

- 没有 Docker 日志、退出码或配置证据时，不要直接归因 EchoMem。
- Judge 空响应是 Judge/模型响应异常，不是空召回。
- `empty_retrieval_count > 0` 才能判定存在空召回。
- API Key、401、403、429、上游超时归类模型服务问题。
- `HostIp`、`HostPort`、Docker JSON、端口映射错误归类测试平台问题。
- `ModuleNotFoundError` 或 pip 构建失败先归类依赖问题。

## 其他 agent 的复核要求

其他 agent 接手任务时必须先阅读本文档，然后输出以下检查结果：

```text
1. 当前执行是否按本手册顺序
2. 实际 checkout/commit 是否正确
3. API Key 环境变量是否完整注入（只报告变量名，不报告值）
4. threshold 和 extraction temperature 是否生效
5. workspace 是否和其他任务隔离
6. health、注入、QA、Judge 是否完整
7. 是否存在空召回或 Judge 异常
8. 出错时是否需要修改 EchoMem
```

禁止仅凭飞书摘要判断。必须检查任务目录中的 `config.json`、`summary.json` 和
`container.log`。
