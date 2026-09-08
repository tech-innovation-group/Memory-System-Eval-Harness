# EchoMem 4U8G 六项黑盒观测运行手册

本入口只回答“本次真实环境观测到了什么”，不回答“是否达标”。它不设置 P95、
准确率、Jain、吞吐或劣化比例门槛。所有 HTTP 错误、超时、空召回、未发送请求、
Commit pending/failed 都保留在分母。最终数据状态只使用 `MEASURED`、`PARTIAL`、
`BLOCKED`、`EXECUTION_ERROR`。

## 安装

在仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

需要一个专用 EchoMem 测试容器。容器必须实际限制为 4 CPU、8 GiB，并允许测试
进程读取 Docker inspect；M5/M6 RESET 会对该容器执行 kill-9 和启动。不要填写
共享、生产或名称模糊的容器。

## 凭据与配置

复制 `performance/targets/echomem/docs/six-metrics.profile.example.json` 到 Git
忽略的本地目录。`tenant_config` 至少配置 8 个独立租户，每个凭据只引用环境变量：

```json
{
  "tenants": [
    {
      "tenant_id": "tenant-1",
      "user_id": "stress-user-1",
      "account_id": "stress-account-1",
      "agent_id": "stress-agent-1",
      "auth_key_env": "ECHOMEM_TENANT_1_KEY"
    }
  ]
}
```

禁止在 JSON、Git、日志或报告中写 `auth_key`。观测入口发现明文 key 会直接拒绝
运行。真实 LLM、embedding 和故障控制 token 同样只从环境变量读取：

```bash
export ECHOMEM_TENANT_1_KEY='...'
# 继续设置 ECHOMEM_TENANT_2_KEY ... ECHOMEM_TENANT_8_KEY
export ECHOMEM_TEST_CONTROL_TOKEN='...'
export YOUR_LLM_API_KEY='...'
export YOUR_EMBEDDING_API_KEY='...'
```

若专用服务允许 bootstrap 注册，可一次生成引用环境变量的租户文件和本地密钥文件：

```bash
.venv/bin/python -m performance.targets.echomem.provision \
  --base-url http://127.0.0.1:8010 --count 32 \
  --out .local-stress/tenants.json \
  --env-file .local-stress/test.env
```

不要省略 `--env-file` 后再把生成的明文 `auth_key` JSON 用于观测入口；该入口会拒绝运行。
模型密钥和测试控制 Token 继续追加到同一个 `test.env`，运行时通过 `--env-file` 加载。

`preflight_config` 必须指向 EchoMem 实际使用的 JSON 配置。运行器从该配置推导
expected lanes，不接受在测试配置里写死四条 lane。它会在发压前真实调用 LLM 和
embedding endpoint，mock/fake 或错误模型不会生成成功结果。

## 完整运行

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json \
  --profile 4U8G \
  --out-dir results/echomem-4u8g-$(date +%Y%m%d-%H%M%S)
```

完整运行包括 M1 的跨租户 T 阶梯、租户内 U 阶梯及 Search/Commit/mixed/hotspot
四类开放到达负载；M2 的 24 个故障用例；M3 的 4/8 租户等需求窗口；M4 的
baseline/均匀洪泛/单租户洪泛；M5 的三个独立 kill-9 样本；M6 的 2 秒时序采样。
默认关闭 soak。真实模型速度和故障恢复时间不同，通常需要 8 到 16 小时。

## 单项与 quick

用 `--metrics` 选择单项：

```bash
# 将 M1 替换为 M2、M3、M4、M5 或 M6
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json --profile 4U8G \
  --out-dir results/m1 --metrics M1
```

M6 单项会自动执行 NORMAL、QUEUE、REJECT、RESET 所需依赖负载，但不会把这些
依赖数据冒充为其他指标的完整执行。quick smoke 使用短窗口和小样本，报告固定标记
`quick-non-complete` 与 `PARTIAL`，不能与完整采样混用：

```bash
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles .local-stress/six-metrics.profile.json --profile 4U8G \
  --out-dir results/smoke --quick
```

quick 一般需要 10 到 40 分钟，取决于真实模型和 Commit 恢复时间。

## 续跑、停止与清理

同一命令追加 `--resume`。已完成的 case 和 M1 子目录会跳过，未完成的通用 case
从第一个缺失 `summary.json` 的场景继续。中断时按 `Ctrl-C`；每个故障用例都在
`finally` 中关闭故障，M5 每次 kill 后都会启动目标容器。

中断后仍应人工确认专用容器已运行，并用控制接口执行一次 disable：

```bash
docker inspect echomem-stress-4u8g --format '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}'
curl -fsS -X POST http://127.0.0.1:8010/api/inspect/test-control/fault \
  -H "X-EchoMem-Test-Token: $ECHOMEM_TEST_CONTROL_TOKEN" \
  -H 'Content-Type: application/json' -d '{"action":"disable"}'
