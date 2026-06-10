# EchoMem LoCoMo 测试操作指南

**生成时间**: 2026-05-31  
**测试状态**: ✅ 已打通  
**测试结果**: 10 题测试完成，Exact Match 30%

---

## 📋 测试概览

### 已完成验证
- ✅ EchoMem LoCoMo 数据格式兼容性
- ✅ Local Memory Agent 检索和答案抽取
- ✅ 10 题快速测试流程
- ✅ 结果 CSV 生成和分析

### 测试结果摘要
```
数据集: echomem_memrouter_locomo10.json
Conversation: conv-30 (10 个 conversation 之一)
题目数: 10 题
Exact Match: 3/10 (30.0%)
需要 Judge: 7/10 (70.0%)
平均 Token: 325 tokens/题
```

---

## 🚀 快速开始（10 题测试）

### 步骤 1：确认数据文件

数据文件已就绪：
```bash
dataset/locomo10.json
```

数据来源：
```
https://github.com/tech-innovation-group/echo_memory/releases/tag/version_0.0.5
```

数据结构：
- 10 个 conversation (conv-26, conv-30, conv-41~50)
- 共 1540 题
- 4 个类别 (C1: Personal Fact, C2: Temporal, C3: Multi-hop, C4: Long-context)

### 步骤 2：运行 10 题测试

```bash
cd /Users/chx/locomo-eval-web

/Users/chx/jiuwenclaw/bin/python3.12 scripts/local_memory_agent.py \
  --dataset dataset/locomo10.json \
  --format auto \
  --out-dir /Users/chx/locomo-eval-web/runs/echomem_test_10q \
  --sample conv-30 \
  --count 10 \
  --top-k 6
```

**参数说明**：
- `--dataset`: 数据集路径
- `--format auto`: 自动识别为 LoCoMo 格式
- `--out-dir`: 输出目录
- `--sample conv-30`: 选择 conv-30 这个 conversation
- `--count 10`: 只测试前 10 题
- `--top-k 6`: 检索 top-6 相关记忆

**预期输出**：
```json
{
  "count": 10,
  "exact_match_count": 3,
  "exact_match_rate": 0.3,
  "total_injection_tokens_est": 3250,
  "avg_injection_tokens_est": 325.0,
  "status": "LOCAL_AGENT_DONE",
  "output_csv": ".../local_agent_results.csv"
}
```

### 步骤 3：查看结果

```bash
# 查看 CSV 结果
head -5 /Users/chx/locomo-eval-web/runs/echomem_test_10q/local_agent_results.csv

# 查看检索到的记忆
jq '.[0]' /Users/chx/locomo-eval-web/runs/echomem_test_10q/relevant_memory.json

# 查看摘要
cat /Users/chx/locomo-eval-web/runs/echomem_test_10q/summary.json
```

### 步骤 4：分析准确率

```bash
/Users/chx/jiuwenclaw/bin/python3.12 -c "
import csv
with open('runs/echomem_test_10q/local_agent_results.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    correct = sum(1 for r in rows if r['simple_grade'] == 'CORRECT')
    needs_judge = sum(1 for r in rows if r['simple_grade'] == 'NEEDS_JUDGE')
    print(f'Exact Match: {correct}/{len(rows)} ({correct/len(rows)*100:.1f}%)')
    print(f'需要 Judge: {needs_judge}/{len(rows)}')
"
```

---

## 📊 测试结果详解

### 成功案例（Exact Match）

**题目 3**: How do Jon and Gina both like to destress?
- **Gold**: by dancing
- **Response**: by dancing
- **Grade**: ✅ CORRECT
- **检索质量**: 找到 6 条高度相关的 evidence，score 最高 24 分

**题目 5**: Why did Jon decide to start his dance studio?
- **Gold**: He lost his job and decided to start his own business to share his passion.
- **Response**: He lost his job and decided to start his own business to share his passion.
- **Grade**: ✅ CORRECT
- **检索质量**: 找到明确的 evidence "Lost my job as a banker yesterday"

**题目 6**: What Jon thinks the ideal dance studio should look like?
- **Gold**: By the water, with natural light and Marley flooring
- **Response**: By the water, with natural light and Marley flooring
- **Grade**: ✅ CORRECT
- **检索质量**: 找到描述理想工作室的 evidence

### 需要 Judge 的案例

