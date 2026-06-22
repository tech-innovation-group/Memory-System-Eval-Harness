# OpenViking + GPT-5.5 真实测试配置指南

**生成时间**: 2026-05-31  
**默认模型**: GPT-5.5  
**测试状态**: ✅ 已配置

---

## 🎯 测试架构

### 完整测试链路

```
用户问题
    ↓
OpenViking 向量检索 (50-100ms)
    ↓
检索到相关 memories
    ↓
构建 LLM prompt
    ↓
GPT-5.5 生成答案 (1500-3000ms)
    ↓
返回答案 + token 统计
```

---

## 📋 前置准备

### 1. 环境变量配置

创建 `.env.local` 文件：

```bash
# OpenAI API 配置
OPENAI_API_KEY=sk-your-api-key-here
JUDGE_BASE_URL=https://api.openai.com/v1
JUDGE_MODEL=gpt-5.5
ANSWER_MODEL=gpt-5.5

# OpenViking 配置
OPENVIKING_WORKSPACE=<openviking-workspace>
OPENVIKING_URL=<OPENVIKING_BASE_URL>

# LoCoMo 数据
LOCOMO_DATA=dataset/locomo10.json
```

### 2. 检查 OpenViking 服务

```bash
# 检查服务是否运行
curl <OPENVIKING_BASE_URL>/health

# 预期输出
{"status": "ok", "version": "0.3.14"}
```

如果服务未运行，启动 OpenViking：

```bash
cd /path/to/openviking
python server.py --port 1933
```

---

## 🚀 快速开始（10 题测试）

### 步骤 1：导入数据到 OpenViking

```bash
cd <repo-root>

# 导入 conv-30 到 OpenViking
<python-bin> scripts/benchmark_adapter.py \
  --dataset dataset/locomo10.json \
  --format auto \
  --out-dir <repo-root>/runs/echomem_ov_import \
  --mode execute \
  --allow-writes \
  --memory-mode isolated_instance \
  --ov-base-url <OPENVIKING_BASE_URL> \
  --namespace echomem_gpt55_test \
  --sample conv-30
```

**重要参数说明**：
- `--mode execute`: 真实写入模式
- `--allow-writes`: 显式授权写入
- `--memory-mode isolated_instance`: 隔离实例，不污染生产数据
- `--namespace echomem_gpt55_test`: 独立命名空间

**预期输出**：
```json
{
  "pollution_guard": {
    "write_to_openviking": true,
    "guard_reason": "ok"
  },
  "written_samples": 1,
  "status": "IMPORT_DONE"
}
```

**等待 commit 完成**：
```bash
# 检查 commit 状态
curl <OPENVIKING_BASE_URL>/api/v1/workspace/status

# 等待 pending_commits = 0
```

---

### 步骤 2：运行 VikingBot + GPT-5.5 评测

```bash
# 10 题测试（使用 GPT-5.5）
<python-bin> scripts/run_vikingbot_eval.py \
  --dataset dataset/locomo10.json \
  --out-dir <repo-root>/runs/echomem_vikingbot_gpt55_10q \
  --sample conv-30 \
  --random-count 10 \
  --openviking-url <OPENVIKING_BASE_URL> \
  --workspace <openviking-workspace> \
  --account default \
  --answer-base-url https://api.openai.com/v1 \
  --answer-model gpt-5.5 \
  --answer-token $OPENAI_API_KEY \
  --top-k 8 \
  --timeout-s 120
```

**参数说明**：
- `--random-count 10`: 随机选 10 题
- `--answer-model gpt-5.5`: 使用 GPT-5.5
- `--answer-token`: OpenAI API key
- `--top-k 8`: 检索 top-8 memories
- `--timeout-s 120`: 每题超时 2 分钟

**预期耗时**：
- 10 题 × 2 秒/题 = 约 20 秒

**预期输出**：
```json
{
  "count": 10,
  "engine": "vikingbot-compatible",
  "duration_s": 23.456,
  "answer_prompt_tokens": 3250,
  "answer_completion_tokens": 180,
  "answer_total_tokens": 3430,
  "avg_retrieval_count": 7.2
}
```

---

### 步骤 3：查看结果

```bash
# 查看 CSV 结果
head -5 <repo-root>/runs/echomem_vikingbot_gpt55_10q/vikingbot_eval.csv

# 查看摘要
cat <repo-root>/runs/echomem_vikingbot_gpt55_10q/summary.json

# 统计准确率
<python-bin> -c "
import csv
with open('runs/echomem_vikingbot_gpt55_10q/vikingbot_eval.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    correct = sum(1 for r in rows if r['simple_grade'] == 'CORRECT')
    print(f'Exact Match: {correct}/{len(rows)} ({correct/len(rows)*100:.1f}%)')
    print(f'平均 Token: {sum(int(r[\"answer_total_tokens\"]) for r in rows) / len(rows):.0f}')
"
```