```

## 产物

结果根目录固定保留：

- `execution-manifest.json`：运行时间、Git commit、所选指标、环境摘要和探针执行记录
- `suite.json`：场景、探针及完整原始引用；`tenant_observability_monitor` 保留本机单调时钟窗口、采样间隔上限及采集异常类型
- `records.csv`：合并后的逐请求记录
- `metrics_samples.csv`：合并后的资源/Prometheus 采样
- `summary.json`：M1-M6 四态观测汇总
- `tenant-observability-samples.json`：M6 两秒快照、队列和重启分段来源
- `tenant-observability-before.json`：M6 发压前同步基线，用于计算计数器增量
- `fault-isolation-*.json`、`commit-recovery-*.json`：逐探针证据
- `report.html`：结论先行的最终 HTML

汇总值均可追溯到 JSON/CSV。字段未采集时保持 `null`；运行器不会删除失败样本、
隐藏错误或用 0 填补缺失值。

### M3/M4 洪泛补测的 Commit 证据

`performance.targets.echomem.acceptance.contention_matrix` 的固定租户洪泛补测会保存
每次提交的 HTTP 状态、可识别公共原因码、Retry-After、受理时间和终态轮询历史。
综合报告按轮次列出计划/记录数、202、HTTP 拒绝、未知原因、完成、失败与未终态。

负载中的 Commit 只提交一次：503/429 不自动重提，不能用后来成功的尝试覆盖拒绝。
轮询是对已受理原任务的只读观察，可在原期限内继续；轮询的错误也全部保留。
503 本身不能证明限流、模型欠费或 API key 异常，必须结合实际原因码和服务证据。
未识别的原因仅标 `UNRECOGNIZED`；历史未记录原因标 `NOT_RECORDED`，不导出响应正文。

兼容 EchoMem 的字符串 `error` 响应（如 `HTTP_LANE_SATURATED`、
`HTTP_INGRESS_SATURATED`、`COMMIT_UNAVAILABLE`），只导出固定公共枚举。
这一区分可用于定位 HTTP 入口拒绝、Commit 服务不可用等不同阶段，不能仅凭503推断。
采集器修复不会回填历史缺失原因，也不会改变历史样本的通过状态。

洪泛计划数与实际受理量分开。若锁定场景要求至少32个已受理Commit而实际只有16个，
报告会同时展示16次受理、剩余拒绝和真实Search结果，但不会将该轮判为完整洪泛证据。

### M6 过程完整性

M6 不只比较首尾。每一帧均使用运行前锁定的 `tenant × lane` 分母，空帧、
缺失字段、重复行、布尔值、负数及 NaN 不会被跳过。报告同时展示有效快照数、
每帧有效单元数，以及在全部采样帧中均完整的单元数；后者不是服务可用租户数量。

采样器在一次请求结束后等待 2 秒；HTTP 超时为 15 秒。默认最大采样空档为 20 秒，
可通过 `tenant_observability.max_sampling_gap_s` 调整。该参数约束证据完整性，
不是 Search 性能合格线。比较包括负载窗口首尾；旧产物没有本机时钟边界时，
只计算实际相邻帧间隔，不补造窗口覆盖证据。

计数器只在已确认的同一进程内做差。`queued` 下降是正常现象，累计计数下降
需要解释：同进程下降记录为异常；已确认重启则分段，不跨进程相减；身份未知
保持证据不足。M6 的 RESET 必须来自进程身份变化，不能仅凭计数下降或 PID
缺失推断重启。故障期间无法获取的帧仍保留，不能借“计划重启”隐藏采集失败。

只有每帧覆盖、采样间隔和进程分段核验完整，且实际观察到四种行为，M6 才为
`MEASURED`；缺数据为 `PARTIAL`，没有有效分母为 `BLOCKED`，采集失败或同进程
计数异常为 `EXECUTION_ERROR`。这些状态不代表服务性能是否达标。

## EchoMem 接口契约

当前完整执行需要：公开 Search、session open/message/Commit、commit status、history、
archive、cursor，以及受保护的 `/api/inspect/test-control/fault` 和
`/api/inspect/tenant-observability`。后者需返回 tenant、lane、queued、
wait/exec seconds total、rejected/accepted/completed/failed total。可靠切分 RESET
还需 `process_started_at`（可附 `process_id`）或每进程唯一的 `boot_id`；单独 PID
可能复用，不足以证明一直是同一进程。缺接口或身份字段时报告会
标记 `BLOCKED/PARTIAL`，不会降低测试要求。