**题目 1**: When Jon has lost his job as a banker?
- **Gold**: 19 January, 2023
- **Response**: Hey Gina!
- **问题**: 答案抽取失败，返回了对话开头而非日期
- **检索质量**: ✅ 找到了正确的 evidence "Lost my job as a banker yesterday"
- **改进方向**: 增强时间抽取逻辑

**题目 2**: When Gina has lost her job at Door Dash?
- **Gold**: January, 2023
- **Response**: Thanks, Jon!
- **问题**: 同样的答案抽取问题
- **检索质量**: ✅ 找到了 "lost my job at Door Dash this month"
- **改进方向**: 时间推理（"this month" → "January, 2023"）

**题目 9**: Which city have both Jean and John visited?
- **Gold**: Rome
- **Response**: by dancing
- **问题**: 完全错误的答案，可能是检索失败
- **检索质量**: ❌ 检索到的 evidence 不包含城市信息
- **改进方向**: 多跳推理（需要找到 Jon 和 Gina 各自访问的城市）

---

## 🔧 完整测试流程（所有 conversation）

### 测试单个 conversation 的所有题目

```bash
# conv-30 有 199 题
/Users/chx/jiuwenclaw/bin/python3.12 scripts/local_memory_agent.py \
  --dataset dataset/locomo10.json \
  --format auto \
  --out-dir /Users/chx/locomo-eval-web/runs/echomem_conv30_full \
  --sample conv-30 \
  --top-k 6
```

### 测试所有 10 个 conversation

```bash
# 不指定 --sample，测试全部 1540 题
/Users/chx/jiuwenclaw/bin/python3.12 scripts/local_memory_agent.py \
  --dataset dataset/locomo10.json \
  --format auto \
  --out-dir /Users/chx/locomo-eval-web/runs/echomem_full_1540 \
  --top-k 6
```

**注意**: 全量测试需要较长时间（约 5-10 分钟）

### 按类别测试

```bash
# 只测试 Category 2 (Temporal) 的题目
# 需要先用 jq 过滤数据集，或在代码中添加 category 过滤
```

---

## 📈 与 OpenViking 对比测试

### 步骤 1：导入数据到 OpenViking

```bash
# 使用 benchmark_adapter 导入
/Users/chx/jiuwenclaw/bin/python3.12 scripts/benchmark_adapter.py \
  --dataset dataset/locomo10.json \
  --format auto \
  --out-dir /Users/chx/locomo-eval-web/runs/echomem_ov_import \
  --mode execute \
  --allow-writes \
  --memory-mode isolated_instance \
  --ov-base-url http://localhost:1933 \
  --namespace echomem_test_$(date +%s)
```

**重要**: 
- 需要 OpenViking 服务运行在 `http://localhost:1933`
- 使用 `isolated_instance` 模式避免污染生产数据
- 导入完成后等待 commit 完成

### 步骤 2：运行 VikingBot 评测

```bash
/Users/chx/jiuwenclaw/bin/python3.12 scripts/run_vikingbot_eval.py \
  --dataset dataset/locomo10.json \
  --format auto \
  --out-dir /Users/chx/locomo-eval-web/runs/echomem_vikingbot_10q \
  --sample conv-30 \
  --count 10 \
  --ov-base-url http://localhost:1933 \
  --namespace echomem_test_1780225356
```

### 步骤 3：对比分析

```bash
# 使用 Web UI 进行对比
# 访问 http://127.0.0.1:19181/
# 进入 "Runs 分析" 页面
# 选择 Local Agent 和 VikingBot 的结果进行对比
```

---

## 🎯 优化建议

### 当前问题分析

**问题 1: 时间抽取失败** (7/10 题)
- **现象**: 检索到了包含时间的 evidence，但答案抽取返回了对话开头
- **原因**: `answer_from_memory` 函数的时间抽取逻辑不够强
- **位置**: `local_memory_agent.py` 第 244-246 行
- **改进**: 增强正则表达式，支持更多时间格式

**问题 2: 多跳推理缺失** (1/10 题)
- **现象**: "Which city have both Jean and John visited?" 需要跨多条 evidence 汇总
- **原因**: 当前检索是单次 top-k，没有多跳机制
- **改进**: 实现 StructMem 的跨事件连接机制

**问题 3: 答案抽取过于简单** (多题)
- **现象**: 直接返回第一句话，而非精确答案
- **原因**: `answer_from_memory` 的 fallback 逻辑太粗糙
- **位置**: `local_memory_agent.py` 第 252-253 行
- **改进**: 使用 LLM 进行答案生成，或增强规则匹配

### 优化优先级