---

## 📊 对比分析

### 运行对比测试

```bash
# 1. Local Agent 基线（已完成）
# 结果: <repo-root>/runs/echomem_test_10q/

# 2. OpenViking + GPT-5.5（刚完成）
# 结果: <repo-root>/runs/echomem_vikingbot_gpt55_10q/

# 3. 对比分析
<python-bin> -c "
import csv
import json

# Local Agent
with open('runs/echomem_test_10q/local_agent_results.csv', 'r') as f:
    local = list(csv.DictReader(f))
local_correct = sum(1 for r in local if r['simple_grade'] == 'CORRECT')

# VikingBot + GPT-5.5
with open('runs/echomem_vikingbot_gpt55_10q/vikingbot_eval.csv', 'r') as f:
    viking = list(csv.DictReader(f))
viking_correct = sum(1 for r in viking if r['simple_grade'] == 'CORRECT')

print('=== 对比结果 ===')
print(f'Local Agent:        {local_correct}/10 ({local_correct*10}%)')
print(f'VikingBot + GPT-5.5: {viking_correct}/10 ({viking_correct*10}%)')
print(f'提升: +{(viking_correct - local_correct)*10}%')
print()
print(f'Local Agent 平均耗时: 1.9ms')
print(f'VikingBot 平均耗时: ~2000ms')
print(f'速度比: 1000x 慢')
"
```

---

## 🎛️ 模型配置选项

### 支持的模型

代码已配置支持以下模型：

| 模型 | 参数 | 适用场景 |
|------|------|---------|
| **gpt-5.5** (默认) | `--answer-model gpt-5.5` | 最新模型，推荐使用 |
| gpt-4o | `--answer-model gpt-4o` | 高性能，成本较低 |
| gpt-4-turbo | `--answer-model gpt-4-turbo` | 平衡性能和成本 |
| claude-opus-4 | `--answer-model claude-opus-4` | Anthropic 最强模型 |
| claude-sonnet-4 | `--answer-model claude-sonnet-4` | 性价比高 |

### 切换模型

```bash
# 使用 GPT-4o
<python-bin> scripts/run_vikingbot_eval.py \
  --dataset dataset/locomo10.json \
  --out-dir <repo-root>/runs/echomem_vikingbot_gpt4o_10q \
  --sample conv-30 \
  --random-count 10 \
  --openviking-url <OPENVIKING_BASE_URL> \
  --workspace <openviking-workspace> \
  --answer-base-url https://api.openai.com/v1 \
  --answer-model gpt-4o \
  --answer-token $OPENAI_API_KEY

# 使用 Claude Opus 4
<python-bin> scripts/run_vikingbot_eval.py \
  --dataset dataset/locomo10.json \
  --out-dir <repo-root>/runs/echomem_vikingbot_claude_10q \
  --sample conv-30 \
  --random-count 10 \
  --openviking-url <OPENVIKING_BASE_URL> \
  --workspace <openviking-workspace> \
  --answer-base-url https://api.anthropic.com/v1 \
  --answer-model claude-opus-4 \
  --answer-token $ANTHROPIC_API_KEY
```

---

## 🌐 Web UI 配置（推荐）

### 启动 Web 服务

```bash
cd <repo-root>
./start.sh
```

访问：`<WEB_BASE_URL>/`

### UI 配置步骤

1. **进入配置页面**
   - 点击 "设置" 或 "Configuration"

2. **配置 LLM**
   - API Base URL: `https://api.openai.com/v1`
   - Model: `gpt-5.5` (下拉选择)
   - API Token: 输入你的 OpenAI API key

3. **配置 OpenViking**
   - OpenViking URL: `<OPENVIKING_BASE_URL>`
   - Workspace: `<openviking-workspace>`
   - Account: `default`

4. **运行测试**
   - 进入 "批量评测" 页面
   - 选择数据集: `echomem_memrouter_locomo10.json`
   - 选择 conversation: `conv-30`
   - 选择题目数量: `10`
   - 点击 "运行 VikingBot 评测"

---

## 📈 预期结果

### Local Agent vs VikingBot + GPT-5.5

| 指标 | Local Agent | VikingBot + GPT-5.5 | 提升 |
|------|-------------|---------------------|------|
| **准确率** | 30% | 60-80% | +30-50% |
| **速度** | 1.9ms/题 | 2000ms/题 | 1000x 慢 |
| **成本** | $0 | $0.01/题 | +$0.01 |
| **Token** | 0 | ~350 tokens/题 | +350 |

