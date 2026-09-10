# 并发拓扑与超长请求实验

使用被测 EchoMem 自带完整 config.example.json，配置真实模型、独立租户认证和
DEBUG JSON 日志。Embedding 固定 qwen3.7-text-embedding-flash。先完成本机指南的
模型、身份、Commit 终态和真实事实召回预检。

## 参数实验

从已经可启动的完整配置生成四份文件，不改变引擎、存储、熔断与 CPU 相关 Commit worker：

```bash
.venv/bin/python -m performance.targets.echomem.prepare_concurrency_configs \
  --config /absolute/path/to/EchoMem/config.json \
  --out-dir .local-stress/concurrency-configs
```

| EchoMem 配置 | 16 | 32 | 64 | 128 |
| --- | ---: | ---: | ---: | ---: |
| scheduling.http.max_workers | 64 | 128 | 256 | 512 |
| scheduling.retrieval.admission_permits | 16 | 32 | 64 | 128 |
| recall.max_inflight | 16 | 32 | 64 | 128 |
| scheduling.fanout.executor_workers | 32 | 64 | 128 | 256 |
| scheduling.fanout.engine_max_inflight | 16 | 32 | 64 | 128 |
| LLM / Embedding 总通道 | 64 | 128 | 256 | 512 |
| Recall LLM / Embedding 子通道 | 16 | 32 | 64 | 128 |
| Commit queue / tenant quota | 64 / 16 | 128 / 32 | 256 / 64 | 512 / 128 |

Provider budget 根据全部消费者份额求和；这不代表外部模型账户拥有对应配额。
每份配置都必须由目标 EchoMem 启动校验。若内存预算、字段或资源约束不通过，记录
配置失败并停止该档，不删除保护校验。核对环境变量没有覆盖 JSON，保存实际生效值。
每份配置使用新结果目录，先排空再切换；禁止用不同配置的结果拼成同一次容量测量。

## 四种拓扑

N 为 16、32、64、128；每个案例默认计划 128 次操作，按需增大 requests_per_level。

1. N 个独立用户，每个一个 Session，每 Session 最多一个在途操作。
2. N/2 个独立用户，每用户两个 Session，每 Session 最多一个在途操作。
3. N/4 个独立用户，每用户一个 Session，每 Session 最多四个在途操作。
4. 四个独立租户，两租户发短 Search、两租户发长 Message + Commit，每 Session 最多 N/4 个在途操作。

当前用户身份来自独立租户凭据，因此完整第一种拓扑的 128 档需要 128 份独立凭据。
不足的档位保留计划数、sent=0 和原因，不复用 key。目标并发与实测峰值分别列出。
第四类按操作类型分别统计延迟、2xx 受理吞吐和错误；受理 Jain 不能称为 Commit
完成公平性，也不能与同档等权 M2 Jain 混用。正式完成公平性使用 M2 的终态吞吐。
每场结束轮询已受理 Commit，300 秒仍未排空则停止后续场景。失败终态保留，202 不算完成。

将下列段落合并进指南的 profile（顶层 profiles 数组中的单个实例对象）：

```json
{
  "required_embedding_model": "qwen3.7-text-embedding-flash",
  "concurrency_topology": {
    "enabled": true,
    "levels": [16, 32, 64, 128],
    "requests_per_level": 128,
    "sessions_per_user": 2,
    "within_session_concurrency": 4,
    "large_commit_chars": 65536,
    "drain_timeout_s": 300,
    "timeout_s": 60
  },
  "payload_boundary": {
    "enabled": true,
    "sizes_bytes": [0, 1, 1024, 65536, 262144, 524288, 1048576],
    "commit_content_chars": 1048576,
    "commit_chunk_chars": 262144,
    "commit_timeout_s": 600,
    "mcp_base_url": "http://127.0.0.1:8001",
    "mcp_add_memory_tool": "add_memory",
    "mcp_add_memory_chars": 1048576
  }
}
```

此扩展须使用包含探针实现的版本；PR34 仅文档分支不包含实现。用官方
observation_run 入口显式 --metrics M1,M2,M3 启动，核对最终探针 JSON 是否存在。

## 边界与解释

七种长度各测 Message JSON 文本、Search JSON 文本、Commit text/plain，及三个 API
的 application/octet-stream，共 42 次请求。报告分别记录内容字节与实际请求体字节，
JSON 包装也占长度。Commit 本身是控制接口，合法超长路径是先 Add Message 再提交。
另写入 1 MiB 内容（每块 256 KiB）并等待真实 Commit 终态；记录实际接收字符数，
部分写入后 completed 不等于完整成功。MCP add_memory 独立调用，不能用 Message 替代。

这里重复字符用于长度边界，不作为有意义的记忆召回或模型准确率语料。
自动 Commit 可能被大消息触发，必须采集对应任务并排空后才能得到独立案例的结果；
当前边界探针尚未完成自动 Commit 全量对账，应将受其影响的延迟解释为混合积压观测。
模型上下文超限、413、415、429、503、超时分别保留；HTTP 成功不代表内容全部入库。
