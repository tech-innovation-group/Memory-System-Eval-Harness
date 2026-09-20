# M1/M2/M3 压测开发者资料

每次机器人压测结束后，`scripts/feishu_m123_runner.py` 会在观测入口完成后执行
`scripts/build_stress_artifacts.py`。它不改写 `summary.json`、CSV 或原始结构化日志，
只生成并挂到同一份 `report.html`：

- `anomaly-dossier.json`：可机器读取的异常、证据、严重级别和下一步。
- `developer-bundle.tar.gz`：脱敏资料包，包含报告、配置、manifest、场景 CSV、容器日志和结构化 stage 日志；不包含租户环境文件或密钥。
- 报告中的“异常诊断与开发者分析”：先展示确定性统计，再展示可选的大模型分析。

大模型只接收汇总后的计数、状态码、空召回、Commit 终态和有限运行元数据，不发送 API key、租户凭据或整份原始日志。运行环境提供
`ANOMALY_LLM_BASE_URL`、`ANOMALY_LLM_MODEL`、`ANOMALY_LLM_API_KEY` 时生成分析；未配置时仍生成确定性 dossier，并明确标记模型分析不可用。

异常判定目前包括：

1. 多租户空召回率高于单租户基线；
2. Search HTTP/传输错误；
3. Commit 拒绝；
4. Commit 接受后未在观察窗口内终态；
5. 缺少事实命中质量证据。

资料包用于开发者分析，不能用其中的 LLM 文字替代原始分母和请求级证据。

## 注入数据量

压测 profile 的每租户注入量支持两档：`100`（默认）和 `1000`。机器人宿主机配置
`/opt/memory-eval-web/server.env` 中的 `M123_STRESS_REPEAT_COUNT` 控制默认档位：

```bash
M123_STRESS_REPEAT_COUNT=100   # 快速回归
M123_STRESS_REPEAT_COUNT=1000  # 大数据量压测
```

任务生成的 `stress-profile.json` 会记录实际值，报告和开发者资料包也会保留该 profile。
非法值会在任务启动前拒绝，避免误把压测量扩大到未验证的档位。
