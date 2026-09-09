# EchoMem 六项本机压测

这一个文件包含完整的本机部署、配置、执行和结果解释。测试直接访问本机 EchoMem，
不需要服务器，也不要求把容器限制为 4U8G。报告会记录容器实际 CPU、内存和镜像，
因此不同电脑的容量数据应分别比较。

> 完整测试会对专用 EchoMem 容器注入租户故障，并在 M5 中执行真实 `kill -9` 和重启。
> 请勿指向日常开发、共享或生产容器。

## 测试内容

| 指标 | 测试动作 | 主要输出 |
| --- | --- | --- |
| M1 单实例容量与 DAU | 预注入真实记忆，按租户数和租户内热用户数逐档增加 Search、Commit 与混合负载，直到出现持续阻塞、失败、崩溃或积压不能恢复 | 每档 P95、吞吐、错误类型、质量分母、CPU、内存、最后正常档、首个拥塞档、三种业务画像 DAU 换算 |
| M2 多租户公平性 | 4 租户、8 租户使用独立凭证，以同档位请求并发 Search 和独立 Session Commit | 每租户 Commit 吞吐、Search P95、Commit Jain 指数、Search Jain 指数和零完成租户 |
| M3 Commit 洪泛优先级 | 先测热记忆 Search 基线，再运行均匀洪泛和单租户洪泛；仅统计与未完成 Commit 确认重叠的 Search | 基线/洪泛 Search P95、错误、召回质量、Commit 计划/202/拒绝/完成/未终态数量 |
| M4 单租户故障隔离 | 依次给一个租户注入 `delay` 和 `reject`，其余租户持续执行真实记忆 Search | 旁观租户故障前/中/后的 Search P95、错误率和劣化百分比 |
| M5 202 Commit 崩溃恢复 | Commit 返回 202 且尚未完成时 kill 容器，重启后只轮询原任务并重复提交幂等键 | 恢复终态，以及 history、archive、cursor 的消息集合、顺序、丢失和重复对账 |
| M6 分层分租户可观测性 | 全程采集受保护观测接口，并覆盖 NORMAL、QUEUE、REJECT、RESET | 每个 `tenant × lane` 的 queued、wait、exec、rejected 四元组，缺失帧、非法值和重启代际 |

测试是**观测型**的，不内置“P95 必须小于多少”之类性能门槛。`MEASURED` 表示规定的
数据分母已采集完整，不等于性能优秀；失败、超时、HTTP 200 但召回质量失败、Provider
异常和长期 pending Commit 都会原样进入报告。

每次运行还会生成两组横向证据：关键接口调用账本按 Search、Open、Add、Commit、
Commit 状态、History、Archive、Cursor、Metrics、故障控制和租户观测分别统计实际
调用次数与错误；负向契约探针独立检查缺认证、畸形 JSON、缺必填字段、错误字段类型、
不存在资源及非法故障类型，不把这些请求混入性能分母。接口和模块耗时分开显示：
客户端仅能证明 HTTP 端到端耗时，EchoMem 响应实际提供的 route/engine 阶段计时另表展示，
缺失的内部阶段保持“不可观测”，不使用 P95 相减猜测。

M3 同时包含均匀 Commit 洪泛和单租户洪泛。后者让一个租户承担全部 Commit，四个租户
继续独立 Search，用于观察不同租户负载与耗时是否串扰；它是异构/吵闹邻居场景，不能
拿来计算 M2 的等权 Jain 公平性。当前 EchoMem 故障控制作用于目标租户全部认证请求，
若要分别制造“仅 Search 慢”或“仅 Commit 慢”，服务端还需提供按 operation 选择的故障范围。

## 1. 准备环境

本机需要 macOS 或 Linux、Docker Compose、Git、Python 3.11+，以及可用的真实 LLM 和
Embedding 凭证。禁止使用 mock。EchoMem 被测版本还需包含故障控制和租户观测接口。

获取两个仓库。`ECHOMEM_DIR` 可以换成自己的绝对路径：

```bash
git clone https://github.com/tech-innovation-group/EchoMem.git
export ECHOMEM_DIR="$PWD/EchoMem"

git clone https://github.com/tech-innovation-group/Memory-System-Eval-Harness.git
cd Memory-System-Eval-Harness
git fetch origin pull/32/head:pr32-six-metrics
git switch pr32-six-metrics
git rev-parse HEAD
```

PR 合入后可直接切换合入后的目标分支。测试归档时保留最后一条命令输出的完整 commit。

## 2. 本机部署 EchoMem

使用 EchoMem 仓库自带的单节点 Compose，不设置 CPU 或内存上限：