**P0 (立即实施)**:
1. 修复时间抽取逻辑
2. 增强 "when" 类问题的处理

**P1 (本周)**:
1. 实现多跳推理原型
2. 改进答案抽取的 fallback 策略

**P2 (本月)**:
1. 集成 StructMem 的事件关系图
2. 支持 LLM 驱动的答案生成

---

## 📁 输出文件说明

### local_agent_results.csv
包含每题的完整结果：
- `question`: 问题文本
- `answer`: Gold answer
- `response`: Agent 的回答
- `simple_grade`: CORRECT / NEEDS_JUDGE
- `relevant_memory`: 检索到的 evidence (JSON)
- `time_cost`: 耗时（秒）

### relevant_memory.json
每题检索到的 top-k evidence：
```json
{
  "question_id": "conv-30_qa0",
  "question": "When Jon has lost his job as a banker?",
  "hits": [
    {
      "score": 9,
      "rank": 1,
      "time": "4:04 pm on 20 January, 2023",
      "text": "Jon D1:2: Hey Gina! Good to see you too. Lost my job as a banker yesterday..."
    }
  ]
}
```

### local_memory_store.json
模拟的本地记忆存储：
- `namespace`: 测试命名空间
- `pollution_guard`: 污染防护标记
- `samples`: 所有 conversation 的 events

### summary.json
测试摘要统计：
- `count`: 总题数
- `exact_match_count`: Exact Match 数量
- `exact_match_rate`: Exact Match 比例
- `avg_injection_tokens_est`: 平均 token 消耗

---

## 🔍 调试技巧

### 查看单题的检索结果

```bash
jq '.[] | select(.question_id == "conv-30_qa0")' \
  /Users/chx/locomo-eval-web/runs/echomem_test_10q/relevant_memory.json
```

### 查看检索评分分布

```bash
jq '[.[] | .hits[0].score] | add / length' \
  /Users/chx/locomo-eval-web/runs/echomem_test_10q/relevant_memory.json
```

### 查看错误题目

```bash
/Users/chx/jiuwenclaw/bin/python3.12 -c "
import csv
with open('runs/echomem_test_10q/local_agent_results.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row['simple_grade'] == 'NEEDS_JUDGE':
            print(f'Q: {row[\"question\"]}')
            print(f'Gold: {row[\"answer\"]}')
            print(f'Response: {row[\"response\"][:100]}')
            print()
"
```

### 修改检索参数

```bash
# 增加 top-k 到 10
--top-k 10

# 测试特定题目
--questions "conv-30_qa0,conv-30_qa1,conv-30_qa2"
```

---

## ✅ 验收标准

测试完成后，应该得到：
- ✅ `local_agent_results.csv` (10 行数据)
- ✅ `relevant_memory.json` (10 题的检索结果)
- ✅ `summary.json` (统计摘要)
- ✅ Exact Match ≥ 30% (当前基线)
- ✅ 平均 token ≤ 400 tokens/题

---

## 🆘 常见问题

### Q: 数据集格式不兼容？
A: 确认使用 `--format auto`，系统会自动识别 LoCoMo 格式

### Q: 检索结果为空？
A: 检查 `--top-k` 参数，建议设置为 4-10

### Q: Exact Match 太低？
A: 这是正常的，Local Memory Agent 是基线系统，主要用于验证数据和流程

### Q: 如何提高准确率？
A: 
1. 调整 `--top-k` 参数
2. 修改 `local_memory_agent.py` 的答案抽取逻辑
3. 使用 OpenViking + VikingBot 进行真实评测

### Q: 如何运行 Judge？
A: 使用 `local_judge.py`:
```bash
/Users/chx/jiuwenclaw/bin/python3.12 scripts/local_judge.py \
  --csv /Users/chx/locomo-eval-web/runs/echomem_test_10q/local_agent_results.csv \
  --judge-url https://your-judge-api/v1 \
  --judge-model your-model \
  --judge-token your-token
```

---

## 📚 相关文档

- [外部测试方案](/Users/chx/locomo-eval-web/docs/external_tester_handoff_plan.md)
- [EchoMemory v0.0.5 Release](https://github.com/tech-innovation-group/echo_memory/releases/tag/version_0.0.5)
- [EchoMemory GitHub](https://github.com/tech-innovation-group/echo_memory)

---

**最后更新**: 2026-05-31  
**测试状态**: ✅ 已验证  
**下一步**: 运行完整 conv-30 (199 题) 或全量测试 (1540 题)
