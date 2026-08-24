# Remote Server Evaluation Flow

本文档记录 `Memory-System-Eval-Harness` 在服务器上的真实执行方式，供人工或
其他 agent 复核。它描述的是服务器编排层，不改变 EchoMem 源码，也不替代本仓库
`benchmarks/locomo/run_eval.py` 的评测逻辑。

## 一键部署

Linux 服务器安装 Git、curl 和 Docker 后。PR 26 合并前验证部署分支：

```bash
git clone --branch feat/server-eval-deployment-config \
  https://github.com/tech-innovation-group/Memory-System-Eval-Harness.git \
  /opt/memory-eval-harness
cd /opt/memory-eval-harness
sudo deploy/install_server.sh
```

PR 合并后把 `--branch feat/server-eval-deployment-config` 改为
`--branch v3_mcpTool`。

首次执行会生成 `/opt/memory-eval-web/server.env` 并退出。填写以下配置后再次执行：

部署脚本同时安装只读 Codex 监控入口：

```bash
codex --version
codex-monitor <任务ID>
```

Codex 只读取任务、Docker 和结果证据，不修改 EchoMem、PR、测试配置或容器。
配置 `OPENAI_API_KEY` 后才能执行模型分析；没有 Key 时 `codex-monitor` 仍会
输出本地任务摘要。详情见 [CODEX_SERVER_README.md](CODEX_SERVER_README.md)。

```dotenv
DEFAULT_LLM_API_KEY=你的DeepSeekKey
DEFAULT_EMBEDDING_API_KEY=你的DashScopeEmbeddingKey
SESSION_SECRET=随机长字符串
PUBLIC_BASE_URL=http://服务器公网IP:8081
FEISHU_APP_ID=飞书应用ID
FEISHU_APP_SECRET=飞书应用密钥
# 飞书启用 Encrypt Key 时再填写：
FEISHU_ENCRYPT_KEY=
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

`PUBLIC_BASE_URL` 必须填写飞书能够访问的 IP 或域名，例如
`http://服务器公网IP:8081`。程序会自动把这个地址的 hostname 加入
`ALLOWED_HOSTS`，否则公网访问和 Feishu 回调会返回 HTTP 400。

## Feishu callback checklist

If a group message produces no reply and `jobs.json` has no new task, check the
Feishu application before debugging the evaluator:

```text
[ ] Request URL is http://服务器公网IP:8081/feishu/events
    (or the matching PUBLIC_BASE_URL host)
[ ] Event subscription uses the developer-server callback mode
[ ] Event `im.message.receive_v1` is subscribed
[ ] The latest application version is published after changing events or URL
[ ] The bot is installed in the target group and the message @mentions the bot
[ ] The app has permission to receive messages and send messages as the bot
[ ] If Encrypt Key is enabled, `FEISHU_ENCRYPT_KEY` is configured on the server
```

The server-side smoke checks are:

```bash
curl -fsS http://127.0.0.1:8081/
curl -fsS -X POST http://127.0.0.1:8081/feishu/events \
  -H 'Content-Type: application/json' \
  --data '{"type":"url_verification","challenge":"probe","token":"probe"}'
docker logs --since 10m memory-eval-web
```

An unchanged `jobs.json` together with no `Feishu event received` log means
the message did not reach this server. In that case, changing evaluator or
EchoMem code cannot fix the missing reply.

Feishu callbacks are acknowledged immediately. Command parsing, LLM intent
classification, task creation, and the outgoing bot message run in a
background worker so a slow model request cannot make Feishu retry the event
or show no response. Every received event is also recorded (without secrets)
in:

```text
/data/feishu-events.jsonl
```

Inspect the most recent callback records with:

```bash
tail -n 20 /data/feishu-events.jsonl
```

Useful outcomes are:

```text
accepted
job_created
job_create_failed:...
ignored_missing_chat_id
ignored_all_members_mention
```

`ignored_missing_chat_id` means Feishu delivered an event without a usable
conversation ID, so the platform cannot send a reply or attach the task to a
group. Check that the bot is installed in the group and that the subscribed
event is `im.message.receive_v1`.

When Feishu encryption is enabled, the request body contains an `encrypt` field.
The Web service decrypts it with `FEISHU_ENCRYPT_KEY`; a missing or invalid key
returns HTTP 400 and writes an explicit error to the Web log.

固定执行清单见 [ECHOMEM_EVAL_RUNBOOK.md](ECHOMEM_EVAL_RUNBOOK.md)。本文档保留
服务器目录、入口和复核命令；实际执行时先读 runbook，再按本文档定位文件。

## 代码和服务位置

服务器默认目录：

```text
/opt/memory-eval-harness/              # 测试平台 Git checkout
/opt/memory-eval-web/                  # Web/飞书运行目录
/opt/memory-eval-sources/              # EchoMem 源码和 commit 缓存
/opt/memory-eval-harness/results/      # 任务结果目录
/opt/memory-eval-web/cache/recall/     # 共享 embedding warm-up files
/opt/memory-eval-web/data/skills/echomem-eval-startup/
                                      # 宿主机上的启动 Skill 和故障记录
```

服务器 Web 入口：

```text
/opt/memory-eval-harness/deploy/web_app_server.py
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

飞书固定命令进入 `deploy/web_app_server.py`，创建任务后由后台 worker 调用：

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
7. 根据依赖声明文件指纹复用 EchoMem 依赖镜像；源码变化但依赖不变时只
   更换任务源码挂载，依赖变化才重新构建。
8. 将当前任务源码挂载到 runner，停止旧 EchoMem 进程，创建独立 workspace。
9. 复用只读的共享 `semantic_embeddings.json` 和（存在时）
   `template_embeddings.json`，但 workspace/cache、tenant、session 和 memory 状态
   按任务隔离。
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
/opt/memory-eval-web/data/skills/echomem-eval-startup/SKILL.md
/opt/memory-eval-web/data/skills/echomem-eval-startup/preflight.sh
/opt/memory-eval-web/data/skills/echomem-eval-startup/incidents.jsonl
```

任务失败时平台会自动向 `incidents.jsonl` 追加脱敏记录。提交新任务前执行：

```bash
/opt/memory-eval-web/data/skills/echomem-eval-startup/preflight.sh --strict
```

## 其他 agent 复核命令

在服务器上执行：

```bash
cd /opt/memory-eval-harness
git rev-parse HEAD
git status --short
grep -n 'def prepare_echomem_source' deploy/web_app_server.py
grep -n 'def run_source_job' deploy/web_app_server.py
find /opt/memory-eval-web/data/skills/echomem-eval-startup -maxdepth 2 -type f -print
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