```bash
cd "$ECHOMEM_DIR/deploy/single-node"
./manage.sh init
cp ../../configs/config.example.json ./config.json
```

编辑当前目录的 `.env` 和 `config.json`：

1. 以被测 EchoMem 代码中的 `configs/config.example.json` 为准，不使用测试平台模板；
2. 保留配置中的 `api_key_env`，把真实密钥只填入 `.env`；
3. 确认 `engine.enabled` 包含本次要测的真实记忆引擎；
4. LLM 与 Embedding 都必须可用，Search 返回 HTTP 200 不能替代模型预检；
5. 为压测专用控制面设置随机 token，并启用测试控制。

EchoMem Core 进程需要接收下面两个环境变量：

```dotenv
ECHOMEM_TEST_CONTROL_ENABLED=true
ECHOMEM_TEST_CONTROL_TOKEN=<本机随机长字符串>
```

同时确认 `deploy/single-node/compose.yaml` 的 `core.environment` 已透传这两个变量。被测版本
还必须提供：

```text
GET/POST /api/inspect/test-control/fault
GET      /api/inspect/tenant-observability
```

缺少这些接口时，M4 和 M6 会明确报告 `BLOCKED`，不能用客户端模拟结果替代。

启动并检查：

```bash
cd "$ECHOMEM_DIR/deploy/single-node"
./manage.sh up
./manage.sh status
./manage.sh smoke
curl -fsS http://127.0.0.1:8010/api/v1/system/ready
curl -fsS http://127.0.0.1:8010/metrics >/dev/null
```

取得 Core 容器名，后面填入 profile：

```bash
docker inspect --format '{{.Name}}' "$(docker compose ps -q core)"
```

输出通常类似 `/echomem-core-1`；profile 使用去掉开头 `/` 后的 `echomem-core-1`。

## 3. 安装测试平台

回到测试平台仓库根目录：

```bash
cd /absolute/path/to/Memory-System-Eval-Harness
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p .local-stress
```

## 4. 创建独立测试租户

M2 必须使用不同租户凭证；重复使用同一个 key 只能测到并发，不能证明租户公平。

```bash
.venv/bin/python -m performance.targets.echomem.provision \
  --base-url http://127.0.0.1:8010 \
  --count 32 \
  --out .local-stress/tenants.json \
  --env-file .local-stress/test.env
chmod 600 .local-stress/tenants.json .local-stress/test.env
```

`tenants.json` 只保存 `auth_key_env` 名称，真实租户 key 位于 Git 忽略的 `test.env`。
继续编辑 `test.env`，加入：

```dotenv
ECHOMEM_TEST_CONTROL_TOKEN=<与 EchoMem Core 完全相同的 token>
ECHOMEM_LLM_API_KEY=<真实 LLM key>
ECHOMEM_EMBEDDING_API_KEY=<真实 Embedding key>
```

若 `config.json` 使用其他 `*_api_key_env` 名称，也要把对应变量加入 `test.env`。测试平台
会在发压前分别验证 LLM 和 Embedding；任何一个失败都会阻止依赖真实记忆的场景。

## 5. 创建唯一的本机 profile

新建 `.local-stress/six-metrics.profile.json`，只放下面这一个 profile。将三处绝对路径和
容器名替换为本机实际值：

```json
{
  "profiles": [
    {
      "name": "Local",
      "base_url": "http://127.0.0.1:8010",
      "resource_container": "echomem-core-1",
      "require_4u8g": false,
      "tenant_config": "/absolute/path/to/Memory-System-Eval-Harness/.local-stress/tenants.json",
      "preflight_config": "/absolute/path/to/EchoMem/deploy/single-node/config.json",
      "m1_tenant_levels": [1, 2, 4, 8, 16, 32],
      "m1_user_levels": [1, 2, 4, 8],
      "m1_duration_s": 300,
      "m1_search_rps_per_user": 1,
      "dau_scenarios": [
        {"name": "read-heavy", "searches_per_user_day": 50, "commits_per_user_day": 5, "peak_to_average_ratio": 3},
        {"name": "balanced", "searches_per_user_day": 20, "commits_per_user_day": 20, "peak_to_average_ratio": 5},
        {"name": "write-heavy", "searches_per_user_day": 5, "commits_per_user_day": 50, "peak_to_average_ratio": 8}
      ],
      "fault_isolation": {
        "samples": 100,
        "repeats": 3,
        "token_env": "ECHOMEM_TEST_CONTROL_TOKEN"
      },
      "tenant_observability": {
        "token_env": "ECHOMEM_TEST_CONTROL_TOKEN"
      },
      "commit_recovery": {
        "allow_container_restart": true,
        "samples": 3,
        "messages": 12,
        "content_chars": 1000,
        "recovery_timeout_s": 180
      }
    }
  ]
}
```

