# 六项 4U8G 黑盒压测：交付与运行手册

## 1. 拉取与安装

本改动基于 PR31，单独提交新 PR，不更新 PR29 或 PR31。请检出包含本手册的新增 PR 分支，
而不是仅检出源仓库 v3；新增的容量观测模块未随 PR31 发布。
运行不需要任何 `/Users/chx` 或 `/opt/...` 私有脚本。
运行目录可自选，下面统一用仓库内的 `.local-stress/`。这些目录不要提交 Git。

```bash
git clone --branch codex/pr31-six-metric-observation https://github.com/tech-innovation-group/Memory-System-Eval-Harness.git
cd Memory-System-Eval-Harness
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m performance --target echomem --help
mkdir -p .local-stress
cp performance/targets/echomem/docs/six-metrics.profile.example.json .local-stress/profiles.json
```

Python 3.11+；建议 Linux Docker 主机运行，runner 与被测服务分开。
Windows 可用 PowerShell 的 `.venv/Scripts/Activate.ps1` 激活，但 M5 仍需能访问目标 Docker。
现成环境可以直接复用，无须重新安装 EchoMem。测试平台不会自动拉代码、改 EchoMem 配置或部署服务。

## 2. 先准备真实服务

| 必须准备 | 如何确认 | 缺失的影响 |
|---|---|---|
| 独立 EchoMem 测试容器 | Docker `--cpus=4 --memory=8g`；base_url 必须指向这个容器 | 不能出具 4U8G 验收 |
| 被测版本与工作目录 | 记录 EchoMem commit；持久化工作目录挂载到容器，重启后不能丢挂载 | 版本不可比 / M5 恢复不成立 |
| 真实 LLM + Embedding | 被测服务实际生效的配置，双方使用相同模型与凭据环境 | M1–M4 召回质量无效 |
| 独立租户身份 | 默认 32 个独立 auth_key、tenant_id；前四个做公平/隔离 | 同 key 多名称不能证明租户隔离 |
| 只读及故障契约 | 下表 API 必须可用 | 对应指标不能验收，不伪装为通过 |
| Docker 控制权限 | runner 可 inspect、kill、start **该专用容器** | M5 不会执行真实崩溃 |

保持 `base_url` 与 `resource_container` 指向同一个目标是部署者的责任；
普通 HTTP readiness 本身不能证明请求一定被路由到这个 Docker ID。
4U8G 检查的是容器限额，不保证宿主机有独占 8GB 空闲内存或 4 核 CPU。
正式压测应避免同机其他任务争抢资源；不要把共享主机干扰误判为 EchoMem 问题。

| 接口 | 用途 |
|---|---|
| `GET /api/v1/system/ready`、`GET /metrics` | 健康与全局指标 |
| session open / messages / commit / commit status | 真正写入、异步完成与计时 |
| `POST /api/retrieval/search` | 路由、真实 embedding/检索和内容命中检查 |
| session history / archive / commit-cursor | M5 消息集合、顺序、幂等对账 |
| `GET/POST /api/inspect/test-control/fault` | M2 故障状态、注入与撤销 |
| `GET /api/inspect/tenant-observability` | M6 逐租户逐层观测 |

后两类受保护 API 在 EchoMem 侧需开启 `ECHOMEM_TEST_CONTROL_ENABLED` 并配置
`ECHOMEM_TEST_CONTROL_TOKEN`，runner 必须传入相同 Token。404 也可能是 Token 不匹配或功能关闭，
不能直接断言 EchoMem 没有实现。不要在生产服务公开测试控制接口。

## 3. 配置与身份

编辑 `.local-stress/profiles.json`：

