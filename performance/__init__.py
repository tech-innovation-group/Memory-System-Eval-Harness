"""Performance stress-testing of memory backends (EchoMem).

Independent of benchmarks/dynamic: measures throughput, latency, injection
(write) cost, mixed read/write degradation, and server resources (CPU/RSS)
under multi-tenant concurrency. The server side is observed read-only through
its Prometheus /metrics endpoint; no server-side change is required.
"""