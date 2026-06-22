# OpenViking 记忆存储机制详解

## 🔍 核心发现

**OpenViking 的记忆不是存储在文件系统的 `.md` 文件中，而是存储在向量数据库中！**

## 📁 数据存储位置

### 1. Workspace 结构
```
openviking_workspace_locomo_20260601_014238_5b2c49/
├── _system/           # 系统数据
│   └── queue/
│       └── queue.db
├── vectordb/          # 向量数据库 ⭐ 记忆的实际存储位置
│   └── context/
│       ├── collection_meta.json
│       ├── index/     # 向量索引
│       │   └── default/
│       │       └── versions/
│       │           └── .../
│       │               ├── vector_index/  # 向量索引数据
│       │               └── scalar_index/  # 标量索引数据
│       └── store/     # 实际数据存储（RocksDB）
│           ├── 000003.log
│           ├── CURRENT
│           ├── MANIFEST-000002
│           └── ...
└── viking/            # 原始对话数据
    └── default/
        └── session/   # 对话 session
            └── locomo-conv-30-s*/
                └── history/
                    └── archive_*/
                        └── messages.jsonl
```

### 2. 记忆的存储形式

**虚拟 URI (Viking URI)**:
```
viking://user/default/memories/events/2023/01/20/lost_job_banker.md
viking://user/default/memories/entities/person/jon.md
viking://user/default/memories/entities/friends/jon.md
```

这些 URI **不是真实的文件路径**，而是：
- 记忆的逻辑标识符
- 存储在向量数据库中的元数据
- 通过 OpenViking API 访问

**实际存储**:
- 向量数据: `/vectordb/context/index/default/versions/.../vector_index/`
- 原始数据: `/vectordb/context/store/` (RocksDB 格式)

## 🔄 数据流程

### 导入阶段
```
1. 对话历史 (messages.jsonl)
   ↓
2. OpenViking 处理
   ↓
3. 生成记忆片段
   ↓
4. 向量化 (text-embedding-v3)
   ↓
5. 存储到 vectordb/
```

### 查询阶段
```
1. 用户问题
   ↓
2. 向量化查询
   ↓
3. 在 vectordb 中检索
   ↓
4. 返回相似记忆 (带 Viking URI)
```

## 🎯 为什么找不到 `.md` 文件？

因为 **OpenViking 不使用文件系统存储记忆**！

传统文件系统:
```
memories/
├── events/
│   └── 2023/
│       └── 01/
│           └── 20/
│               └── lost_job_banker.md  ❌ 不存在
```

OpenViking 实际:
```
vectordb/
└── context/
    ├── index/        # 向量索引
    └── store/        # RocksDB 数据库
        └── [binary data]  ✅ 记忆存储在这里
```

## 📊 记忆数据示例

从评测结果中，我们看到 OpenViking 返回的记忆：

```json
{
  "context_type": "memory",
  "uri": "viking://user/default/memories/events/2023/01/20/lost_job_banker.md",
  "level": 2,
  "score": 0.6486793989383726,
  "abstract": "time: 2023-01-20 (Friday)\n[user]: [Jon] D1:2: Hey Gina! Good to see you too. Lost my job as a banker yesterday, so I'm gonna take a shot at starting my own business.",
  "overview": null
}
```

注意：
- `uri`: 虚拟路径，不是真实文件
- `score`: 相似度分数
- `abstract`: 记忆摘要（从 vectordb 读取）

## 🔧 如何访问记忆？

### 方法 1: 通过 OpenViking API

```bash
curl <OPENVIKING_BASE_URL>/api/v1/search/find \
  -H "Content-Type: application/json" \
  -H "X-OV-Account: default" \
  -H "X-OV-User-ID: default" \
  -H "X-OV-Agent-ID: default" \
  -d '{
    "query": "When did Jon lose his job?",
    "target_uri": "viking://user/memories/",
    "limit": 10,
    "score_threshold": 0.1
  }'
```

**前提**: OpenViking 必须正在运行，并且指向包含数据的 workspace。