| 字段 | 填写方法 |
|---|---|
| `base_url` | runner 可访问的服务地址，例如 `http://127.0.0.1:8010` |
| `resource_container` | **专用测试**容器名，不能指向机器人或生产容器 |
| `preflight_config` | 被测服务实际生效配置的本地副本，如 `./echomem.config.json`，不是平台旧模板 |
| `tenant_config` | `./tenants.json` |
| `capacity_levels` | 默认 `[2,4,8,16,32]`；要扩大容量上界先准备更多独立身份 |
| `dau_model` | 每人每日 Search 次数、峰均比；样例 20/5 仅作演示，需按业务确认 |
| `tenant_observability.expected_lanes` | 按真实启用配置列出所有被测层，关闭项说明原因；不能为了通过删掉启用层 |
| `commit_recovery.allow_container_restart` | 默认 false；确认容器专用于压测后设为 true，授权 M5 kill-9/start |

所有相对文件路径以 profiles.json 所在目录解析。配置中的 `api_key_env` 需要在 runner 环境中设置；
支持原生配置内联 `api_key`，但更建议使用环境变量。配置文件与环境文件权限设置为 0600。
`--env-file .local-stress/test.env` 可加载 `KEY=VALUE`；不要把密钥粘贴到报告或 GitHub。
本手册不提供任何固定模型密钥，也不会替换服务的模型、阈值、路由或记忆配置。

仅在允许公开 bootstrap 注册的隔离测试服务中执行：

```bash
python -m performance.targets.echomem.provision \
  --base-url http://127.0.0.1:8010 --count 32 \
  --out .local-stress/tenants.json
```

文件已存在时命令拒绝覆盖。关闭公开注册的服务由管理员提供身份文件，字段格式参考
[租户样例](../profiles/tenants.example.json)。注册发生在服务端，需有相应权限。

## 4. 先检查，再正式测

### 优先产出数据，不应用性能门槛

容量模块默认 `observe`，不因 P95、吞吐或召回质量低于阈值而停止。
每档输出真实请求数、P95、HTTP 状态、有效召回吞吐、CPU/RSS 和质量分母。
仅把退出、OOM、运行期间重启、停止加压后在指定观察窗口内仍无法排空或无任何成功 Search
列为运行故障边界证据。有限恢复窗口不能证明永久不可恢复；鉴权/请求配置错误不计容量边界。

```bash
python -m performance.targets.echomem.acceptance.capacity_experiment \
  --base-url http://127.0.0.1:8010 --target-container echomem-test-4u8g \
  --output .local-stress/capacity-001 --topology cross-tenant \
  --levels 2,4,8,16 --duration-s 120 --warmup-s 15 \
  --load-profile both --assessment-mode observe

# 不重复注入已有用户，只准备新增用户的记忆。
python -m performance.targets.echomem.acceptance.capacity_experiment \
  --base-url http://127.0.0.1:8010 --target-container echomem-test-4u8g \
  --output .local-stress/capacity-002 --topology cross-tenant \
  --levels 32 --duration-s 120 --warmup-s 15 --load-profile both \
  --reuse-seed .local-stress/capacity-001 --seed-validation-queries 8

python -m performance.targets.echomem.acceptance.capacity_publish \
  --observation .local-stress/capacity-001 --observation .local-stress/capacity-002 \
  --output .local-stress/capacity-report

# 找到首个请求无法全部完成的档位后，用相邻通过/失败档各做三次新身份确认。
# completion 不应用延迟或质量门槛，但 Search HTTP/传输失败、Commit 未受理或未完成均判失败。
python -m performance.targets.echomem.acceptance.capacity_confirmation \
  --base-url http://127.0.0.1:8010 --target-container echomem-test-4u8g \
  --output .local-stress/capacity-confirm-001 --topology cross-tenant \
  --levels 2,4 --repeats 3 --pure-duration-s 60 --mixed-duration-s 60 \
  --warmup-s 10 --assessment-mode completion
```

独立身份由测试环境公开注册接口创建，真实短文经 Commit 生成记忆后进行自然语言事实检索。
每用户 40 道 recall 变体，混合组另有 20 道 no-recall；准备阶段可以抽样验证，
正式负载仍遍历完整问题池。未完成准备、未命中、降级和未发出的请求均保留计数。
`identities.private.json` 含凭据，仅保留在执行机，权限 0600，不上传或下载到公开报告。
报告目录的 `report.html` / `report.json` 为脱敏汇总；DAU 仅作明确画像下的吞吐等价换算。

