# 通用系统压测

`stress/generic/runner.py` 是配置驱动的真实 HTTP/JSON 压测器。它不使用
mock，也不依赖 EchoMem 的接口语义；只需要提供目标系统的地址、请求模板、
健康检查、断言和负载场景。

## 快速开始

```bash
python3 stress/generic/runner.py \
  --config stress/generic/example.json \
  --out-dir results/stress/generic_$(date +%Y%m%d_%H%M%S)
```

认证、租户 ID、测试数据等放在 `variables` 中，模板里使用
`{变量名}`。变量可以引用环境变量，例如 `"API_TOKEN": "${API_TOKEN}"`，
也可以临时覆盖：

```bash
export API_TOKEN='real-token'
python3 stress/generic/runner.py \
  --config stress/generic/example.json \
  --var "API_TOKEN=$API_TOKEN"
```

真实密钥不要提交到 Git。

## 配置能力

- `target.base_url / headers / timeout_s`：目标地址和默认请求配置。
- `requests`：定义 GET、POST 等请求、JSON body、期望状态码和 JSON 断言。
- `healthcheck`：正式压测前的健康检查，失败时不会发送负载。
- `scenarios`：固定请求数或 `duration_s + rps`，支持并发、混合请求和 P95 门槛。
- `--pid`：可选的 Linux `/proc` RSS 采样；不提供时仍保留请求级指标。
- `--var NAME=VALUE`：覆盖配置变量，可重复使用。

每轮输出 `summary.json`、`requests.csv`、`resources.csv` 和 `report.html`。
报告会区分配置/可用性、错误率、延迟、目标请求未达成和资源增长等问题。

## 服务器任务入口

服务器 Web/飞书编排层支持 `generic_stress`。提交时把完整配置放在
`config` 字段，任务会进入与 EchoMem 相同的单并发队列：

```bash
curl -X POST http://127.0.0.1:8081/api/bridge/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "test_type": "generic_stress",
    "config": {
      "target": {
        "name": "example-api",
        "base_url": "https://example.com"
      },
      "requests": {
        "health": {
          "method": "GET",
          "path": "/health",
          "expected_status": 200
        }
      },
      "healthcheck": {"request": "health"},
      "scenarios": [
        {"name": "smoke", "requests": ["health"], "total_requests": 10, "concurrency": 2}
      ]
    }
  }'
```

配置中的密钥使用 `${ENV_NAME}`，由服务器进程环境提供；不要把真实密钥放进
JSON、任务描述或报告。任务详情页会展示实时阶段、最近日志以及
`generic/report.html`、`summary.json`、`requests.csv` 和 `resources.csv`。

## 适用范围和边界

该入口覆盖绝大多数 HTTP/JSON 服务，包括 REST、网关、Agent API、检索服务
和模型代理。WebSocket、gRPC、纯 TCP、复杂登录流程需要新增 transport
适配器；不应把一个 HTTP 200 响应直接解释成业务正确，业务断言应写进
`requests.*.assertions`。
