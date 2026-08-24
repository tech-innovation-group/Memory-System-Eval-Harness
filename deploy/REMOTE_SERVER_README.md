# Remote Server Evaluation Flow

本文档记录 `Memory-System-Eval-Harness` 在服务器上的真实执行方式，供人工或
其他 agent 复核。它描述的是服务器编排层，不改变 EchoMem 源码，也不替代本仓库
`benchmarks/locomo/run_eval.py` 的评测逻辑。

## 一键部署

Linux 服务器安装 Docker 后：

```bash
git clone https://github.com/tech-innovation-group/Memory-System-Eval-Harness.git \
  /opt/memory-eval-harness
cd /opt/memory-eval-harness
sudo deploy/install_server.sh
```

首次执行会生成 `/opt/memory-eval-web/server.env` 并退出。填写以下配置后再次执行：

```dotenv
DEFAULT_LLM_API_KEY=你的DeepSeekKey
DEFAULT_EMBEDDING_API_KEY=你的DashScopeEmbeddingKey
SESSION_SECRET=随机长字符串
```

脚本会构建 runner/Web 镜像、创建数据目录、启动 `memory-eval-web`，并检查
`http://服务器IP:8081/`。更新代码时执行：

```bash
cd /opt/memory-eval-harness
git pull --ff-only
sudo deploy/install_server.sh
```

脚本不会删除任务数据、源码缓存或历史结果；结果按配置保留 3 天。查看服务：

```bash
docker logs -f memory-eval-web
docker ps
curl -fsS http://127.0.0.1:8081/
```

DeepSeek 只负责 LLM/Judge，DashScope `text-embedding-v3` 只负责 embedding；
两个 API Key 和 endpoint 不要混用。完整故障处理见
[ECHOMEM_EVAL_RUNBOOK.md](ECHOMEM_EVAL_RUNBOOK.md)。

固定执行清单见 [ECHOMEM_EVAL_RUNBOOK.md](ECHOMEM_EVAL_RUNBOOK.md)。本文档保留
服务器目录、入口和复核命令；实际执行时先读 runbook，再按本文档定位文件。

## 代码和服务位置

服务器默认目录：

```text
/opt/memory-eval-harness-latest/       # 测试平台 Git checkout
/opt/memory-eval-web/                  # Web/飞书运行目录
/opt/memory-eval-sources/              # EchoMem 源码和 commit 缓存
/opt/memory-eval-web/results/          # 任务结果目录
/opt/memory-eval-web/cache/recall/     # 共享 semantic_embeddings.json
/data/skills/echomem-eval-startup/    # 启动故障技能和历史案例
```

注意：服务器的 `deploy/` 运行层目前是服务器工作区中的未跟踪文件，不属于
`54878f3` 这个测试平台 Git commit。复核时必须同时检查 Git checkout 和
`deploy/web/app.py`，不能只看 `git log` 就认为线上编排层已经被版本化。

服务器 Web 入口：

```text
/opt/memory-eval-harness-latest/deploy/web/app.py
```

服务器 Harness 启动脚本：

```text
/opt/memory-eval-harness-update-20260820/deploy/harness/run-harness.sh
```

常驻 runner 由 `app.py` 管理。依赖没有变化时复用镜像；每个任务仍使用独立的
源码路径、EchoMem workspace、tenant/session 和结果目录，因此同一个 commit 可以
重复测试，不会直接复用旧准确率。

## 飞书/Web 到评测脚本

固定命令：

```text
测试 develop
测试 PR 227
状态 <任务ID>
结果 <任务ID>
```

飞书固定命令进入 `deploy/web/app.py`，创建任务后由后台 worker 调用：

```text
benchmarks/locomo/run_eval.py
```