其余主要指标的短测入口：

```bash
python -m performance.targets.echomem.acceptance.main_metric_samples \
  --base-url http://127.0.0.1:8010 --container echomem-test-4u8g \
  --seed-directory .local-stress/capacity-002 --output .local-stress/main-samples \
  --expected-lanes recall_engine,recall_intent_llm,recall_query_embedding,commit \
  --duration-s 60 --allow-container-restart

# M2 正式矩阵：T1-T4 × reject/delay × 3轮，真实HTTP故障与恢复后对照。
python -m performance.targets.echomem.acceptance.fault_matrix \
  --base-url http://127.0.0.1:8010 --container echomem-test-4u8g \
  --seed-directory .local-stress/capacity-002 --output .local-stress/fault-matrix \
  --repeats 3 --phase-duration-s 60 --recovery-duration-s 30 --delay-ms 1000

# M5 恢复矩阵：三个真实 kill-9 时机和不同消息量。
python -m performance.targets.echomem.acceptance.recovery_matrix \
  --base-url http://127.0.0.1:8010 --container echomem-test-4u8g \
  --seed-directory .local-stress/capacity-002 --output .local-stress/recovery-matrix \
  --kill-delays-s 0.0,0.2,1.0 --message-counts 8,12,20
```

只可对专用容器传 `--allow-container-restart`。控制 Token 由环境提供；expected-lanes
必须按实际启用模块核对，不能删掉缺少数据的启用层以伪造完整率。
短测覆盖一个可指定的故障租户、三名旁观者、四租户各 8 次 Commit 洪泛、一次 202 崩溃恢复及四元组快照。
`--fault-target-index 0..3`、`--fault-type reject|delay`、`--fault-delay-ms` 可形成全租户故障矩阵；
`--commits-per-tenant`、`--commit-timeout-s`、`--recovery-kill-delay-s` 和
`--recovery-messages` 用于扩大 M3-M5 样本。完整结论至少轮换四租户、两类故障并重复，
不能只发布默认 T1/reject 的结果。
Commit Jain 仅用相同 Search 观测窗口内完成吞吐，窗口后排空单列；零完成租户不删除。
原始探针产物仅保留执行机，包含会话与身份标识，不能直接发布。
这不是完整故障矩阵或全天 DAU 验证，也不能仅凭 Search 延迟证明内部严格优先调度。

在同一执行机将原始短测与脱敏容量统计合成为可分享 HTML：

```bash
python -m performance.targets.echomem.acceptance.main_metric_report \
  --source .local-stress/main-samples/report.json \
  --capacity .local-stress/capacity-report/report.json \
  --output .local-stress/six-metric-report
```

仅分享生成目录内的 HTML/JSON，不分享 main-samples 原始目录。
`report.html` 内含六项关键数据，`capacity-report.html` 含每档详细分母与统计。
如在独立目录补跑了恢复探针，可用 `--recovery` 指定其 JSON；不会改写原始短测结果。

### 旧完整套件入口

下面的 `--six-metrics` 保留既有 SLO 验收语义，与上述无性能门槛观测分开。
本轮只要数据时使用上面的入口；不得以旧套件 PASS/FAIL 推导最大用户为零。

```bash
python -m performance --target echomem \
  --profiles .local-stress/profiles.json --profile 4U8G \
  --six-metrics --check-only --env-file .local-stress/test.env \
  --out-dir .local-stress/check-001
```

读取 `check-001/4U8G/readiness.json`。检查包括独立身份数量、真实模型种类及返回结构、
4U8G 容器运行状态、故障接口 Token、观测接口和是否遗留活跃故障。
仅发送只读服务请求和最小真实模型请求（会有少量模型费用）；不灌种、不修改故障状态、不重启容器。
检查通过只说明可以开始，不代表六项通过。接口缺失、模型失败时先按 owner/next_action 修复。

