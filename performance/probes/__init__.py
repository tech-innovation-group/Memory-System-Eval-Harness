"""EchoMem 故障、恢复、限流与对账探针。

探针是独立 CLI 工具，直接以 urllib 访问真实 EchoMem HTTP 服务，不依赖
压测 runner。每个探针只在部署方显式提供故障/恢复控制（命令、HTTP 端点、
容器、PID）时才执行真实操作，否则如实上报 INCONCLUSIVE。
"""