实际命令的关键参数为：

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
```

QA/Judge 并发由任务配置传入，当前服务器单并发队列通常使用 `1/1`。实际值必须
以任务结果目录的 `config.json` 和 `summary.json` 为准。

## PR 任务的真实顺序

1. 请求 GitHub 最新 `EchoMem/develop` commit。
2. `develop` 任务下载或复用该 commit 的源码缓存。
3. PR 任务读取 PR 状态、目标分支、head commit 和当前合并快照。
4. 关闭 PR、目标分支不是 `develop`、或合并快照不可用时停止，不启动 EchoMem。
5. 可合并时使用 GitHub 生成的 `pull/<number>/merge.tar.gz`，实际测试的是当前
   `develop + PR` 的合并结果，而不是只测试 PR head。
6. 从 checkout 内的 `configs/config.example.json` 生成任务配置；没有该文件时直接
   失败，不回退到测试平台模板。
7. 根据依赖声明文件指纹复用长期 runner 镜像；依赖变化才重新构建。
8. 将当前任务源码挂载到 runner，停止旧 EchoMem 进程，创建独立 workspace。
9. 复用只读的共享 `semantic_embeddings.json`，但 workspace/cache、tenant、session
   和 memory 状态按任务隔离。
10. 启动 EchoMem，进行容器内健康检查和网络健康检查。
11. 依次执行记忆注入、81 题 QA、Judge 和结果汇总。
12. 保存结果、日志和诊断，回传飞书摘要，并尝试上传结果压缩包。

## EchoMem 运行时覆盖

当前服务器对 EchoMem 任务注入以下运行时环境：

```text
ECHOMEM_AUTO_COMMIT_THRESHOLD=20000
ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE=0.7
```

服务器默认模型配置为：

```text
DEFAULT_LLM_BASE_URL=https://api.deepseek.com/v1
DEFAULT_LLM_MODEL=deepseek-v4-flash
DEFAULT_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DEFAULT_EMBEDDING_MODEL=text-embedding-v3
```

模型覆盖只作用于服务器任务环境；EchoMem 源码本身不修改。embedding、rerank、engine
开关和旧/新配置字段应以被测 checkout 的 `config.example.json` 为准。API Key 只通过
环境变量传递，不写入 Git、配置结果或日志。

## 结果和诊断文件

每个任务结果目录至少应检查：

```text
summary.json
config.json
qa_results.csv
judge_results.csv
retrieval_traces.jsonl
agent_traces/
container.log
echomem.inspect.*.json
echomem.logs.*.txt
```

判断“空召回”时优先看 `summary.json` 的：

```text
retrieval_coverage
empty_retrieval_count
empty_retrieval_rate
retrieval_observed_count
retrieval_errors
```

Judge 空响应应看 `judge_results.csv` 和 `container.log`，不能误判成空召回。
Judge 异常按当前规则作为错误计入总题数分母。

## 启动失败处理

健康检查失败时，平台应：

1. 保存 Docker inspect 和 Docker logs；
2. 优先在 EchoMem 容器内检查 `127.0.0.1:8010/health`；
3. 检查容器状态、退出码、OOMKilled、启动命令和 workspace 日志；
4. 自动重启 EchoMem 一次；
5. 仍失败时分类为 EchoMem 代码、EchoMem 配置、测试平台、依赖、模型服务或
   Docker/服务器问题，并明确“是否需要修改 EchoMem”。

故障经验持久化在：

```text
/data/skills/echomem-eval-startup/SKILL.md
/data/skills/echomem-eval-startup/preflight.sh
/data/skills/echomem-eval-startup/incidents.jsonl
```

## 其他 agent 复核命令

在服务器上执行：

```bash
cd /opt/memory-eval-harness-latest
git rev-parse HEAD
git status --short
sed -n '612,660p' deploy/web/app.py
sed -n '980,1210p' deploy/web/app.py
sed -n '1317,1450p' deploy/web/app.py
find /data/skills/echomem-eval-startup -maxdepth 1 -type f -print
```

在某个任务结果目录执行：

```bash
cat summary.json
cat config.json
grep -n 'conv-30_qa71' qa_results.csv judge_results.csv
grep -nE 'empty|retrieval|Judge|health|error' container.log
```

结论应分成两层报告：

1. **平台流程是否按本 README 执行**：检查源码 commit、merge base、配置来源、
   runner/image、健康检查和最终调用命令。
2. **EchoMem 评测结果**：检查注入、召回、QA、Judge 和准确率，不能把平台启动
   故障、Judge 空响应或上传失败混入 EchoMem 算法结论。