```bash
python -m performance --target echomem \
  --profiles .local-stress/profiles.json --profile 4U8G \
  --six-metrics --env-file .local-stress/test.env \
  --out-dir .local-stress/run-001
```

不用环境文件时省略 `--env-file`，变量从当前 shell 读取。正式运行再次检查环境，
随后真实生成记忆、确认 Search 命中，再执行全部场景。每次使用新目录，禁止混入旧结果。
不加 `--six-metrics` 会进入其他完整矩阵，耗时与本手册不同。

## 5. 实际执行哪些用例

完整判定公式、输入输出、分母详见 [六项方案](six-metrics.md)。

| 指标/用例 | 负载与步骤 | 必须生成的数据 |
|---|---|---|
| 数据准备 | 每租户短事实 → open → add → Commit → completed → Search 标记命中 | 每租户 marker、可见性、降级原因；不使用 LoCoMo 长对话 |
| M1 capacity-2/4/8/16/32/64 | 每档纯召回+混合各60s，N个活跃身份、N RPS；相邻完成/失败档各三次确认 | 档位、实际身份数、全部请求数、质量率、平均/P50/P95/P99、错误、CPU/RSS、完成边界、DAU假设与保守估算 |
| M2 fault matrix | 前四租户各做 reject/delay，三轮；每轮 before/during/after | 24 例、目标故障生效证据、每个旁观租户三阶段延迟与错误、最差 p95 劣化、故障撤销证据 |
| M3 fairness-bounded | 四个同档位租户；120s 同一窗口；每租户 8 个 Commit；Search 并行持续发出 | 每租户 Commit 完成/秒、Search p95、两个 Jain、零完成和错误分母 |
| M4 recall-baseline + search-priority-blackbox | 两阶段同一份记忆、16 RPS、32 Search workers；洪泛阶段第15秒提交32个 Commit | 唯一202受理数、开始/完成时间、积压重叠 Search 分母、逐租户基线与洪泛p95及劣化比 |
| M5 commit-recovery | 12条消息 → 202且仍未完成 → kill-9/start → 原任务自主恢复 → 再做同key重试 | 受理数、未完成数、恢复数、消息集合、cursor、顺序、幂等六项检查；默认一次故障样本 |
| M6 tenant-observability | 负载前与负载后（M5重启之前）快照对比 | 预期租户×启用lane分母、queued/wait/exec/rejected、本次accepted/failed/completed增量 |
| query-mixed | recall + 明确无需记忆的问候/运算/翻译，60s | recall与no_recall分别统计错误、延迟、命中/误召回；不混成一个accuracy |

Search 与 Commit 是压测客户端独立并发任务，客户端不模拟服务端的优先调度。
Search 计时为真实 HTTP 调用耗时，包括服务端路由/检索/模型等待，不包括另起一轮 QA/Judge。
超时、HTTP 错误、降级和空召回不从分母删除。

## 6. 耗时、进度与结果

默认 9 个负载场景的设定窗口合计 **660秒**，不等于总时长。还包括灌种、Commit排空、
24个故障用例和恢复等待；模型慢或限流时会显著增加，不能承诺一个小时必定完成。
`--timeout-s` 是单 case/probe 上限，不是整个测试的总预算。无默认 soak。
`4U8G/progress.json` 可看灌种与负载场景进度；故障阶段可查看逐例 JSON 是否生成。

| 输出 | 用途 |
|---|---|
| `4U8G/six-metrics.html` | 六项结论、表格、Jain可视化、逐租户对比与模块问题，正式阅读入口 |
| `4U8G/six-metrics.json` | 六项机器可读验收数据与完整分母 |
| `4U8G/suite.json` | 本轮场景与探针索引、资源、模型预检与种子证据 |
| `4U8G/<case>/records.csv` | 所有请求耗时/错误/质量证据 |
| `search_results.csv` / `commit_results.csv` / `metrics_samples.csv` | 各case下的检索、写入与服务指标 |
| `fault-isolation-*.json` / `commit-recovery.json` / `tenant-observability.json` | 各探针原始证据 |