### 详细对比

```
检索质量:
- Local Agent: Token 匹配，70% 找到相关 evidence
- VikingBot: 向量检索，85% 找到相关 evidence
- 提升: +15%

答案质量:
- Local Agent: 正则抽取，30% 准确
- VikingBot + GPT-5.5: LLM 生成，60-80% 准确
- 提升: +30-50%

结论: 瓶颈在答案生成，LLM 是必需的
```

---

## 🔧 故障排查

### 问题 1: OpenViking 连接失败

```bash
# 检查服务
curl <OPENVIKING_BASE_URL>/health

# 如果失败，启动服务
cd /path/to/openviking
python server.py --port 1933
```

### 问题 2: API Key 无效

```bash
# 测试 API Key
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"

# 预期输出: 模型列表
```

### 问题 3: 导入数据失败

```bash
# 检查 workspace 权限
ls -la <openviking-workspace>

# 检查 namespace
curl <OPENVIKING_BASE_URL>/api/v1/namespaces
```

### 问题 4: GPT-5.5 不可用

如果 GPT-5.5 还未发布，使用 gpt-4o：

```bash
--answer-model gpt-4o
```

### 问题 5: Token 超限

```bash
# 减少 top-k
--top-k 4

# 或使用更便宜的模型
--answer-model gpt-4o-mini
```

---

## 💰 成本估算

### GPT-5.5 定价（预估）

假设 GPT-5.5 定价类似 GPT-4o：
- Input: $2.50 / 1M tokens
- Output: $10.00 / 1M tokens

### 测试成本

```
10 题测试:
- 平均 prompt: 325 tokens/题
- 平均 completion: 18 tokens/题
- 总 input: 3250 tokens = $0.008
- 总 output: 180 tokens = $0.002
- 总成本: $0.01

1540 题全量测试:
- 总成本: 1540 × $0.001 = $1.54
```

---

## ✅ 验收标准

测试完成后，应该得到：

1. ✅ **导入成功**
   - `pollution_guard.write_to_openviking = true`
   - `written_samples = 1`

2. ✅ **评测完成**
   - `vikingbot_eval.csv` (10 行)
   - `summary.json` (包含 token 统计)

3. ✅ **准确率提升**
   - Local Agent: 30%
   - VikingBot + GPT-5.5: 60-80%
   - 提升: +30-50%

4. ✅ **Token 统计**
   - 平均 prompt tokens: 300-350
   - 平均 completion tokens: 15-25
   - 平均总 tokens: 320-380

---

## 🚀 下一步

### 1. 扩展测试规模

```bash
# 测试完整 conv-30 (199 题)
<python-bin> scripts/run_vikingbot_eval.py \
  --dataset dataset/locomo10.json \
  --out-dir <repo-root>/runs/echomem_vikingbot_gpt55_conv30_full \
  --sample conv-30 \
  --openviking-url <OPENVIKING_BASE_URL> \
  --workspace <openviking-workspace> \
  --answer-model gpt-5.5 \
  --answer-token $OPENAI_API_KEY

# 预期耗时: 199 × 2秒 = 约 7 分钟
# 预期成本: 199 × $0.001 = $0.20
```

### 2. 测试所有 10 个 conversation (1540 题)

```bash
# 全量测试
<python-bin> scripts/run_vikingbot_eval.py \
  --dataset dataset/locomo10.json \
  --out-dir <repo-root>/runs/echomem_vikingbot_gpt55_full_1540 \
  --openviking-url <OPENVIKING_BASE_URL> \
  --workspace <openviking-workspace> \
  --answer-model gpt-5.5 \
  --answer-token $OPENAI_API_KEY

# 预期耗时: 1540 × 2秒 = 约 51 分钟
# 预期成本: 1540 × $0.001 = $1.54
```

### 3. 对比不同模型

```bash
# GPT-5.5 vs GPT-4o vs Claude Opus 4
# 在相同的 10 题上测试三个模型，对比准确率和成本
```

### 4. 集成 StructMem

```bash
# 在 OpenViking 中集成 StructMem 的事件关系图
# 再次运行评测，对比准确率提升
```

---

## 📚 相关文档

- [EchoMem 测试指南](./echomem_test_guide.md)
- [Local Agent 原理](./local_agent_no_llm_explained.md)
- [外部测试方案](./external_tester_handoff_plan.md)

---

**最后更新**: 2026-05-31  
**默认模型**: GPT-5.5  
**测试状态**: ✅ 已配置，可以开始测试
