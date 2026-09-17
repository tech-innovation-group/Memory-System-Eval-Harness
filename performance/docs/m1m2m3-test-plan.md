# M1/M2/M3 完整测试方案实现文档

对齐 `echomem-m1m2m3-complete-plan-20260916.html` 设计稿。

## 实现清单

### 新增场景文件
- `scenes/scene_commit_barrier.py` — M1 Commit 容量（同步屏障 commit）+ Search 容量
- `scenes/scene_commit_flood.py` — M3 Commit 洪泛 + M2 混合窗口

### 修改文件
- `protocol.py` — 新增 `_extract_layer_timing()` 分层耗时采集（方案A: Response Header）
- `extended_profile.py` — levels 从 [16,64] 扩展到 [1,8,16,64]

### 核心数据流

```
Phase 0: Seed（所有租户使用同一 LoCoMo session 的同一句话；每租户使用不同问法）
  → 固定 LoCoMo 版本 + 随机种子
  → 64 个不同 session 分配，保存 conv_id/消息数/字符数/哈希
  → threshold = max(20000, 最长 session 字符数 + 余量)
  → 逐租户 search 预检，空召回/路由跳过/降级分别记录

Phase 1: Commit Barrier（M1 Commit 容量 + M3 前半段）
  → 1/8/16/64 租户同步起跑 commit
  → 采集：提交耗时、受理率、完成率、e2e P50/P95/P99
  → 每档给出样本数，C1 只有 1 个样本不计算分位数

Phase 2: Search Capacity（M1 Search 容量）
  → 复用 Phase 1 记忆，每租户 1 QPS × 60s
  → 采集：P50/P95/P99、RPS、错误率、429 首次出现时间
  → 分层耗时（如果 EchoMem 返回 response header）

Phase 3: Commit Flood（M3 S1/S2）
  → S0 = Phase 2 的纯 search baseline
  → S1 = 每租户 search 1 QPS + 后台维持一个 commit
  → S2 = 单租户灌 commit，其余租户旁观 search

Phase 4: Fairness（M2，复用 Phase 2/3 数据）
  → Jain Fairness（吞吐 + 延迟）
  → 每租户单独展示，不折叠

## 配置：auto_commit_threshold

```yaml
session:
  auto_commit_threshold: 20000  # 确保一个 session = 一次 commit
```

运行前需计算最长 session 累计字符数，threshold 设为 max(20000, 最长字符数 + 安全余量)。

## 分层耗时（方案A）

EchoMem 在 search response 返回以下 headers：

| Header | 含义 |
|--------|------|
| X-EchoMem-Intent-Ms | 意图识别耗时 |
| X-EchoMem-Embedding-Ms | 向量化耗时 |
| X-EchoMem-Retrieval-Ms | 检索耗时 |
| X-EchoMem-Rerank-Ms | 重排耗时 |
| X-EchoMem-Assembly-Ms | 响应组装耗时 |

客户端在 `_extract_layer_timing()` 中解析这些 header 并记录到 `RequestRecord.extra`。

## 报告格式

- `report.html`: M1 档位对比图 + M2 per-tenant 分布 + M3 S0/S1/S2 对比
- `summary.json`: 每 case 的完整统计
- `records.csv`: 原始请求记录（含分层耗时）
- `commit_evidence.csv`: Commit 每阶段耗时