`require_4u8g: false` 表示不检查固定 4U8G cgroup；Docker 未设置上限时 CPU 和内存字段
可能显示为 `0`，含义是使用宿主机默认资源。为保证数据可比较，报告还会保存容器 ID、
镜像 ID 和 Docker 资源配置。

## 6. 先运行快速链路检查

所有命令均在测试平台仓库根目录执行：

```bash
bash -n performance/targets/echomem/run_six_metrics.sh
performance/targets/echomem/run_six_metrics.sh quick \
  .local-stress/six-metrics.profile.json \
  results/local-six-metrics-quick \
  .local-stress/test.env
```

profile 文件只有一个 profile 时，脚本会自动选择 `Local`，不需要再写 `--profile`。
`quick` 使用真实 HTTP、模型、租户、故障和重启，但缩短采样时间，结果固定视为
`PARTIAL`，只用于确认整条链路能跑通。

## 7. 运行完整六项测试

```bash
performance/targets/echomem/run_six_metrics.sh full \
  .local-stress/six-metrics.profile.json \
  results/local-six-metrics-full \
  .local-stress/test.env
```

执行顺序为 `M1 → M2 → M3 → M4 → M5`，M6 从开始到结束持续采样。默认不运行 soak。
机器速度、模型限流和容量边界不同会影响总时长，M1 的逐档容量测试通常最耗时。

只测一项：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json \
  --metrics M1 \
  --env-file .local-stress/test.env \
  --out-dir results/local-m1
```

`--metrics` 可使用 `M1` 到 `M6`，也可传 `M1,M2,M3`。只测 M6：

```bash
performance/targets/echomem/run_six_metrics.sh m6 \
  .local-stress/six-metrics.profile.json \
  results/local-m6 \
  .local-stress/test.env
```

中断后使用原 profile、原输出目录和 `--resume`：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json \
  --env-file .local-stress/test.env \
  --out-dir results/local-six-metrics-full \
  --resume
```

## 8. 查看报告

打开 `results/local-six-metrics-full/report.html`。同目录关键证据：

| 文件 | 内容 |
| --- | --- |
| `report.html` | 六项总体结论、图表、测试方式、失败类型与 EchoMem 模块建议 |
| `summary.json` | 报告使用的结构化汇总和每项状态 |
| `suite.json` | 场景、探针与分母明细 |
| `records.csv` | 每个请求的延迟、状态和结果 |
| `metrics_samples.csv` | 测试期间 CPU、内存及 Prometheus 采样 |
| `execution-manifest.json` | 测试平台 commit、profile、模型预检和执行状态 |

| 状态 | 含义 |
| --- | --- |
| `MEASURED` | 该项要求的场景和分母完整，数据可用于分析 |
| `PARTIAL` | 有真实数据，但场景、重复次数或分母不完整 |
| `BLOCKED` | 配置、模型、租户、受保护接口或容器条件未满足 |
| `EXECUTION_ERROR` | 测试平台或执行过程发生异常 |

不要删除失败样本后重算，也不要只保留 HTML。容量结论必须同时给出最后正常档和首个持续
拥塞档；Provider 失败、EchoMem admission 拒绝、原子引擎质量失败和客户端传输错误会按
不同责任域拆开展示。

## 9. 本机清理与恢复

```bash
cd "$ECHOMEM_DIR/deploy/single-node"
./manage.sh status
./manage.sh smoke
./manage.sh down
```

`down` 不会删除 `deploy/single-node/data/workspace`。复测应使用新的结果目录，不要覆盖旧
目录，否则原始分母和版本证据会丢失。

## 常见阻塞

| 现象 | 处理 |
| --- | --- |
| `resource-container` 阻塞 | 核对 profile 容器名、Docker 是否运行以及当前用户是否可访问 Docker |
| `fault_isolation` 或 `tenant_observability` 阻塞 | 核对 EchoMem 接口、启用开关及 Core/runner 两侧 token 是否一致 |
| 模型预检失败 | 同时检查 LLM 与 Embedding endpoint、模型名、余额、限流和 `api_key_env` |
| Search 200 但无召回 | 检查种子 Commit、自然语言查询命中和返回正文中的预期事实 |
| M2 不能证明公平性 | 确认租户凭证各不相同，不能让多个 tenant 复用同一个 key |
| M5 未执行重启 | 只有 Commit 已返回 202 且仍未完成时才会 kill；确认目标是专用本机容器 |
| 容器 CPU/内存显示 0 | 本机模式下表示 Docker 未设 cgroup 上限，测试使用宿主机默认资源 |
