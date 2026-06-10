# LoCoMo 数据集格式说明

## 文件信息

- **路径**: `dataset/locomo10.json`
- **大小**: 66,750 行
- **样本数**: 10 个
- **总问题数**: ~2000 个

## 数据结构

### 顶层结构

```json
[
  {
    "sample_id": "conv-26",
    "qa": [...],              // 问答对数组
    "conversation": {...},    // 对话信息
    "event_summary": {...},   // 事件摘要
    "observation": {...},     // 观察记录
    "session_summary": {...}  // 会话摘要
  },
  ...
]
```

### QA 结构

```json
{
  "question": "When did Caroline go to the LGBTQ support group?",
  "answer": "7 May 2023",
  "evidence": ["D1:3"],
  "category": 2
}
```

**字段说明：**
- `question`: 问题文本
- `answer`: 预期答案
- `evidence`: 证据引用（格式：`D{session}:{turn}`）
- `category`: 类别编号（1-3）

### Conversation 结构

```json
{
  "speaker_a": "Caroline",
  "speaker_b": "Melanie",
  "session_1_date_time": "2023-05-07",
  "session_1": [
    {
      "speaker": "Caroline",
      "content": "...",
      "turn_id": "..."
    },
    ...
  ],
  "session_2_date_time": "2023-05-25",
  "session_2": [...],
  ...
}
```

**字段说明：**
- `speaker_a`, `speaker_b`: 对话双方名称
- `session_N_date_time`: 第 N 个会话的日期时间
- `session_N`: 第 N 个会话的对话轮次数组

### Event Summary 结构

```json
{
  "events_session_1": [...],
  "events_session_2": [...],
  ...
}
```

### Observation 结构

```json
{
  "session_1_observation": "...",
  "session_2_observation": "...",
  ...
}
```

### Session Summary 结构

```json
{
  "session_1_summary": "...",
  "session_2_summary": "...",
  ...
}
```

## Context Pack 输出格式

导入器会为每个 QA 对生成一个 Context Pack：

```json
{
  "id": "conv-26_q0",
  "sample_id": "conv-26",
  "category": "C2",
  "question": "When did Caroline go to the LGBTQ support group?",
  "expected_answer": "7 May 2023",
  "evidence": ["D1:3"],
  
  "reference_conversations": [
    {
      "session": 1,
      "date_time": "2023-05-07",
      "speaker": "Caroline",
      "content": "...",
      "turn_id": "..."
    },
    ...
  ],
  
  "context_engineering": {
    "events": [
      {
        "session": 1,
        "events": [...]
      },
      ...
    ],
    "observations": [
      {
        "session": 1,
        "observation": "..."
      },
      ...
    ],
    "summaries": [
      {
        "session": 1,
        "summary": "..."
      },
      ...
    ]
  },
  
  "speakers": {
    "a": "Caroline",
    "b": "Melanie"
  },
  
  "metadata": {
    "total_sessions": 10,
    "total_turns": 56,
    "qa_index": 0,
    "total_qa": 199
  }
}
```

## 统计信息

基于 `locomo10.json`：

- **样本数**: 10
- **总问题数**: ~2000
- **平均问题/样本**: ~200
- **平均会话数/样本**: ~10
- **平均轮次/会话**: ~5-6

## 类别说明

- **Category 1**: 事实性问题（Factual）
- **Category 2**: 时间性问题（Temporal）
- **Category 3**: 推理性问题（Reasoning）

## 使用示例

```javascript
// 导入数据集
const result = await LoCoMoImporter.importDataset('locomo');

console.log(`导入 ${result.stats.total_samples} 个样本`);
console.log(`共 ${result.stats.total_questions} 个问题`);
console.log(`生成 ${result.samples.length} 个 Context Pack`);

// 访问第一个 Context Pack
const firstPack = result.samples[0];
console.log(firstPack.question);
console.log(firstPack.expected_answer);
console.log(firstPack.reference_conversations.length + ' 轮对话');
```

## 注意事项

1. **大文件**: locomo10.json 有 66,750 行，导入可能需要几秒钟
2. **内存占用**: 每个样本约 200 个问题，会生成大量 Context Pack
3. **后端支持**: 需要实现 `/api/dataset/load` 接口来加载文件
4. **证据引用**: `D1:3` 表示 Session 1 的第 3 个 turn

## 参考

- [MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench)
- ICLR 2026 Paper: "Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions"