退出码 0：六项通过；1：至少一项明确FAIL；2：证据不足或前置条件失败。
`objective-suite.html` 是通用 O1–O7 汇总，**不要用它代替 M1–M6 的 six-metrics.html**。
API 200、Commit 202、脚本退出或生成HTML，都不单独代表通过。
原始证据可能含测试身份与会话标识；只对授权人员共享，不把整个配置目录打包公开。

## 7. 能测什么、还不能证明什么

平台具备六项的基础执行、统计与报告路径；这不等于已完成新版方案的所有矩阵。
尤其 M1 的跨租户/租户内容量边界、三次确认、10倍记忆规模和 auto-commit 对照，
不能仅凭默认9个场景宣称完整覆盖。能否得到PASS取决于被测服务和真实数据。
以下限制不能靠改阈值掩盖：

- M1：最高档成功只能给容量下界；DAU是业务假设下的估算，不是实测日活。短记忆负载不是生产数据规模。
- M2：目前只证明测过的租户在请求级拒绝/延迟下的隔离，不涵盖任意模型、存储、进程故障。
- M3：需要真实同档位租户和足够样本；“大家都失败”不能算公平性通过。
- M4：可验证积压下Search SLO，无法只靠客户端延迟证明内部严格排队顺序。严格调度顺序需要EchoMem额外的排队/调度事件证据。
- M5：可测真实202后的自主恢复与顺序/幂等；一次样本成功不是所有崩溃窗口的100%保证。
- M6：需要EchoMem暴露各启用层的真实观察值；平台不能用虚构四元组补齐缺失指标。

缺接口由 EchoMem 实现或部署启用；配置、鉴权、负载样本、统计和报告问题由测试平台处理。
环境检查失败时不会继续消耗数小时模型资源。服务已降级时保留真实失败，不为了得到数字改成 mock。

## 8. 本地回归（不是性能数据）

```bash
python -m pip install pytest pytest-subtests
python -m pytest performance/tests -q
```

单测用本地受控HTTP返回验证判定逻辑；不冒充真实EchoMem或真实模型性能。
检视本次交付重点：`acceptance/readiness.py`、`acceptance/six_metrics.py`、
`orchestrator/suites.py`、`probes/fault_isolation.py`、`probes/commit_recovery.py`。

## 9. M1 单项证据报告

M1 新版采集模块：`acceptance/capacity_seed.py`、`capacity_load.py`、
`capacity_statistics.py`。目前属于单项迭代，尚未全部接入默认九场景入口，
不要把模块单测通过当作完整容量矩阵已执行。

从单项采集结果生成独立 HTML：

```bash
python -m performance.targets.echomem.acceptance.capacity_report \
  /path/to/m1-run/report.json \
  --preflight /path/to/m1-run/preflight.json \
  --router-diagnostics /path/to/m1-run/router-compat.json \
  --output /path/to/m1-published/report.html
```

输出目录需预先创建；两个补充证据参数均可省略。HTML 附带逐题表、模型预检、
容量阶梯、模块归因和 DAU 前提，结构化数据写入同目录。只给此命令传入脱敏的
测试结果，不传配置文件、环境文件或 `identities.private.json`。

语义门槛统计同时保留“命中预期事实”和“HTTP正常 + 事实命中 + 无降级”。
随机 marker 仅作独立诊断，不可因 marker 没召回就跳过 40 道固定语义问题。
所有失败保留在分母；串行种子验证不能当作并发容量数据。
若真实模型有 reasoning 内容却无回答正文，需要核对意图模型单字母输出契约，
不得把模型预检 HTTP 200 当作路由兼容性通过。