### 方法 2: 重启 OpenViking 指向有数据的 workspace

```bash
# 停止当前 OpenViking
ps aux | grep "port 1933" | grep -v grep | awk '{print $2}' | xargs kill

# 启动 OpenViking 指向有数据的 workspace
python3 -m openviking.server.bootstrap \
  --config <repo-root>/runs/openviking_import_20260601_014238_227a10/openviking.runtime.conf \
  --host 127.0.0.1 \
  --port 1933
```

## 📈 数据统计

### 昨天的评测结果 (2026-06-01)

- **Workspace**: `openviking_workspace_locomo_20260601_014238_5b2c49`
- **记忆总数**: ~30 条（每个问题检索到的）
- **记忆来源**:
  - `viking://user/default/memories/events/...`
  - `viking://user/default/memories/entities/person/...`
  - `viking://user/default/memories/entities/friends/...`
  - 等等
- **相似度分数**: 0.57 - 0.65
- **存储位置**: `vectordb/context/`

### 当前状态 (2026-06-02)

- **Workspace**: `openviking_workspace_locomo_20260602_055547_3a5529`
- **记忆总数**: 0 条（新建的 workspace）
- **Session 数据**: ✅ 已导入 19 个 session
- **向量数据**: ❌ 未 commit

## ⚠️ 重要说明

### 1. 记忆不会自动持久化到文件

即使你在 Web UI 中看到记忆，它们也不会自动保存为 `.md` 文件。OpenViking 使用向量数据库存储。

### 2. Workspace 切换会丢失数据

如果 OpenViking 重启并使用新的 workspace，旧 workspace 的记忆将不可访问（除非重新指向旧 workspace）。

### 3. 数据备份

要备份记忆数据，需要备份整个 `vectordb/` 目录：
```bash
cp -r openviking_workspace_xxx/vectordb/ backup/
```

## 🎯 结论

**OpenViking 使用向量数据库（类似 Milvus、Qdrant）存储记忆，而不是文件系统。**

优点：
- ✅ 高效的向量检索
- ✅ 支持大规模记忆
- ✅ 自动相似度计算

缺点：
- ❌ 无法直接查看 `.md` 文件
- ❌ 需要通过 API 访问
- ❌ 数据格式不透明

## 📝 对比

| 特性 | 文件系统存储 | OpenViking 向量数据库 |
|------|------------|---------------------|
| 存储格式 | `.md` 文本文件 | 二进制向量数据 |
| 可读性 | ✅ 可以直接打开 | ❌ 需要通过 API |
| 检索速度 | ❌ 慢（grep） | ✅ 快（向量检索） |
| 相似度计算 | ❌ 不支持 | ✅ 自动计算 |
| 备份 | ✅ 简单（复制文件） | ⚠️ 需要备份数据库 |
| 可移植性 | ✅ 高 | ⚠️ 依赖 OpenViking |

## 🚀 如何查看具体记忆内容

由于当前 OpenViking 使用的是新 workspace（没有数据），要查看 `viking://user/default/memories/events/2023/01/20/` 的内容，你需要：

**选项 A**: 重启 OpenViking 指向有数据的 workspace
```bash
# 1. 停止当前 OpenViking (端口 1933)
kill [PID]

# 2. 启动指向昨天 workspace 的 OpenViking
python3 -m openviking.server.bootstrap \
  --config /path/to/yesterday/openviking.runtime.conf \
  --host 127.0.0.1 \
  --port 1933

# 3. 查询记忆
curl <OPENVIKING_BASE_URL>/api/v1/search/find ...
```

**选项 B**: 重新导入并 commit 记忆到当前 workspace
```bash
# 在 Web UI 中重新导入 conv-30
# 确保勾选 "commit" 选项
```

**选项 C**: 直接查看评测结果中的记忆内容
```bash
# 评测结果的 relevant_memory 字段包含了完整的记忆内容
cat ~/locomo-eval-web/runs/ov_qa_30_vikingbot_20260601_101027/openviking_memory_qa_results.csv
```
